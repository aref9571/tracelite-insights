import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from insights.domain import Event, Issue
from insights.store import Store


class IssueReader(Protocol):
    def iter_issues(self) -> Iterator[Issue]: ...

    def iter_events(self, issue_id: UUID) -> Iterator[Event]: ...


@dataclass(frozen=True)
class SyncReport:
    issues_seen: int
    issues_read: int
    new_events: int
    duration_seconds: float


def sync(client: IssueReader, store: Store, synced_at: datetime) -> SyncReport:
    started = time.monotonic()
    store.create_schema()
    stored_last_seen = store.last_seen_by_issue()
    issues_seen = 0
    issues_read = 0
    new_events = 0
    for issue in client.iter_issues():
        issues_seen += 1
        if has_changed(issue, stored_last_seen.get(issue.id)):
            events = read_new_events(client.iter_events(issue.id), store, issue.id)
            new_events += store.insert_events(events)
            issues_read += 1
        store.upsert_issues([issue], synced_at)
    return SyncReport(
        issues_seen=issues_seen,
        issues_read=issues_read,
        new_events=new_events,
        duration_seconds=time.monotonic() - started,
    )


def has_changed(issue: Issue, stored_last_seen: datetime | None) -> bool:
    return stored_last_seen is None or issue.last_seen_at > stored_last_seen


def read_new_events(events: Iterable[Event], store: Store, issue_id: UUID) -> list[Event]:
    newest_stored = store.newest_received_at(issue_id)
    new_events: dict[UUID, Event] = {}
    for event in events:
        if is_known(event, newest_stored, store):
            break
        new_events.setdefault(event.id, event)
    return list(new_events.values())


def is_known(event: Event, newest_stored: datetime | None, store: Store) -> bool:
    if newest_stored is None or event.received_at > newest_stored:
        return False
    return store.has_event(event.id)
