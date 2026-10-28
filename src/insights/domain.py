import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TraceLiteModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class IssueStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"


class Issue(TraceLiteModel):
    id: UUID
    application: str
    environment: str
    fingerprint: str
    exception_type: str
    message: str | None = None
    status: IssueStatus
    occurrence_count: int
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    resolved_at: AwareDatetime | None = None


class Event(TraceLiteModel):
    id: UUID
    issue_id: UUID | None = None
    exception_type: str
    message: str | None = None
    stack_trace: str | None = None
    occurred_at: AwareDatetime
    received_at: AwareDatetime
    correlation_id: str | None = None
    context: dict[str, Any] | None = None


class DailyEventCount(TraceLiteModel):
    date: datetime.date
    count: int


class IssueStats(TraceLiteModel):
    issue_id: UUID
    occurrence_count: int
    stored_event_count: int
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    calculated_at: AwareDatetime
    events_last_24_hours: int
    events_last_7_days: int
    daily_event_counts: list[DailyEventCount]


class Page[T](TraceLiteModel):
    items: list[T]
    page: int
    size: int
    total_items: int
    total_pages: int
