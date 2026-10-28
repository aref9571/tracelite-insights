import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, SecretStr

from insights.domain import Event, Issue, IssueStats, Page

ISSUES_PATH = "/api/v1/issues"
PAGE_SIZE = 100
RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0, 4.0)


class TraceLiteError(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        message = f"{status} {title}"
        super().__init__(f"{message}: {detail}" if detail else message)


class TraceLiteConnectionError(Exception):
    pass


class TraceLiteClient:
    def __init__(
        self,
        base_url: str,
        management_key: SecretStr,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.management_key = management_key
        self.client = client
        self.sleep = sleep

    def iter_issues(
        self, environment: str | None = None, last_seen_from: datetime | None = None
    ) -> Iterator[Issue]:
        params: dict[str, Any] = {"sort": "FIRST_SEEN", "direction": "ASC"}
        if environment is not None:
            params["environment"] = environment
        if last_seen_from is not None:
            if last_seen_from.tzinfo is None:
                raise ValueError("last_seen_from must have a time zone")
            params["lastSeenFrom"] = last_seen_from.isoformat()
        return self.iter_pages(ISSUES_PATH, params, Issue)

    def iter_events(self, issue_id: UUID) -> Iterator[Event]:
        for event in self.iter_pages(f"{ISSUES_PATH}/{issue_id}/events", {}, Event):
            yield event.model_copy(update={"issue_id": issue_id})

    def get_stats(self, issue_id: UUID) -> IssueStats:
        return IssueStats.model_validate(self.get(f"{ISSUES_PATH}/{issue_id}/stats", {}))

    def iter_pages[T: BaseModel](
        self, path: str, params: dict[str, Any], item_type: type[T]
    ) -> Iterator[T]:
        page_number = 0
        while True:
            body = self.get(path, params | {"page": page_number, "size": PAGE_SIZE})
            page = Page[item_type].model_validate(body)
            yield from page.items
            if page_number >= page.total_pages - 1:
                return
            page_number += 1

    def get(self, path: str, params: dict[str, Any]) -> Any:
        url = self.base_url + path
        for retry_delay in RETRY_DELAYS_SECONDS:
            response = self.request_or_none(url, params)
            if response is not None and response.status_code != 503:
                return self.body_or_error(response)
            self.sleep(retry_delay)
        try:
            response = self.request(url, params)
        except httpx.TransportError as error:
            raise TraceLiteConnectionError(
                f"Could not reach TraceLite at {self.base_url}: {error}"
            ) from error
        return self.body_or_error(response)

    def request_or_none(self, url: str, params: dict[str, Any]) -> httpx.Response | None:
        try:
            return self.request(url, params)
        except httpx.TransportError:
            return None

    def request(self, url: str, params: dict[str, Any]) -> httpx.Response:
        return self.client.get(
            url,
            params=params,
            headers={"X-API-Key": self.management_key.get_secret_value()},
        )

    def body_or_error(self, response: httpx.Response) -> Any:
        if response.status_code == 200:
            return response.json()
        raise problem_error(response)


def problem_error(response: httpx.Response) -> TraceLiteError:
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    title = body.get("title")
    detail = body.get("detail")
    return TraceLiteError(
        response.status_code,
        title if isinstance(title, str) else response.reason_phrase,
        detail if isinstance(detail, str) else None,
    )
