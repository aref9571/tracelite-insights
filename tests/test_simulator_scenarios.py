import re
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone

import pytest

from insights.simulator.scenarios import (
    FAILURE_TYPES,
    HARD_INCIDENT_COUNT,
    INCIDENT_COUNT,
    MAX_HARD_INCIDENT_HOURS,
    MAX_HARD_MULTIPLIER,
    MAX_INCIDENT_HOURS,
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    MINIMUM_DAYS,
    WARM_UP_HOURS,
    Incident,
    default_catalogue,
    hour_starts,
    java_frame,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=28)


def catalogue(seed=1):
    return default_catalogue(seed, START, END)


def method_without_line(frame):
    return re.sub(r"\(.*\)$", "", frame)


def test_java_frame_derives_file_name_from_class():
    assert java_frame("com.example.cart.CartService.checkout:42") == (
        "com.example.cart.CartService.checkout(CartService.java:42)"
    )


def test_java_frame_uses_outer_class_file_for_inner_class():
    assert java_frame("java.util.ArrayList$Itr.next:1052") == (
        "java.util.ArrayList$Itr.next(ArrayList.java:1052)"
    )


def test_failure_and_family_ids_are_unique():
    result = catalogue()

    assert len({failure.id for failure in result.failure_types}) == len(result.failure_types)
    assert len({family.id for family in result.families}) == len(result.families)


def test_catalogue_has_families_with_two_to_four_members_and_singletons():
    result = catalogue()
    members = Counter(failure.family_id for failure in result.failure_types)
    singleton_count = members.pop(None)

    assert len(result.families) == 12
    assert set(members) == {family.id for family in result.families}
    assert all(2 <= count <= 4 for count in members.values())
    assert singleton_count == 15


def test_every_failure_type_becomes_its_own_tracelite_issue():
    keys = {
        (
            failure.application,
            failure.environment,
            failure.exception_type,
            tuple(method_without_line(frame) for frame in failure.stack_frames),
        )
        for failure in FAILURE_TYPES
    }

    assert len(keys) == len(FAILURE_TYPES)


def test_every_failure_type_has_positive_rate_and_frames():
    for failure in FAILURE_TYPES:
        assert failure.base_rate_per_hour > 0
        assert failure.stack_frames


def test_default_catalogue_draws_ten_incidents_after_warm_up_without_overlap():
    incidents = sorted(catalogue().incidents, key=lambda incident: incident.start)

    assert len(incidents) == INCIDENT_COUNT
    assert incidents[0].start >= START + timedelta(hours=WARM_UP_HOURS)
    assert incidents[-1].end <= END
    for earlier, later in zip(incidents, incidents[1:], strict=False):
        assert earlier.end <= later.start


def test_incident_durations_and_multipliers_stay_in_range():
    for incident in catalogue().incidents:
        hours = (incident.end - incident.start) / timedelta(hours=1)
        assert 1 <= hours <= MAX_INCIDENT_HOURS
        assert MIN_MULTIPLIER <= incident.multiplier <= MAX_MULTIPLIER


def test_some_incidents_are_small_on_purpose():
    small = [
        incident
        for incident in catalogue().incidents
        if incident.end - incident.start <= timedelta(hours=MAX_HARD_INCIDENT_HOURS)
        and incident.multiplier <= MAX_HARD_MULTIPLIER
    ]

    assert len(small) >= HARD_INCIDENT_COUNT


def test_incident_failures_belong_to_the_incident_series():
    failures = {failure.id: failure for failure in FAILURE_TYPES}

    for incident in catalogue().incidents:
        assert incident.failure_ids
        for failure_id in incident.failure_ids:
            failure = failures[failure_id]
            assert failure.application == incident.application
            assert failure.environment == incident.environment


def test_incident_on_family_member_includes_family_members_of_same_series():
    for incident in catalogue().incidents:
        first = next(failure for failure in FAILURE_TYPES if failure.id == incident.failure_ids[0])
        if first.family_id is None:
            assert incident.failure_ids == (first.id,)
        else:
            assert {
                failure.family_id for failure in FAILURE_TYPES if failure.id in incident.failure_ids
            } == {first.family_id}


def test_same_seed_gives_same_catalogue_and_different_seed_differs():
    assert catalogue(seed=1) == catalogue(seed=1)
    assert catalogue(seed=1).incidents != catalogue(seed=2).incidents


def test_default_catalogue_rejects_period_shorter_than_minimum():
    with pytest.raises(ValueError, match="at least"):
        default_catalogue(1, START, START + timedelta(days=MINIMUM_DAYS - 1))


def test_default_catalogue_accepts_minimum_period():
    result = default_catalogue(1, START, START + timedelta(days=MINIMUM_DAYS))

    assert len(result.incidents) == INCIDENT_COUNT


def test_incident_covers_only_its_failures_inside_its_window():
    incident = Incident(
        id="incident-01",
        application="checkout-service",
        environment="production",
        failure_ids=("a",),
        start=START,
        end=START + timedelta(hours=2),
        multiplier=5.0,
    )

    assert incident.covers("a", START)
    assert incident.covers("a", START + timedelta(hours=1))
    assert not incident.covers("a", START + timedelta(hours=2))
    assert not incident.covers("a", START - timedelta(hours=1))
    assert not incident.covers("b", START)


def test_hour_starts_lists_every_hour_in_utc():
    berlin_summer = timezone(timedelta(hours=2))
    start = datetime(2026, 9, 1, 2, tzinfo=berlin_summer)

    hours = hour_starts(start, start + timedelta(hours=3))

    assert hours == [datetime(2026, 9, 1, hour, tzinfo=UTC) for hour in range(3)]
    assert all(hour.tzinfo == UTC for hour in hours)


def test_hour_starts_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone-aware"):
        hour_starts(datetime(2026, 9, 1), datetime(2026, 9, 2))


def test_hour_starts_rejects_partial_hours():
    with pytest.raises(ValueError, match="whole hours"):
        hour_starts(START + timedelta(minutes=30), END)


def test_hour_starts_rejects_end_not_after_start():
    with pytest.raises(ValueError, match="after start"):
        hour_starts(START, START)
