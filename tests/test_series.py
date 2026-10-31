from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import numpy as np
import pytest

from insights.domain import Event, Issue, IssueStatus
from insights.series import Series, hourly_counts
from insights.store import Store

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
SYNCED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "insights.duckdb") as opened:
        opened.create_schema()
        yield opened


def add_issue(store, application="checkout-service", environment="production"):
    issue = Issue(
        id=uuid4(),
        application=application,
        environment=environment,
        fingerprint="f" * 64,
        exception_type="java.lang.IllegalStateException",
        status=IssueStatus.UNRESOLVED,
        occurrence_count=1,
        first_seen_at=START,
        last_seen_at=START,
    )
    store.upsert_issues([issue], SYNCED_AT)
    return issue


def add_events(store, issue, *occurred_at):
    events = [
        Event(
            id=uuid4(),
            issue_id=issue.id,
            exception_type=issue.exception_type,
            occurred_at=moment,
            received_at=moment,
        )
        for moment in occurred_at
    ]
    store.insert_events(events)


def at(hour, minute=0, second=0, microsecond=0):
    return START + timedelta(hours=hour, minutes=minute, seconds=second, microseconds=microsecond)


def only_series(series_list):
    assert len(series_list) == 1
    return series_list[0]


def test_hours_without_events_are_zero(store):
    issue = add_issue(store)
    add_events(store, issue, at(1, 15), at(1, 40))

    series = only_series(hourly_counts(store, START, END))

    assert series.counts.tolist() == [0, 2, 0, 0]


def test_series_has_one_entry_per_hour_of_the_period(store):
    add_events(store, add_issue(store), at(2))

    series = only_series(hourly_counts(store, START, END))

    assert series.hours == [at(0), at(1), at(2), at(3)]
    assert len(series.counts) == len(series.hours)


def test_counts_are_a_numpy_integer_array(store):
    add_events(store, add_issue(store), at(0))

    series = only_series(hourly_counts(store, START, END))

    assert isinstance(series, Series)
    assert isinstance(series.counts, np.ndarray)
    assert series.counts.dtype == np.int64


def test_period_includes_start_and_excludes_end(store):
    issue = add_issue(store)
    add_events(store, issue, START, END)

    series = only_series(hourly_counts(store, START, END))

    assert series.counts.tolist() == [1, 0, 0, 0]


def test_last_microsecond_before_end_is_counted_in_the_last_hour(store):
    add_events(store, add_issue(store), at(3, 59, 59, 999_999))

    series = only_series(hourly_counts(store, START, END))

    assert series.counts.tolist() == [0, 0, 0, 1]


def test_events_outside_the_period_are_ignored(store):
    issue = add_issue(store)
    add_events(store, issue, START - timedelta(microseconds=1), END + timedelta(hours=1), at(2))

    series = only_series(hourly_counts(store, START, END))

    assert series.counts.tolist() == [0, 0, 1, 0]


def test_events_with_another_offset_are_counted_in_their_utc_hour(store):
    plus_two = timezone(timedelta(hours=2))
    add_events(store, add_issue(store), datetime(2026, 10, 1, 3, 30, tzinfo=plus_two))

    series = only_series(hourly_counts(store, START, END))

    assert series.counts.tolist() == [0, 1, 0, 0]


def test_hours_are_in_utc_when_the_period_has_another_offset(store):
    plus_two = timezone(timedelta(hours=2))
    add_issue(store)

    series = only_series(
        hourly_counts(
            store,
            datetime(2026, 10, 1, 2, 0, tzinfo=plus_two),
            datetime(2026, 10, 1, 4, 0, tzinfo=plus_two),
        )
    )

    assert series.hours == [at(0), at(1)]
    assert all(hour.utcoffset() == timedelta(0) for hour in series.hours)


def test_series_are_grouped_by_application_and_environment(store):
    checkout_production = add_issue(store, "checkout-service", "production")
    checkout_staging = add_issue(store, "checkout-service", "staging")
    auth_production = add_issue(store, "auth-service", "production")
    add_events(store, checkout_production, at(0), at(0))
    add_events(store, checkout_staging, at(1))
    add_events(store, auth_production, at(3))

    series_list = hourly_counts(store, START, END)

    assert [series.key for series in series_list] == [
        ("auth-service", "production"),
        ("checkout-service", "production"),
        ("checkout-service", "staging"),
    ]
    assert [series.counts.tolist() for series in series_list] == [
        [0, 0, 0, 1],
        [2, 0, 0, 0],
        [0, 1, 0, 0],
    ]


def test_issues_of_the_same_application_and_environment_are_added_up(store):
    first = add_issue(store)
    second = add_issue(store)
    add_events(store, first, at(0), at(2))
    add_events(store, second, at(2))

    series = only_series(hourly_counts(store, START, END))

    assert series.key == ("checkout-service", "production")
    assert series.counts.tolist() == [1, 0, 2, 0]


def test_series_per_issue_is_keyed_by_issue_id(store):
    first = add_issue(store)
    second = add_issue(store)
    add_events(store, first, at(0))
    add_events(store, second, at(1), at(1))

    series_by_key = {
        series.key: series.counts.tolist()
        for series in hourly_counts(store, START, END, by=("issue_id",))
    }

    assert series_by_key == {(first.id,): [1, 0, 0, 0], (second.id,): [0, 2, 0, 0]}


def test_series_per_issue_add_up_to_the_total(store):
    issues = [add_issue(store, application) for application in ("a", "b", "b", "c")]
    for position, issue in enumerate(issues):
        add_events(store, issue, *[at(hour, position) for hour in range(position, 4)])

    per_issue = hourly_counts(store, START, END, by=("issue_id",))
    per_application = hourly_counts(store, START, END, by=("application",))

    issue_total = sum(series.counts for series in per_issue)
    application_total = sum(series.counts for series in per_application)
    assert len(per_issue) == 4
    assert len(per_application) == 3
    assert issue_total.tolist() == application_total.tolist() == [1, 2, 3, 4]


def test_issue_without_events_in_the_period_gives_a_zero_series(store):
    quiet = add_issue(store, "quiet-service")
    add_events(store, quiet, END + timedelta(hours=2))

    series = only_series(hourly_counts(store, START, END))

    assert series.key == ("quiet-service", "production")
    assert series.counts.tolist() == [0, 0, 0, 0]


def test_empty_store_gives_no_series(store):
    assert hourly_counts(store, START, END) == []


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (START.replace(tzinfo=None), END, "time zone"),
        (START, END.replace(tzinfo=None), "time zone"),
        (at(0, 30), END, "whole hours"),
        (START, at(3, 0, 1), "whole hours"),
        (START, at(3, 0, 0, 1), "whole hours"),
        (END, START, "before end"),
        (START, START, "before end"),
    ],
)
def test_invalid_period_is_refused(store, start, end, message):
    with pytest.raises(ValueError, match=message):
        hourly_counts(store, start, end)


@pytest.mark.parametrize(
    ("by", "message"),
    [
        ((), "at least one column"),
        (("fingerprint",), "Cannot group by"),
        (("application; DROP TABLE events",), "Cannot group by"),
        (("application", "application"), "twice"),
    ],
)
def test_invalid_key_columns_are_refused(store, by, message):
    with pytest.raises(ValueError, match=message):
        hourly_counts(store, START, END, by=by)
