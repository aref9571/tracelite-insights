from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from insights.domain import Event, Issue, IssueStatus
from insights.store import Store
from insights.sync import sync

START = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
FIRST_SYNC = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
SECOND_SYNC = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
PAGE_SIZE = 2


class FakeTraceLite:
    def __init__(self):
        self.issues = {}
        self.events = {}
        self.event_reads = []
        self.events_handed_out = 0
        self.arrivals_after_first_page = []

    def add_issue(self, issue_id=None):
        issue_id = issue_id or uuid4()
        self.issues[issue_id] = Issue(
            id=issue_id,
            application="checkout-service",
            environment="production",
            fingerprint="f" * 64,
            exception_type="java.lang.IllegalStateException",
            message="Cart 42 is empty",
            status=IssueStatus.UNRESOLVED,
            occurrence_count=0,
            first_seen_at=START,
            last_seen_at=START,
        )
        self.events[issue_id] = []
        return issue_id

    def receive(self, issue_id, received_at):
        event = Event(
            id=uuid4(),
            issue_id=issue_id,
            exception_type="java.lang.IllegalStateException",
            message="Cart 42 is empty",
            occurred_at=received_at,
            received_at=received_at,
        )
        self.events[issue_id].insert(0, event)
        issue = self.issues[issue_id]
        self.issues[issue_id] = issue.model_copy(
            update={
                "occurrence_count": issue.occurrence_count + 1,
                "last_seen_at": max(issue.last_seen_at, received_at),
            }
        )
        return event

    def iter_issues(self):
        yield from list(self.issues.values())

    def iter_events(self, issue_id):
        self.event_reads.append(issue_id)
        page_number = 0
        while True:
            start = page_number * PAGE_SIZE
            page = self.events[issue_id][start : start + PAGE_SIZE]
            for event in page:
                self.events_handed_out += 1
                yield event
            if page_number == 0:
                for received_at in self.arrivals_after_first_page:
                    self.receive(issue_id, received_at)
            if start + PAGE_SIZE >= len(self.events[issue_id]):
                return
            page_number += 1


def receive_hourly(tracelite, issue_id, count, first):
    return [tracelite.receive(issue_id, first + timedelta(hours=hour)) for hour in range(count)]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "insights.duckdb") as opened:
        yield opened


@pytest.fixture
def tracelite():
    return FakeTraceLite()


def stored_event_ids(store):
    rows = store.connection.execute("SELECT id FROM events").fetchall()
    return sorted(row[0] for row in rows)


def stored_issue(store, issue_id):
    return store.connection.execute(
        "SELECT status, occurrence_count, last_seen_at, synced_at FROM issues WHERE id = ?",
        [issue_id],
    ).fetchone()


def test_first_sync_loads_every_issue_and_event(store, tracelite):
    first = tracelite.add_issue()
    second = tracelite.add_issue()
    events = receive_hourly(tracelite, first, 5, START) + receive_hourly(
        tracelite, second, 3, START
    )

    report = sync(tracelite, store, FIRST_SYNC)

    assert (report.issues_seen, report.issues_read, report.new_events) == (2, 2, 8)
    assert stored_event_ids(store) == sorted(event.id for event in events)
    assert stored_issue(store, first)[1] == 5


def test_second_sync_loads_exactly_the_new_events(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 5, START)
    sync(tracelite, store, FIRST_SYNC)
    new_events = receive_hourly(tracelite, issue_id, 3, START + timedelta(hours=10))

    report = sync(tracelite, store, SECOND_SYNC)

    assert report.new_events == 3
    assert len(stored_event_ids(store)) == 8
    assert {event.id for event in new_events} <= set(stored_event_ids(store))


def test_second_sync_stops_at_the_first_event_already_stored(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 9, START)
    sync(tracelite, store, FIRST_SYNC)
    receive_hourly(tracelite, issue_id, 3, START + timedelta(hours=10))
    tracelite.events_handed_out = 0

    sync(tracelite, store, SECOND_SYNC)

    assert tracelite.events_handed_out == 4


def test_unchanged_issue_is_not_read_again(store, tracelite):
    quiet = tracelite.add_issue()
    busy = tracelite.add_issue()
    receive_hourly(tracelite, quiet, 2, START)
    receive_hourly(tracelite, busy, 2, START)
    sync(tracelite, store, FIRST_SYNC)
    tracelite.event_reads.clear()
    receive_hourly(tracelite, busy, 1, START + timedelta(hours=5))

    report = sync(tracelite, store, SECOND_SYNC)

    assert tracelite.event_reads == [busy]
    assert (report.issues_seen, report.issues_read, report.new_events) == (2, 1, 1)


def test_unchanged_issue_still_gets_its_new_status_and_sync_time(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 2, START)
    sync(tracelite, store, FIRST_SYNC)
    issue = tracelite.issues[issue_id]
    tracelite.issues[issue_id] = issue.model_copy(
        update={"status": IssueStatus.RESOLVED, "resolved_at": SECOND_SYNC}
    )

    sync(tracelite, store, SECOND_SYNC)

    status, _, _, synced_at = stored_issue(store, issue_id)
    assert status == "RESOLVED"
    assert synced_at == SECOND_SYNC


def test_sync_without_changes_reads_no_events(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 3, START)
    sync(tracelite, store, FIRST_SYNC)
    tracelite.event_reads.clear()

    report = sync(tracelite, store, SECOND_SYNC)

    assert tracelite.event_reads == []
    assert (report.issues_read, report.new_events) == (0, 0)


def test_events_arriving_during_the_read_do_not_create_duplicates(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 5, START)
    tracelite.arrivals_after_first_page = [START + timedelta(hours=6)]

    report = sync(tracelite, store, FIRST_SYNC)

    assert tracelite.events_handed_out == 6
    assert report.new_events == 5
    assert len(stored_event_ids(store)) == 5


def test_event_that_arrived_during_the_read_is_loaded_by_the_next_sync(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 5, START)
    tracelite.arrivals_after_first_page = [START + timedelta(hours=6)]
    sync(tracelite, store, FIRST_SYNC)
    tracelite.arrivals_after_first_page = []

    report = sync(tracelite, store, SECOND_SYNC)

    assert report.new_events == 1
    assert len(stored_event_ids(store)) == 6


def test_unstored_event_with_the_newest_stored_time_is_still_loaded(store, tracelite):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 3, START)
    sync(tracelite, store, FIRST_SYNC)
    newest_stored = START + timedelta(hours=2)
    same_time = tracelite.receive(issue_id, newest_stored)
    tracelite.receive(issue_id, START + timedelta(hours=4))

    report = sync(tracelite, store, SECOND_SYNC)

    assert report.new_events == 2
    assert same_time.id in stored_event_ids(store)


def test_sync_of_an_empty_tracelite_creates_the_schema_and_reports_zeros(store, tracelite):
    report = sync(tracelite, store, FIRST_SYNC)

    assert (report.issues_seen, report.issues_read, report.new_events) == (0, 0, 0)
    assert report.duration_seconds >= 0
    assert stored_event_ids(store) == []


def test_only_events_not_newer_than_the_stored_ones_are_looked_up(store, tracelite, monkeypatch):
    issue_id = tracelite.add_issue()
    receive_hourly(tracelite, issue_id, 4, START)
    sync(tracelite, store, FIRST_SYNC)
    receive_hourly(tracelite, issue_id, 3, START + timedelta(hours=10))
    looked_up = []
    has_event = store.has_event
    monkeypatch.setattr(
        store, "has_event", lambda event_id: looked_up.append(event_id) or has_event(event_id)
    )

    sync(tracelite, store, SECOND_SYNC)

    assert looked_up == [tracelite.events[issue_id][3].id]
