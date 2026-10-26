import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from insights.simulator.sender import (
    NEWEST_EVENT_AGE,
    OLDEST_EVENT_AGE,
    SenderError,
    SendOutcome,
    SendStatus,
    TraceLiteSender,
    count_by_status,
    parse_timestamp,
    send_events,
    shift_to_now,
)

CHECKOUT_KEY = "checkout_key_0123456789abcdef0123456789"
AUTH_KEY = "auth_key_0123456789abcdef0123456789abcd"
KEYS = {"checkout-service": SecretStr(CHECKOUT_KEY), "auth-service": SecretStr(AUTH_KEY)}
NOW = datetime(2026, 10, 26, 12, 30, 15, 500000, tzinfo=UTC)
ISSUE_ID = "5b1f7c9e-3f4a-4d2b-9a61-0c8e2f3d4a5b"


def created_response(request):
    return httpx.Response(
        201,
        json={
            "eventId": "9d2c4e6a-1b3f-4a5c-8e7d-2f4a6b8c0d1e",
            "issueId": ISSUE_ID,
            "receivedAt": "2026-10-26T12:30:16.123456Z",
        },
    )


def problem_response(status, title):
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": title, "status": status, "detail": "Details."},
        headers={"Content-Type": "application/problem+json"},
    )


def make_payload(application="checkout-service", occurred_at="2026-10-20T10:00:00Z"):
    return {
        "application": application,
        "environment": "production",
        "exceptionType": "java.lang.IllegalStateException",
        "message": "Cart is empty",
        "stackTrace": "java.lang.IllegalStateException: Cart is empty",
        "occurredAt": occurred_at,
        "correlationId": "req-0123456789ab",
    }


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response(request) if callable(response) else response


def make_sender(handler, base_url="http://tracelite.test", max_requests_per_second=1000.0):
    fake_time = FakeTime()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sender = TraceLiteSender(
        base_url,
        KEYS,
        client,
        max_requests_per_second=max_requests_per_second,
        sleep=fake_time.sleep,
        clock=fake_time.clock,
    )
    return sender, fake_time


def retry_sleeps(fake_time):
    return [seconds for seconds in fake_time.sleeps if seconds >= 0.5]


def test_send_posts_payload_as_json_with_the_application_ingestion_key():
    recorder = Recorder(created_response)
    sender, _ = make_sender(recorder)
    payload = make_payload(application="auth-service")

    sender.send(0, payload)

    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://tracelite.test/api/v1/events"
    assert request.headers["X-API-Key"] == AUTH_KEY
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == payload


def test_send_accepts_base_url_with_trailing_slash():
    recorder = Recorder(created_response)
    sender, _ = make_sender(recorder, base_url="http://tracelite.test/")

    sender.send(0, make_payload())

    assert str(recorder.requests[0].url) == "http://tracelite.test/api/v1/events"


def test_created_response_records_the_returned_issue_id():
    sender, _ = make_sender(Recorder(created_response))

    outcome = sender.send(7, make_payload())

    assert outcome == SendOutcome(7, SendStatus.SENT, "2026-10-20T10:00:00Z", issue_id=ISSUE_ID)


def test_service_unavailable_is_retried_and_then_succeeds():
    recorder = Recorder(problem_response(503, "Service Unavailable"), created_response)
    sender, fake_time = make_sender(recorder)

    outcome = sender.send(0, make_payload())

    assert outcome.status is SendStatus.SENT
    assert len(recorder.requests) == 2
    assert retry_sleeps(fake_time) == [0.5]


def test_service_unavailable_gives_up_after_five_tries_with_exponential_backoff():
    recorder = Recorder(problem_response(503, "Service Unavailable"))
    sender, fake_time = make_sender(recorder)

    outcome = sender.send(0, make_payload())

    assert outcome.status is SendStatus.FAILED
    assert outcome.reason == "503 Service Unavailable"
    assert len(recorder.requests) == 5
    assert retry_sleeps(fake_time) == [0.5, 1.0, 2.0, 4.0]


def test_connection_error_is_retried_and_then_succeeds():
    recorder = Recorder(httpx.ConnectError("connection refused"), created_response)
    sender, fake_time = make_sender(recorder)

    outcome = sender.send(0, make_payload())

    assert outcome.status is SendStatus.SENT
    assert retry_sleeps(fake_time) == [0.5]


def test_unreachable_server_stops_after_five_tries():
    recorder = Recorder(httpx.ConnectTimeout("timed out"))
    sender, _ = make_sender(recorder)

    with pytest.raises(SenderError, match="Could not reach TraceLite at http://tracelite.test"):
        sender.send(0, make_payload())

    assert len(recorder.requests) == 5


@pytest.mark.parametrize(("status", "title"), [(401, "Unauthorized"), (403, "Forbidden")])
def test_rejected_key_stops_with_readable_message_without_the_key(status, title):
    recorder = Recorder(problem_response(status, title))
    sender, _ = make_sender(recorder)

    with pytest.raises(SenderError) as error:
        sender.send(0, make_payload())

    message = str(error.value)
    assert message == f"TraceLite refused the ingestion key for checkout-service: {status} {title}"
    assert CHECKOUT_KEY not in message
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    ("status", "title"), [(400, "Invalid request"), (413, "Payload Too Large")]
)
def test_invalid_or_too_large_event_is_recorded_as_rejected_with_problem_title(status, title):
    recorder = Recorder(problem_response(status, title))
    sender, _ = make_sender(recorder)

    outcome = sender.send(3, make_payload())

    assert outcome == SendOutcome(3, SendStatus.REJECTED, "2026-10-20T10:00:00Z", reason=title)
    assert len(recorder.requests) == 1


def test_server_error_is_recorded_as_failure_without_retry():
    recorder = Recorder(problem_response(500, "Internal Server Error"))
    sender, _ = make_sender(recorder)

    outcome = sender.send(0, make_payload())

    assert outcome.status is SendStatus.FAILED
    assert outcome.reason == "500 Internal Server Error"
    assert len(recorder.requests) == 1


def test_error_without_problem_details_uses_the_http_reason_phrase():
    sender, _ = make_sender(Recorder(httpx.Response(415, text="not json")))

    outcome = sender.send(0, make_payload())

    assert outcome.status is SendStatus.FAILED
    assert outcome.reason == "415 Unsupported Media Type"


def test_requests_are_spaced_by_the_rate_limit():
    sender, fake_time = make_sender(Recorder(created_response), max_requests_per_second=50)

    for index in range(3):
        sender.send(index, make_payload())

    assert fake_time.sleeps == pytest.approx([0.02, 0.02])


def test_rate_limit_does_not_wait_when_requests_are_already_slow():
    sender, fake_time = make_sender(Recorder(created_response), max_requests_per_second=50)

    sender.send(0, make_payload())
    fake_time.now += 1.0
    sender.send(1, make_payload())

    assert fake_time.sleeps == []


@pytest.mark.parametrize("rate", [0, -5])
def test_rate_limit_must_be_positive(rate):
    with pytest.raises(ValueError, match="greater than zero"):
        make_sender(Recorder(created_response), max_requests_per_second=rate)


def test_require_keys_names_every_application_without_a_key():
    sender, _ = make_sender(Recorder(created_response))

    with pytest.raises(SenderError, match="No ingestion key configured for: billing, catalog"):
        sender.require_keys(["checkout-service", "catalog", "billing", "catalog"])


def test_shift_moves_newest_event_to_one_minute_before_now_and_keeps_spacing():
    payloads = [
        make_payload(occurred_at="2026-09-01T00:00:00Z"),
        make_payload(occurred_at="2026-09-02T06:30:00Z"),
    ]

    shifted = shift_to_now(payloads, NOW)

    assert [payload["occurredAt"] for payload in shifted] == [
        "2026-10-25T05:59:15Z",
        "2026-10-26T12:29:15Z",
    ]
    assert payloads[1]["occurredAt"] == "2026-09-02T06:30:00Z"


def test_shifted_events_respect_tracelite_time_limits():
    payloads = [
        make_payload(occurred_at=f"2026-08-{day:02d}T{hour:02d}:17:42Z")
        for day in range(1, 29)
        for hour in (0, 13, 23)
    ]

    shifted = shift_to_now(payloads, NOW)

    moments = [parse_timestamp(payload["occurredAt"]) for payload in shifted]
    assert max(moments) <= NOW - NEWEST_EVENT_AGE
    assert max(moments) <= NOW + timedelta(minutes=5)
    assert min(moments) >= NOW - OLDEST_EVENT_AGE
    assert min(moments) >= NOW - timedelta(days=30)


def test_shift_of_no_events_is_empty():
    assert shift_to_now([], NOW) == []


def test_send_events_skips_events_older_than_29_days_after_shifting():
    recorder = Recorder(created_response)
    sender, _ = make_sender(recorder)
    payloads = [
        make_payload(occurred_at="2026-09-01T00:00:00Z"),
        make_payload(occurred_at="2026-09-28T00:00:00Z"),
        make_payload(occurred_at="2026-10-01T00:00:00Z"),
    ]

    outcomes = send_events(sender, payloads, NOW)

    assert [outcome.status for outcome in outcomes] == [
        SendStatus.SKIPPED,
        SendStatus.SENT,
        SendStatus.SENT,
    ]
    assert [outcome.event_index for outcome in outcomes] == [0, 1, 2]
    sent_times = [json.loads(request.content)["occurredAt"] for request in recorder.requests]
    assert sent_times == ["2026-10-23T12:29:15Z", "2026-10-26T12:29:15Z"]


def test_send_events_checks_keys_before_sending_anything():
    recorder = Recorder(created_response)
    sender, _ = make_sender(recorder)
    payloads = [make_payload(), make_payload(application="catalog-service")]

    with pytest.raises(SenderError, match="catalog-service"):
        send_events(sender, payloads, NOW)

    assert recorder.requests == []


def test_send_events_counts_rejected_events_and_continues():
    recorder = Recorder(
        created_response, problem_response(400, "Invalid request"), created_response
    )
    sender, _ = make_sender(recorder)
    payloads = [make_payload(occurred_at=f"2026-10-2{day}T00:00:00Z") for day in range(3)]

    outcomes = send_events(sender, payloads, NOW)

    assert count_by_status(outcomes) == {
        SendStatus.SENT: 2,
        SendStatus.REJECTED: 1,
        SendStatus.FAILED: 0,
        SendStatus.SKIPPED: 0,
    }


def test_outcome_row_is_plain_json():
    outcome = SendOutcome(4, SendStatus.REJECTED, "2026-10-26T12:00:00Z", reason="Invalid request")

    assert json.loads(json.dumps(outcome.row())) == {
        "event_index": 4,
        "status": "rejected",
        "occurred_at": "2026-10-26T12:00:00Z",
        "issue_id": None,
        "reason": "Invalid request",
    }
