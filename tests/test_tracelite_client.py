import logging
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from insights.tracelite_client import (
    PAGE_SIZE,
    TraceLiteClient,
    TraceLiteConnectionError,
    TraceLiteError,
)

MANAGEMENT_KEY = "management_key_0123456789abcdef01234567"
ISSUE_ID = UUID("5b1f7c9e-3f4a-4d2b-9a61-0c8e2f3d4a5b")


def issue_json(number):
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "application": "checkout-service",
        "environment": "production",
        "fingerprint": f"{number:064x}",
        "exceptionType": "java.lang.IllegalStateException",
        "message": "Cart is empty",
        "status": "UNRESOLVED",
        "occurrenceCount": 42,
        "firstSeenAt": "2026-10-12T09:00:00.123456Z",
        "lastSeenAt": "2026-10-13T07:45:10.654321Z",
        "resolvedAt": None,
    }


def event_json(number):
    return {
        "id": f"0d9c8b7a-6f5e-4d3c-8b2a-{number:012d}",
        "exceptionType": "java.lang.IllegalStateException",
        "message": "Cart is empty",
        "stackTrace": "java.lang.IllegalStateException: Cart is empty",
        "occurredAt": "2026-10-13T09:59:58Z",
        "receivedAt": "2026-10-13T10:00:00.123456Z",
        "correlationId": "req-7f3a",
        "context": {"orderId": "o-1001"},
    }


STATS_JSON = {
    "issueId": str(ISSUE_ID),
    "occurrenceCount": 42,
    "storedEventCount": 42,
    "firstSeenAt": "2026-10-01T08:00:00.123456Z",
    "lastSeenAt": "2026-10-13T10:00:00.123456Z",
    "calculatedAt": "2026-10-13T10:30:00.654321Z",
    "eventsLast24Hours": 5,
    "eventsLast7Days": 17,
    "dailyEventCounts": [
        {"date": "2026-09-30", "count": 0},
        {"date": "2026-10-13", "count": 3},
    ],
}


def page_response(items, page, total_items):
    total_pages = -(-total_items // PAGE_SIZE)
    return httpx.Response(
        200,
        json={
            "items": items,
            "page": page,
            "size": PAGE_SIZE,
            "totalItems": total_items,
            "totalPages": total_pages,
        },
    )


def problem_response(status, title, detail="Details."):
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": title, "status": status, "detail": detail},
        headers={"Content-Type": "application/problem+json"},
    )


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def make_client(handler, base_url="http://tracelite.test"):
    fake_sleep = FakeSleep()
    client = TraceLiteClient(
        base_url,
        SecretStr(MANAGEMENT_KEY),
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=fake_sleep,
    )
    return client, fake_sleep


def test_iter_issues_reads_all_three_pages_in_order():
    total = 2 * PAGE_SIZE + 5
    numbers = list(range(total))
    recorder = Recorder(
        page_response([issue_json(n) for n in numbers[:PAGE_SIZE]], 0, total),
        page_response([issue_json(n) for n in numbers[PAGE_SIZE : 2 * PAGE_SIZE]], 1, total),
        page_response([issue_json(n) for n in numbers[2 * PAGE_SIZE :]], 2, total),
    )
    client, _ = make_client(recorder)

    issues = list(client.iter_issues())

    assert [issue.fingerprint for issue in issues] == [f"{n:064x}" for n in numbers]
    assert [request.url.params["page"] for request in recorder.requests] == ["0", "1", "2"]
    assert all(request.url.params["size"] == "100" for request in recorder.requests)


def test_iter_issues_requests_a_stable_order_and_the_documented_path():
    recorder = Recorder(page_response([issue_json(1)], 0, 1))
    client, _ = make_client(recorder, base_url="http://tracelite.test/")

    list(client.iter_issues())

    url = recorder.requests[0].url
    assert url.path == "/api/v1/issues"
    assert url.params["sort"] == "FIRST_SEEN"
    assert url.params["direction"] == "ASC"
    assert "environment" not in url.params
    assert "lastSeenFrom" not in url.params


def test_iter_issues_with_no_results_makes_one_request():
    recorder = Recorder(page_response([], 0, 0))
    client, _ = make_client(recorder)

    assert list(client.iter_issues()) == []
    assert len(recorder.requests) == 1


def test_iter_issues_is_lazy_and_reads_the_next_page_only_when_needed():
    total = PAGE_SIZE + 1
    recorder = Recorder(
        page_response([issue_json(n) for n in range(PAGE_SIZE)], 0, total),
        page_response([issue_json(PAGE_SIZE)], 1, total),
    )
    client, _ = make_client(recorder)

    issues = client.iter_issues()
    assert recorder.requests == []

    next(issues)
    assert len(recorder.requests) == 1


def test_iter_issues_sends_environment_and_encodes_plus_in_last_seen_from():
    recorder = Recorder(page_response([], 0, 0))
    client, _ = make_client(recorder)
    last_seen_from = datetime(2026, 10, 20, 10, 0, tzinfo=timezone(timedelta(hours=2)))

    list(client.iter_issues(environment="staging", last_seen_from=last_seen_from))

    url = recorder.requests[0].url
    assert url.params["environment"] == "staging"
    assert url.params["lastSeenFrom"] == "2026-10-20T10:00:00+02:00"
    assert b"lastSeenFrom=2026-10-20T10%3A00%3A00%2B02%3A00" in url.query


def test_iter_issues_rejects_a_last_seen_from_without_time_zone():
    client, _ = make_client(Recorder(page_response([], 0, 0)))

    with pytest.raises(ValueError, match="time zone"):
        client.iter_issues(last_seen_from=datetime(2026, 10, 20, 10, 0))


def test_iter_events_pages_newest_first_and_sets_the_issue_id():
    total = PAGE_SIZE + 2
    recorder = Recorder(
        page_response([event_json(n) for n in range(PAGE_SIZE)], 0, total),
        page_response([event_json(PAGE_SIZE), event_json(PAGE_SIZE + 1)], 1, total),
    )
    client, _ = make_client(recorder)

    events = list(client.iter_events(ISSUE_ID))

    assert len(events) == total
    assert events[0].id == UUID("0d9c8b7a-6f5e-4d3c-8b2a-000000000000")
    assert all(event.issue_id == ISSUE_ID for event in events)
    assert events[0].context == {"orderId": "o-1001"}
    assert recorder.requests[0].url.path == f"/api/v1/issues/{ISSUE_ID}/events"
    assert [request.url.params["page"] for request in recorder.requests] == ["0", "1"]


def test_iter_events_stops_at_an_empty_page_past_the_end():
    recorder = Recorder(page_response([], 0, 0))
    client, _ = make_client(recorder)

    assert list(client.iter_events(ISSUE_ID)) == []
    assert len(recorder.requests) == 1


def test_get_stats_parses_the_documented_response():
    recorder = Recorder(httpx.Response(200, json=STATS_JSON))
    client, _ = make_client(recorder)

    stats = client.get_stats(ISSUE_ID)

    assert recorder.requests[0].url.path == f"/api/v1/issues/{ISSUE_ID}/stats"
    assert stats.issue_id == ISSUE_ID
    assert stats.events_last_24_hours == 5
    assert stats.events_last_7_days == 17
    assert stats.calculated_at == datetime(2026, 10, 13, 10, 30, 0, 654321, tzinfo=UTC)
    assert stats.daily_event_counts[1].date == date(2026, 10, 13)
    assert stats.daily_event_counts[1].count == 3
    assert stats.model_dump(mode="json", by_alias=True) == STATS_JSON


def test_unknown_issue_raises_tracelite_error_with_the_problem_details():
    recorder = Recorder(problem_response(404, "Issue not found", "No issue exists with this ID."))
    client, fake_sleep = make_client(recorder)

    with pytest.raises(TraceLiteError) as error:
        list(client.iter_events(ISSUE_ID))

    assert error.value.status == 404
    assert error.value.title == "Issue not found"
    assert error.value.detail == "No issue exists with this ID."
    assert str(error.value) == "404 Issue not found: No issue exists with this ID."
    assert len(recorder.requests) == 1
    assert fake_sleep.calls == []


def test_error_without_problem_body_uses_the_reason_phrase():
    client, _ = make_client(Recorder(httpx.Response(500, text="oops")))

    with pytest.raises(TraceLiteError) as error:
        client.get_stats(ISSUE_ID)

    assert error.value.status == 500
    assert error.value.title == "Internal Server Error"
    assert error.value.detail is None
    assert str(error.value) == "500 Internal Server Error"


@pytest.mark.parametrize(("status", "title"), [(401, "Unauthorized"), (403, "Forbidden")])
def test_rejected_management_key_is_raised_immediately(status, title):
    recorder = Recorder(problem_response(status, title))
    client, fake_sleep = make_client(recorder)

    with pytest.raises(TraceLiteError) as error:
        list(client.iter_issues())

    assert error.value.status == status
    assert len(recorder.requests) == 1
    assert fake_sleep.calls == []


def test_service_unavailable_is_retried_and_then_succeeds():
    recorder = Recorder(
        problem_response(503, "Service Unavailable"),
        httpx.Response(200, json=STATS_JSON),
    )
    client, fake_sleep = make_client(recorder)

    stats = client.get_stats(ISSUE_ID)

    assert stats.issue_id == ISSUE_ID
    assert len(recorder.requests) == 2
    assert fake_sleep.calls == [0.5]


def test_service_unavailable_gives_up_after_five_tries_with_exponential_backoff():
    recorder = Recorder(problem_response(503, "Service Unavailable"))
    client, fake_sleep = make_client(recorder)

    with pytest.raises(TraceLiteError) as error:
        client.get_stats(ISSUE_ID)

    assert error.value.status == 503
    assert len(recorder.requests) == 5
    assert fake_sleep.calls == [0.5, 1.0, 2.0, 4.0]


def test_service_unavailable_on_a_later_page_retries_that_page():
    total = PAGE_SIZE + 1
    recorder = Recorder(
        page_response([issue_json(n) for n in range(PAGE_SIZE)], 0, total),
        problem_response(503, "Service Unavailable"),
        page_response([issue_json(PAGE_SIZE)], 1, total),
    )
    client, fake_sleep = make_client(recorder)

    issues = list(client.iter_issues())

    assert len(issues) == total
    assert [request.url.params["page"] for request in recorder.requests] == ["0", "1", "1"]
    assert fake_sleep.calls == [0.5]


def test_connection_error_is_retried_and_then_succeeds():
    recorder = Recorder(httpx.ConnectError("connection refused"), page_response([], 0, 0))
    client, fake_sleep = make_client(recorder)

    assert list(client.iter_issues()) == []
    assert fake_sleep.calls == [0.5]


def test_unreachable_server_raises_connection_error_after_five_tries():
    recorder = Recorder(httpx.ConnectTimeout("timed out"))
    client, fake_sleep = make_client(recorder)

    with pytest.raises(
        TraceLiteConnectionError, match="Could not reach TraceLite at http://tracelite.test"
    ):
        list(client.iter_issues())

    assert len(recorder.requests) == 5
    assert fake_sleep.calls == [0.5, 1.0, 2.0, 4.0]


def test_management_key_is_sent_on_every_request():
    total = PAGE_SIZE + 1
    recorder = Recorder(
        page_response([issue_json(n) for n in range(PAGE_SIZE)], 0, total),
        page_response([issue_json(PAGE_SIZE)], 1, total),
    )
    client, _ = make_client(recorder)

    list(client.iter_issues())

    assert [request.headers["X-API-Key"] for request in recorder.requests] == [
        MANAGEMENT_KEY,
        MANAGEMENT_KEY,
    ]


def test_management_key_never_appears_in_logs_errors_or_repr(caplog):
    recorder = Recorder(
        problem_response(503, "Service Unavailable"),
        problem_response(401, "Unauthorized"),
    )
    client, _ = make_client(recorder)

    with caplog.at_level(logging.DEBUG), pytest.raises(TraceLiteError) as error:
        list(client.iter_issues())

    assert caplog.records
    assert MANAGEMENT_KEY not in caplog.text
    assert MANAGEMENT_KEY not in str(error.value)
    assert MANAGEMENT_KEY not in repr(client.management_key)
