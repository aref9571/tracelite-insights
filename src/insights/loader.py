import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from insights.domain import Event, Issue, IssueStatus
from insights.fingerprint import fingerprint
from insights.simulator.truth import EVENT_FAILURES_FILE, EVENTS_FILE
from insights.store import Store

OFFLINE_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/aref9571/tracelite-insights/offline")


@dataclass(frozen=True)
class LoadReport:
    issues: int
    events: int
    new_events: int


def load_simulation(store: Store, sim_dir: Path, loaded_at: datetime) -> LoadReport:
    payloads = read_jsonl(sim_dir / EVENTS_FILE)
    failure_ids = read_failure_ids(sim_dir / EVENT_FAILURES_FILE, len(payloads))
    issues: dict[UUID, Issue] = {}
    events: list[Event] = []
    for line_number, payload in enumerate(payloads, start=1):
        try:
            event, issue = parse_occurrence(payload, issues)
        except (KeyError, ValidationError) as error:
            raise ValueError(f"{EVENTS_FILE} line {line_number}: {error!r}") from error
        issues[issue.id] = issue
        events.append(event)
    store.create_schema()
    store.upsert_issues(list(issues.values()), loaded_at)
    new_events = store.insert_events(events)
    store.insert_event_failures(
        {event.id: failure_id for event, failure_id in zip(events, failure_ids, strict=True)}
    )
    return LoadReport(issues=len(issues), events=len(events), new_events=new_events)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name} line {line_number}: {error}") from error
    return rows


def read_failure_ids(path: Path, event_count: int) -> list[str]:
    rows = read_jsonl(path)
    if len(rows) != event_count:
        raise ValueError(f"{path.name} has {len(rows)} rows but {EVENTS_FILE} has {event_count}")
    for index, row in enumerate(rows):
        if row.get("event_index") != index:
            raise ValueError(f"{path.name} line {index + 1}: expected event_index {index}")
    return [row["failure_id"] for row in rows]


def parse_occurrence(payload: dict[str, Any], issues: dict[UUID, Issue]) -> tuple[Event, Issue]:
    issue_fingerprint = fingerprint(payload["exceptionType"], payload.get("stackTrace"))
    issue_id = offline_issue_id(payload["application"], payload["environment"], issue_fingerprint)
    event = event_from_payload(payload, issue_id)
    existing = issues.get(issue_id)
    if existing is None:
        return event, new_issue(payload, issue_id, issue_fingerprint, event)
    return event, count_occurrence(existing, event)


def offline_issue_id(application: str, environment: str, issue_fingerprint: str) -> UUID:
    return uuid5(OFFLINE_NAMESPACE, f"{application}\n{environment}\n{issue_fingerprint}")


def offline_event_id(payload: dict[str, Any]) -> UUID:
    return uuid5(OFFLINE_NAMESPACE, json.dumps(payload, sort_keys=True))


def event_from_payload(payload: dict[str, Any], issue_id: UUID) -> Event:
    return Event(
        id=offline_event_id(payload),
        issue_id=issue_id,
        exception_type=payload["exceptionType"],
        message=payload.get("message"),
        stack_trace=payload.get("stackTrace"),
        occurred_at=payload["occurredAt"],
        received_at=payload["occurredAt"],
        correlation_id=payload.get("correlationId"),
        context=payload.get("context"),
    )


def new_issue(
    payload: dict[str, Any], issue_id: UUID, issue_fingerprint: str, event: Event
) -> Issue:
    return Issue(
        id=issue_id,
        application=payload["application"],
        environment=payload["environment"],
        fingerprint=issue_fingerprint,
        exception_type=event.exception_type,
        message=event.message,
        status=IssueStatus.UNRESOLVED,
        occurrence_count=1,
        first_seen_at=event.received_at,
        last_seen_at=event.received_at,
    )


def count_occurrence(issue: Issue, event: Event) -> Issue:
    return issue.model_copy(
        update={
            "occurrence_count": issue.occurrence_count + 1,
            "first_seen_at": min(issue.first_seen_at, event.received_at),
            "last_seen_at": max(issue.last_seen_at, event.received_at),
        }
    )
