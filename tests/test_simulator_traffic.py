import json
import re
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from insights.simulator.scenarios import (
    FAILURE_TYPES,
    FAMILIES,
    Catalogue,
    FailureType,
    Incident,
    default_catalogue,
)
from insights.simulator.traffic import (
    PLACEHOLDER_VALUES,
    daily_factor,
    fill_template,
    hourly_rate,
    incident_multiplier,
    java_stack_trace,
    placeholder_names,
    simulate,
    weekly_factor,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=7)
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
FRAME_PATTERN = re.compile(r"^\tat [\w.$]+\.[\w$<>]+\([\w$]+\.java:\d+\)$")


def single_failure(rate=2.0):
    return FailureType(
        id="checkout-empty-cart",
        application="checkout-service",
        environment="production",
        exception_type="java.lang.IllegalStateException",
        message_template="Cart is empty",
        stack_frames=("com.example.cart.CartService.checkout(CartService.java:42)",),
        base_rate_per_hour=rate,
    )


def one_failure_catalogue(rate=2.0, incidents=()):
    return Catalogue(families=(), failure_types=(single_failure(rate),), incidents=incidents)


@pytest.fixture(scope="module")
def week_of_events():
    return simulate(default_catalogue(1, START, END), START, END, seed=1)


def test_daily_factor_peaks_at_fourteen_and_is_lowest_at_night():
    assert daily_factor(datetime(2026, 9, 1, 14, tzinfo=UTC)) == pytest.approx(1.0)
    assert daily_factor(datetime(2026, 9, 1, 2, tzinfo=UTC)) == pytest.approx(0.4)
    assert daily_factor(datetime(2026, 9, 1, 8, tzinfo=UTC)) == pytest.approx(0.7)


def test_weekly_factor_is_lower_on_weekends():
    assert weekly_factor(datetime(2026, 9, 4, tzinfo=UTC)) == 1.0
    assert weekly_factor(datetime(2026, 9, 5, tzinfo=UTC)) == 0.6
    assert weekly_factor(datetime(2026, 9, 6, tzinfo=UTC)) == 0.6


def test_hourly_rate_combines_base_rate_with_daily_weekly_and_incident_factors():
    saturday_peak = datetime(2026, 9, 5, 14, tzinfo=UTC)
    incident = Incident(
        id="incident-01",
        application="checkout-service",
        environment="production",
        failure_ids=("checkout-empty-cart",),
        start=saturday_peak,
        end=saturday_peak + timedelta(hours=1),
        multiplier=10.0,
    )

    rate = hourly_rate(single_failure(rate=2.0), saturday_peak, (incident,))

    assert rate == pytest.approx(2.0 * 1.0 * 0.6 * 10.0)


def test_incident_multiplier_is_one_outside_incidents():
    incident = Incident(
        id="incident-01",
        application="checkout-service",
        environment="production",
        failure_ids=("a",),
        start=START,
        end=START + timedelta(hours=1),
        multiplier=10.0,
    )

    assert incident_multiplier("a", START, (incident,)) == 10.0
    assert incident_multiplier("a", START + timedelta(hours=1), (incident,)) == 1.0
    assert incident_multiplier("b", START, (incident,)) == 1.0


def test_same_seed_gives_identical_events(week_of_events):
    again = simulate(default_catalogue(1, START, END), START, END, seed=1)

    assert again == week_of_events


def test_different_seed_gives_different_events(week_of_events):
    other = simulate(default_catalogue(1, START, END), START, END, seed=2)

    assert other != week_of_events


def test_events_lie_inside_period_are_timezone_aware_and_sorted(week_of_events):
    times = [event.occurred_at for event in week_of_events]

    assert week_of_events
    assert all(START <= moment < END for moment in times)
    assert all(moment.tzinfo is not None for moment in times)
    assert times == sorted(times)


def test_mean_count_matches_configured_rate_over_ten_thousand_hours():
    end = START + timedelta(hours=10_000)
    failure = single_failure(rate=2.0)
    events = simulate(one_failure_catalogue(rate=2.0), START, end, seed=7)
    hours = [START + timedelta(hours=offset) for offset in range(10_000)]
    expected_mean = np.mean([hourly_rate(failure, hour, ()) for hour in hours])

    observed_mean = len(events) / 10_000

    assert observed_mean == pytest.approx(expected_mean, rel=0.05)


def test_incident_hours_have_higher_mean_than_same_hours_outside_incidents():
    days = 200
    end = START + timedelta(days=days)
    incident_days = range(0, days, 2)
    incidents = tuple(
        Incident(
            id=f"incident-{day}",
            application="checkout-service",
            environment="production",
            failure_ids=("checkout-empty-cart",),
            start=START + timedelta(days=day, hours=14),
            end=START + timedelta(days=day, hours=15),
            multiplier=5.0,
        )
        for day in incident_days
    )
    events = simulate(one_failure_catalogue(rate=1.0, incidents=incidents), START, end, seed=3)
    counts_at_fourteen = np.zeros(days)
    for event in events:
        if event.occurred_at.hour == 14:
            counts_at_fourteen[(event.occurred_at - START).days] += 1

    incident_mean = counts_at_fourteen[0::2].mean()
    normal_mean = counts_at_fourteen[1::2].mean()

    assert incident_mean > 3 * normal_mean


def test_event_payloads_never_contain_failure_or_family_ids(week_of_events):
    hidden_ids = [failure.id for failure in FAILURE_TYPES] + [family.id for family in FAMILIES]

    for event in week_of_events:
        payload = event.payload()
        text = json.dumps(payload)
        assert "failureId" not in payload
        assert "familyId" not in payload
        assert not any(hidden_id in text for hidden_id in hidden_ids)


def test_event_payloads_satisfy_tracelite_field_rules(week_of_events):
    for event in week_of_events:
        payload = event.payload()
        assert set(payload) == {
            "application",
            "environment",
            "exceptionType",
            "message",
            "stackTrace",
            "occurredAt",
            "correlationId",
        }
        assert NAME_PATTERN.match(payload["application"])
        assert len(payload["application"]) <= 100
        assert NAME_PATTERN.match(payload["environment"])
        assert len(payload["environment"]) <= 50
        assert payload["exceptionType"].strip()
        assert len(payload["exceptionType"]) <= 255
        assert len(payload["message"]) <= 4000
        assert len(payload["stackTrace"]) <= 65_536
        assert NAME_PATTERN.match(payload["correlationId"])
        assert len(payload["correlationId"]) <= 128
        assert datetime.fromisoformat(payload["occurredAt"]).tzinfo is not None
        assert not any("\u0000" in value for value in payload.values())
        assert len(json.dumps(payload).encode("utf-8")) <= 256 * 1024


def test_stack_trace_uses_java_format(week_of_events):
    for event in week_of_events:
        header, *frame_lines = event.stack_trace.split("\n")
        assert header == f"{event.exception_type}: {event.message}"
        assert frame_lines
        assert all(FRAME_PATTERN.match(line) for line in frame_lines)


def test_java_stack_trace_puts_each_frame_on_its_own_line():
    trace = java_stack_trace(
        "java.lang.IllegalStateException",
        "Cart is empty",
        ("a.B.c(B.java:1)", "a.D.e(D.java:2)"),
    )

    assert trace == (
        "java.lang.IllegalStateException: Cart is empty\n\tat a.B.c(B.java:1)\n\tat a.D.e(D.java:2)"
    )


def test_every_message_placeholder_has_a_value_source():
    for failure in FAILURE_TYPES:
        assert set(placeholder_names(failure.message_template)) <= set(PLACEHOLDER_VALUES)


def test_fill_template_replaces_every_placeholder():
    rng = np.random.default_rng(1)

    for failure in FAILURE_TYPES:
        message = fill_template(failure.message_template, rng)
        assert "{" not in message
        assert "}" not in message


def test_fill_template_keeps_text_without_placeholders():
    assert fill_template("Cart is empty", np.random.default_rng(1)) == "Cart is empty"


def test_simulate_rejects_naive_period():
    with pytest.raises(ValueError, match="timezone-aware"):
        simulate(one_failure_catalogue(), datetime(2026, 9, 1), datetime(2026, 9, 2), seed=1)
