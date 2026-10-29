import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import duckdb
import pytest

from insights import store as store_module
from insights.domain import Event, Issue, IssueStatus
from insights.store import ROWS_PER_INSERT, Store

FIRST_SEEN = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
SYNCED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def make_issue(**changes):
    values = {
        "id": uuid4(),
        "application": "checkout-service",
        "environment": "production",
        "fingerprint": "f" * 64,
        "exception_type": "java.lang.IllegalStateException",
        "message": "Cart 42 is empty",
        "status": IssueStatus.UNRESOLVED,
        "occurrence_count": 1,
        "first_seen_at": FIRST_SEEN,
        "last_seen_at": FIRST_SEEN,
    }
    return Issue(**(values | changes))


def make_event(issue_id, **changes):
    values = {
        "id": uuid4(),
        "issue_id": issue_id,
        "exception_type": "java.lang.IllegalStateException",
        "message": "Cart 42 is empty",
        "stack_trace": "java.lang.IllegalStateException: Cart 42 is empty",
        "occurred_at": FIRST_SEEN,
        "received_at": FIRST_SEEN,
        "correlation_id": "req-1",
        "context": None,
    }
    return Event(**(values | changes))


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "insights.duckdb") as opened:
        opened.create_schema()
        yield opened


def fetch_all(store, sql, parameters=()):
    return store.connection.execute(sql, list(parameters)).fetchall()


def count_rows(store, table):
    return fetch_all(store, f"SELECT count(*) FROM {table}")[0][0]


def test_create_schema_creates_tables_and_indexes(store):
    tables = fetch_all(store, "SELECT table_name FROM duckdb_tables() ORDER BY table_name")
    indexes = fetch_all(store, "SELECT index_name FROM duckdb_indexes() ORDER BY index_name")

    assert tables == [("events",), ("issues",), ("sim_event_failures",)]
    assert indexes == [("events_issue_id_idx",), ("events_occurred_at_idx",)]


def test_create_schema_twice_keeps_existing_rows(store):
    issue = make_issue()
    store.upsert_issues([issue], SYNCED_AT)

    store.create_schema()

    assert count_rows(store, "issues") == 1


def test_data_survives_reopening_the_database(tmp_path):
    path = tmp_path / "insights.duckdb"
    issue = make_issue()
    with Store(path) as first:
        first.create_schema()
        first.upsert_issues([issue], SYNCED_AT)
        first.insert_events([make_event(issue.id)])

    with Store(path) as second:
        assert count_rows(second, "issues") == 1
        assert count_rows(second, "events") == 1


def test_upsert_issues_inserts_all_columns(store):
    resolved_at = FIRST_SEEN + timedelta(days=1)
    issue = make_issue(status=IssueStatus.RESOLVED, occurrence_count=3, resolved_at=resolved_at)

    written = store.upsert_issues([issue], SYNCED_AT)

    assert written == 1
    assert fetch_all(store, "SELECT * FROM issues") == [
        (
            issue.id,
            "checkout-service",
            "production",
            "f" * 64,
            "java.lang.IllegalStateException",
            "Cart 42 is empty",
            "RESOLVED",
            3,
            FIRST_SEEN,
            FIRST_SEEN,
            resolved_at,
            SYNCED_AT,
        )
    ]


def test_upsert_issues_updates_an_existing_issue(store):
    issue = make_issue(occurrence_count=1)
    store.upsert_issues([issue], SYNCED_AT)
    newer = issue.model_copy(
        update={"occurrence_count": 5, "last_seen_at": FIRST_SEEN + timedelta(hours=3)}
    )
    later_sync = SYNCED_AT + timedelta(hours=1)

    store.upsert_issues([newer], later_sync)

    assert fetch_all(store, "SELECT occurrence_count, last_seen_at, synced_at FROM issues") == [
        (5, FIRST_SEEN + timedelta(hours=3), later_sync)
    ]


def test_upsert_issues_rejects_naive_synced_at(store):
    with pytest.raises(ValueError, match="synced_at must have a time zone"):
        store.upsert_issues([make_issue()], datetime(2026, 10, 20, 12, 0))


def test_upsert_issues_with_no_issues_writes_nothing(store):
    assert store.upsert_issues([], SYNCED_AT) == 0
    assert count_rows(store, "issues") == 0


def test_failed_upsert_rolls_back_every_row_of_the_call(store, monkeypatch):
    monkeypatch.setattr(store_module, "ROWS_PER_INSERT", 2)
    broken = make_issue().model_copy(update={"application": None})
    issues = [make_issue(), make_issue(), make_issue(), broken]

    with pytest.raises(duckdb.Error):
        store.upsert_issues(issues, SYNCED_AT)

    assert count_rows(store, "issues") == 0


def test_insert_events_returns_number_of_new_events(store):
    issue_id = uuid4()
    events = [make_event(issue_id), make_event(issue_id)]

    assert store.insert_events(events) == 2
    assert count_rows(store, "events") == 2


def test_insert_events_ignores_events_already_stored(store):
    issue_id = uuid4()
    stored = make_event(issue_id)
    store.insert_events([stored])

    written = store.insert_events([stored, make_event(issue_id)])

    assert written == 1
    assert count_rows(store, "events") == 2


def test_insert_events_ignores_duplicates_within_one_call(store):
    event = make_event(uuid4())

    assert store.insert_events([event, event]) == 1


def test_insert_events_keeps_the_first_stored_version(store):
    event = make_event(uuid4(), message="first")
    store.insert_events([event])

    store.insert_events([event.model_copy(update={"message": "second"})])

    assert fetch_all(store, "SELECT message FROM events") == [("first",)]


def test_insert_events_handles_more_rows_than_one_statement(store):
    issue_id = uuid4()
    events = [make_event(issue_id) for _ in range(2 * ROWS_PER_INSERT + 1)]

    assert store.insert_events(events) == len(events)


def test_insert_events_rejects_event_without_issue_id(store):
    event = make_event(None)

    with pytest.raises(ValueError, match="has no issue_id"):
        store.insert_events([event])
    assert count_rows(store, "events") == 0


def test_timestamps_round_trip_as_the_same_instant_in_utc(store):
    occurred_at = datetime(2026, 10, 1, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    store.insert_events([make_event(uuid4(), occurred_at=occurred_at)])

    [(stored,)] = fetch_all(store, "SELECT occurred_at FROM events")

    assert stored == occurred_at
    assert stored.utcoffset() == timedelta(0)
    assert stored.hour == 8


def test_context_is_stored_as_json_and_missing_context_as_null(store):
    issue_id = uuid4()
    with_context = make_event(issue_id, context={"userId": "u-1", "attempt": 2})
    without_context = make_event(issue_id, context=None)
    store.insert_events([with_context, without_context])

    rows = dict(fetch_all(store, "SELECT id, context FROM events"))

    assert json.loads(rows[with_context.id]) == {"userId": "u-1", "attempt": 2}
    assert rows[without_context.id] is None


def test_newest_received_at_returns_latest_time_of_that_issue(store):
    issue_id = uuid4()
    newest = FIRST_SEEN + timedelta(hours=2)
    store.insert_events(
        [
            make_event(issue_id, received_at=FIRST_SEEN),
            make_event(issue_id, received_at=newest),
            make_event(uuid4(), received_at=newest + timedelta(hours=1)),
        ]
    )

    assert store.newest_received_at(issue_id) == newest


def test_newest_received_at_is_none_for_issue_without_events(store):
    assert store.newest_received_at(UUID(int=0)) is None


def test_insert_event_failures_stores_each_event_once(store):
    event_id = uuid4()

    assert store.insert_event_failures({event_id: "checkout-db-timeout"}) == 1
    assert store.insert_event_failures({event_id: "checkout-db-timeout"}) == 0
    assert fetch_all(store, "SELECT * FROM sim_event_failures") == [
        (event_id, "checkout-db-timeout")
    ]


def test_has_event_tells_stored_events_from_unknown_ones(store):
    event = make_event(uuid4())
    store.insert_events([event])

    assert store.has_event(event.id) is True
    assert store.has_event(uuid4()) is False


def test_last_seen_by_issue_maps_every_stored_issue_to_its_last_seen_time(store):
    later = FIRST_SEEN + timedelta(days=1)
    first = make_issue()
    second = make_issue(last_seen_at=later)
    store.upsert_issues([first, second], SYNCED_AT)

    assert store.last_seen_by_issue() == {first.id: FIRST_SEEN, second.id: later}


def test_last_seen_by_issue_is_empty_without_issues(store):
    assert store.last_seen_by_issue() == {}
