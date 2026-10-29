import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Self
from uuid import UUID

import duckdb

from insights.domain import Event, Issue

ROWS_PER_INSERT = 500

CREATE_ISSUES_TABLE = (
    "CREATE TABLE IF NOT EXISTS issues ("
    "id UUID PRIMARY KEY, "
    "application VARCHAR NOT NULL, "
    "environment VARCHAR NOT NULL, "
    "fingerprint VARCHAR NOT NULL, "
    "exception_type VARCHAR NOT NULL, "
    "message VARCHAR, "
    "status VARCHAR NOT NULL, "
    "occurrence_count BIGINT NOT NULL, "
    "first_seen_at TIMESTAMPTZ NOT NULL, "
    "last_seen_at TIMESTAMPTZ NOT NULL, "
    "resolved_at TIMESTAMPTZ, "
    "synced_at TIMESTAMPTZ NOT NULL)"
)

CREATE_EVENTS_TABLE = (
    "CREATE TABLE IF NOT EXISTS events ("
    "id UUID PRIMARY KEY, "
    "issue_id UUID NOT NULL, "
    "exception_type VARCHAR NOT NULL, "
    "message VARCHAR, "
    "stack_trace VARCHAR, "
    "occurred_at TIMESTAMPTZ NOT NULL, "
    "received_at TIMESTAMPTZ NOT NULL, "
    "correlation_id VARCHAR, "
    "context JSON)"
)

CREATE_SIM_EVENT_FAILURES_TABLE = (
    "CREATE TABLE IF NOT EXISTS sim_event_failures ("
    "event_id UUID PRIMARY KEY, "
    "failure_id VARCHAR NOT NULL)"
)

SCHEMA_STATEMENTS = (
    CREATE_ISSUES_TABLE,
    CREATE_EVENTS_TABLE,
    CREATE_SIM_EVENT_FAILURES_TABLE,
    "CREATE INDEX IF NOT EXISTS events_issue_id_idx ON events (issue_id)",
    "CREATE INDEX IF NOT EXISTS events_occurred_at_idx ON events (occurred_at)",
)

ISSUE_COLUMNS = (
    "id",
    "application",
    "environment",
    "fingerprint",
    "exception_type",
    "message",
    "status",
    "occurrence_count",
    "first_seen_at",
    "last_seen_at",
    "resolved_at",
    "synced_at",
)

EVENT_COLUMNS = (
    "id",
    "issue_id",
    "exception_type",
    "message",
    "stack_trace",
    "occurred_at",
    "received_at",
    "correlation_id",
    "context",
)

SIM_EVENT_FAILURE_COLUMNS = ("event_id", "failure_id")

ISSUE_UPSERT_ACTION = "ON CONFLICT (id) DO UPDATE SET " + ", ".join(
    f"{column} = excluded.{column}" for column in ISSUE_COLUMNS if column != "id"
)


class Store:
    def __init__(self, path: Path | str) -> None:
        self.connection = duckdb.connect(str(path))
        self.connection.execute("SET TimeZone = 'UTC'")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def create_schema(self) -> None:
        for statement in SCHEMA_STATEMENTS:
            self.connection.execute(statement)

    def upsert_issues(self, issues: Sequence[Issue], synced_at: datetime) -> int:
        if synced_at.tzinfo is None:
            raise ValueError("synced_at must have a time zone")
        rows = [issue_row(issue, synced_at) for issue in issues]
        return self.insert_rows("issues", ISSUE_COLUMNS, rows, ISSUE_UPSERT_ACTION)

    def insert_events(self, events: Sequence[Event]) -> int:
        rows = [event_row(event) for event in events]
        return self.insert_rows("events", EVENT_COLUMNS, rows, "ON CONFLICT DO NOTHING")

    def insert_event_failures(self, failure_ids: dict[UUID, str]) -> int:
        rows = [(event_id, failure_id) for event_id, failure_id in failure_ids.items()]
        return self.insert_rows(
            "sim_event_failures", SIM_EVENT_FAILURE_COLUMNS, rows, "ON CONFLICT DO NOTHING"
        )

    def newest_received_at(self, issue_id: UUID) -> datetime | None:
        row = self.connection.execute(
            "SELECT max(received_at) FROM events WHERE issue_id = ?", [issue_id]
        ).fetchone()
        return row[0] if row else None

    def has_event(self, event_id: UUID) -> bool:
        row = self.connection.execute("SELECT 1 FROM events WHERE id = ?", [event_id]).fetchone()
        return row is not None

    def last_seen_by_issue(self) -> dict[UUID, datetime]:
        rows = self.connection.execute("SELECT id, last_seen_at FROM issues").fetchall()
        return dict(rows)

    def insert_rows(
        self,
        table: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
        conflict_action: str,
    ) -> int:
        written = 0
        self.connection.begin()
        try:
            for start in range(0, len(rows), ROWS_PER_INSERT):
                chunk = rows[start : start + ROWS_PER_INSERT]
                sql = insert_statement(table, columns, len(chunk), conflict_action)
                parameters = [value for row in chunk for value in row]
                written += self.connection.execute(sql, parameters).fetchone()[0]
        except Exception:
            self.connection.rollback()
            raise
        self.connection.commit()
        return written


def insert_statement(
    table: str, columns: Sequence[str], row_count: int, conflict_action: str
) -> str:
    placeholders = "(" + ", ".join("?" for _ in columns) + ")"
    values = ", ".join(placeholders for _ in range(row_count))
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values} {conflict_action}"


def issue_row(issue: Issue, synced_at: datetime) -> tuple[Any, ...]:
    return (
        issue.id,
        issue.application,
        issue.environment,
        issue.fingerprint,
        issue.exception_type,
        issue.message,
        issue.status.value,
        issue.occurrence_count,
        issue.first_seen_at,
        issue.last_seen_at,
        issue.resolved_at,
        synced_at,
    )


def event_row(event: Event) -> tuple[Any, ...]:
    if event.issue_id is None:
        raise ValueError(f"Event {event.id} has no issue_id and cannot be stored")
    return (
        event.id,
        event.issue_id,
        event.exception_type,
        event.message,
        event.stack_trace,
        event.occurred_at,
        event.received_at,
        event.correlation_id,
        None if event.context is None else json.dumps(event.context),
    )
