from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from insights.store import Store

KEY_COLUMNS = {
    "application": "issues.application",
    "environment": "issues.environment",
    "issue_id": "issues.id",
}


@dataclass(frozen=True)
class Series:
    key: tuple
    hours: list[datetime]
    counts: np.ndarray


def hourly_counts(
    store: Store,
    start: datetime,
    end: datetime,
    by: Sequence[str] = ("application", "environment"),
) -> list[Series]:
    check_key_columns(by)
    check_period(start, end)
    rows = store.connection.execute(hourly_counts_query(by), [start, end, start, end]).fetchall()
    return series_from_rows(rows, len(by))


def event_period(store: Store) -> tuple[datetime, datetime] | None:
    first, last = store.connection.execute(
        "SELECT date_trunc('hour', min(occurred_at)), date_trunc('hour', max(occurred_at)) "
        "FROM events"
    ).fetchone()
    if first is None:
        return None
    return first, last + timedelta(hours=1)


def check_key_columns(by: Sequence[str]) -> None:
    if not by:
        raise ValueError("by must name at least one column")
    unknown = [column for column in by if column not in KEY_COLUMNS]
    if unknown:
        raise ValueError(f"Cannot group by {unknown}; allowed columns are {list(KEY_COLUMNS)}")
    if len(set(by)) != len(by):
        raise ValueError(f"by contains a column twice: {list(by)}")


def check_period(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must have a time zone")
    if not is_whole_hour(start) or not is_whole_hour(end):
        raise ValueError("start and end must be whole hours")
    if start >= end:
        raise ValueError("start must be before end")


def is_whole_hour(moment: datetime) -> bool:
    return moment.minute == 0 and moment.second == 0 and moment.microsecond == 0


def hourly_counts_query(by: Sequence[str]) -> str:
    key_select = ", ".join(f"{KEY_COLUMNS[column]} AS {column}" for column in by)
    key_names = ", ".join(by)
    return (
        "WITH hours AS ("
        "SELECT unnest(range(?::TIMESTAMPTZ, ?::TIMESTAMPTZ, INTERVAL 1 HOUR)) AS hour), "
        f"series_keys AS (SELECT DISTINCT {key_select} FROM issues), "
        f"hour_counts AS (SELECT {key_select}, "
        "date_trunc('hour', events.occurred_at) AS hour, count(*) AS event_count "
        "FROM events JOIN issues ON events.issue_id = issues.id "
        "WHERE events.occurred_at >= ? AND events.occurred_at < ? "
        "GROUP BY ALL) "
        f"SELECT {key_names}, hour, coalesce(event_count, 0) "
        "FROM series_keys CROSS JOIN hours "
        f"LEFT JOIN hour_counts USING ({key_names}, hour) "
        f"ORDER BY {key_names}, hour"
    )


def series_from_rows(rows: Sequence[tuple], key_size: int) -> list[Series]:
    grouped: dict[tuple, tuple[list[datetime], list[int]]] = {}
    for row in rows:
        key = tuple(row[:key_size])
        hour, count = row[key_size], row[key_size + 1]
        hours, counts = grouped.setdefault(key, ([], []))
        hours.append(hour)
        counts.append(count)
    return [
        Series(key=key, hours=hours, counts=np.array(counts, dtype=np.int64))
        for key, (hours, counts) in grouped.items()
    ]
