from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from insights.domain import Event, Issue, IssueStatus, Page

ISSUE_LIST_RESPONSE = {
    "items": [
        {
            "id": "5b1f7c9e-3f4a-4d2b-9a61-0c8e2f3d4a5b",
            "application": "checkout-service",
            "environment": "production",
            "fingerprint": "3c1f0e5a7b9d2f4e6a8c0b1d3f5e7a9c2b4d6f8e0a1c3e5b7d9f1a3c5e7b9d0f",
            "exceptionType": "java.lang.IllegalStateException",
            "message": "Cart is empty",
            "status": "UNRESOLVED",
            "occurrenceCount": 42,
            "firstSeenAt": "2026-10-12T09:00:00.123456Z",
            "lastSeenAt": "2026-10-13T07:45:10.654321Z",
            "resolvedAt": None,
        }
    ],
    "page": 0,
    "size": 20,
    "totalItems": 1,
    "totalPages": 1,
}

EVENT_LIST_RESPONSE = {
    "items": [
        {
            "id": "0d9c8b7a-6f5e-4d3c-8b2a-1f0e9d8c7b6a",
            "exceptionType": "java.lang.IllegalStateException",
            "message": "Cart is empty",
            "stackTrace": (
                "java.lang.IllegalStateException: Cart is empty\n"
                "\tat com.example.cart.CartService.checkout(CartService.java:42)"
            ),
            "occurredAt": "2026-10-13T09:59:58Z",
            "receivedAt": "2026-10-13T10:00:00.123456Z",
            "correlationId": "req-7f3a",
            "context": {"orderId": "o-1001"},
        }
    ],
    "page": 0,
    "size": 20,
    "totalItems": 1,
    "totalPages": 1,
}


def issue_json(**overrides):
    return {**ISSUE_LIST_RESPONSE["items"][0], **overrides}


def event_json(**overrides):
    return {**EVENT_LIST_RESPONSE["items"][0], **overrides}


def test_issue_list_example_parses_into_page_of_issues():
    page = Page[Issue].model_validate(ISSUE_LIST_RESPONSE)

    assert page.page == 0
    assert page.size == 20
    assert page.total_items == 1
    assert page.total_pages == 1
    issue = page.items[0]
    assert issue.id == UUID("5b1f7c9e-3f4a-4d2b-9a61-0c8e2f3d4a5b")
    assert issue.application == "checkout-service"
    assert issue.environment == "production"
    assert issue.exception_type == "java.lang.IllegalStateException"
    assert issue.message == "Cart is empty"
    assert issue.status is IssueStatus.UNRESOLVED
    assert issue.occurrence_count == 42
    assert issue.first_seen_at == datetime(2026, 10, 12, 9, 0, 0, 123456, tzinfo=UTC)
    assert issue.last_seen_at == datetime(2026, 10, 13, 7, 45, 10, 654321, tzinfo=UTC)
    assert issue.resolved_at is None


def test_event_list_example_parses_into_page_of_events():
    page = Page[Event].model_validate(EVENT_LIST_RESPONSE)

    event = page.items[0]
    assert event.id == UUID("0d9c8b7a-6f5e-4d3c-8b2a-1f0e9d8c7b6a")
    assert event.issue_id is None
    assert event.exception_type == "java.lang.IllegalStateException"
    assert event.stack_trace.startswith("java.lang.IllegalStateException: Cart is empty\n\tat ")
    assert event.occurred_at == datetime(2026, 10, 13, 9, 59, 58, tzinfo=UTC)
    assert event.received_at == datetime(2026, 10, 13, 10, 0, 0, 123456, tzinfo=UTC)
    assert event.correlation_id == "req-7f3a"
    assert event.context == {"orderId": "o-1001"}


def test_resolved_issue_parses_status_and_resolved_at_with_offset():
    issue = Issue.model_validate(
        issue_json(status="RESOLVED", resolvedAt="2026-10-13T10:00:00+02:00")
    )

    assert issue.status is IssueStatus.RESOLVED
    assert issue.resolved_at == datetime(2026, 10, 13, 8, 0, 0, tzinfo=UTC)


def test_event_accepts_null_for_all_optional_fields():
    event = Event.model_validate(
        event_json(message=None, stackTrace=None, correlationId=None, context=None)
    )

    assert event.message is None
    assert event.stack_trace is None
    assert event.correlation_id is None
    assert event.context is None


def test_issue_accepts_null_message():
    issue = Issue.model_validate(issue_json(message=None))

    assert issue.message is None


def test_event_issue_id_can_be_set_by_field_name():
    issue_id = UUID("5b1f7c9e-3f4a-4d2b-9a61-0c8e2f3d4a5b")

    event = Event.model_validate({**event_json(), "issue_id": issue_id})

    assert event.issue_id == issue_id


@pytest.mark.parametrize("field", ["firstSeenAt", "lastSeenAt", "resolvedAt"])
def test_issue_rejects_naive_datetimes(field):
    with pytest.raises(ValidationError, match="timezone"):
        Issue.model_validate(issue_json(**{field: "2026-10-12T09:00:00"}))


@pytest.mark.parametrize("field", ["occurredAt", "receivedAt"])
def test_event_rejects_naive_datetimes(field):
    with pytest.raises(ValidationError, match="timezone"):
        Event.model_validate(event_json(**{field: "2026-10-13T09:59:58"}))


def test_issue_rejects_unknown_status():
    with pytest.raises(ValidationError, match="status"):
        Issue.model_validate(issue_json(status="IGNORED"))


def test_issue_requires_exception_type():
    issue = issue_json()
    del issue["exceptionType"]

    with pytest.raises(ValidationError, match="exceptionType"):
        Issue.model_validate(issue)


def test_issue_dump_by_alias_reproduces_tracelite_json():
    issue = Issue.model_validate(issue_json())

    assert issue.model_dump(mode="json", by_alias=True) == issue_json()


def test_event_round_trips_through_camel_case_dump():
    event = Event.model_validate(event_json())

    dumped = event.model_dump(by_alias=True)

    assert "stackTrace" in dumped
    assert "stack_trace" not in dumped
    assert Event.model_validate(dumped) == event


def test_page_round_trips_through_camel_case_dump():
    page = Page[Issue].model_validate(ISSUE_LIST_RESPONSE)

    assert page.model_dump(mode="json", by_alias=True) == ISSUE_LIST_RESPONSE
