import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
from pydantic import SecretStr

from insights.simulator.traffic import format_timestamp

EVENTS_PATH = "/api/v1/events"
SENT_FILE = "sent.jsonl"
RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0, 4.0)
NEWEST_EVENT_AGE = timedelta(minutes=1)
OLDEST_EVENT_AGE = timedelta(days=29)
REJECTED_STATUS_CODES = {400, 413}
AUTHENTICATION_STATUS_CODES = {401, 403}


class SenderError(Exception):
    pass


class SendStatus(StrEnum):
    SENT = "sent"
    REJECTED = "rejected"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SendOutcome:
    event_index: int
    status: SendStatus
    occurred_at: str
    issue_id: str | None = None
    reason: str | None = None

    def row(self) -> dict[str, Any]:
        return asdict(self)


class TraceLiteSender:
    def __init__(
        self,
        base_url: str,
        ingestion_keys: dict[str, SecretStr],
        client: httpx.Client,
        max_requests_per_second: float = 50.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_requests_per_second <= 0:
            raise ValueError("max_requests_per_second must be greater than zero")
        self.events_url = base_url.rstrip("/") + EVENTS_PATH
        self.ingestion_keys = ingestion_keys
        self.client = client
        self.min_interval_seconds = 1 / max_requests_per_second
        self.sleep = sleep
        self.clock = clock
        self.next_request_at = 0.0

    def require_keys(self, applications: Iterable[str]) -> None:
        missing = sorted(set(applications) - self.ingestion_keys.keys())
        if missing:
            raise SenderError(f"No ingestion key configured for: {', '.join(missing)}")

    def send(self, event_index: int, payload: dict[str, Any]) -> SendOutcome:
        key = self.ingestion_keys[payload["application"]]
        for retry_delay in RETRY_DELAYS_SECONDS:
            response = self.post_or_none(payload, key)
            if response is not None and response.status_code != 503:
                return self.outcome(event_index, payload, response)
            self.sleep(retry_delay)
        try:
            response = self.post(payload, key)
        except httpx.TransportError as error:
            raise SenderError(f"Could not reach TraceLite at {self.events_url}: {error}") from error
        return self.outcome(event_index, payload, response)

    def post_or_none(self, payload: dict[str, Any], key: SecretStr) -> httpx.Response | None:
        try:
            return self.post(payload, key)
        except httpx.TransportError:
            return None

    def post(self, payload: dict[str, Any], key: SecretStr) -> httpx.Response:
        self.wait_for_rate_limit()
        return self.client.post(
            self.events_url,
            json=payload,
            headers={"X-API-Key": key.get_secret_value()},
        )

    def wait_for_rate_limit(self) -> None:
        now = self.clock()
        if now < self.next_request_at:
            self.sleep(self.next_request_at - now)
            now = self.next_request_at
        self.next_request_at = now + self.min_interval_seconds

    def outcome(
        self, event_index: int, payload: dict[str, Any], response: httpx.Response
    ) -> SendOutcome:
        status_code = response.status_code
        occurred_at = payload["occurredAt"]
        if status_code == 201:
            issue_id = response.json()["issueId"]
            return SendOutcome(event_index, SendStatus.SENT, occurred_at, issue_id=issue_id)
        title = problem_title(response)
        if status_code in AUTHENTICATION_STATUS_CODES:
            raise SenderError(
                f"TraceLite refused the ingestion key for {payload['application']}: "
                f"{status_code} {title}"
            )
        if status_code in REJECTED_STATUS_CODES:
            return SendOutcome(event_index, SendStatus.REJECTED, occurred_at, reason=title)
        return SendOutcome(
            event_index, SendStatus.FAILED, occurred_at, reason=f"{status_code} {title}"
        )


def problem_title(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("title"), str):
        return body["title"]
    return response.reason_phrase


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def shift_to_now(payloads: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    if not payloads:
        return []
    occurred = [parse_timestamp(payload["occurredAt"]) for payload in payloads]
    newest_target = (now - NEWEST_EVENT_AGE).replace(microsecond=0)
    offset = newest_target - max(occurred)
    return [
        payload | {"occurredAt": format_timestamp(moment + offset)}
        for payload, moment in zip(payloads, occurred, strict=True)
    ]


def send_events(
    sender: TraceLiteSender, payloads: list[dict[str, Any]], now: datetime
) -> list[SendOutcome]:
    sender.require_keys(payload["application"] for payload in payloads)
    oldest_allowed = now - OLDEST_EVENT_AGE
    outcomes = []
    for index, payload in enumerate(shift_to_now(payloads, now)):
        if parse_timestamp(payload["occurredAt"]) < oldest_allowed:
            outcomes.append(SendOutcome(index, SendStatus.SKIPPED, payload["occurredAt"]))
        else:
            outcomes.append(sender.send(index, payload))
    return outcomes


def count_by_status(outcomes: list[SendOutcome]) -> dict[SendStatus, int]:
    counts = Counter(outcome.status for outcome in outcomes)
    return {status: counts[status] for status in SendStatus}
