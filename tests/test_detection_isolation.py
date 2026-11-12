from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pytest

from insights.detection.base import Detector, find_anomalies
from insights.detection.isolation import HOURS_PER_DAY, IsolationForestDetector
from insights.series import Series

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
WEEKS = 5
PEAK_HOURS = (14, 15)
TRAINING_HOURS = 14 * HOURS_PER_DAY
SPIKE_HOUR = 4 * 7 * HOURS_PER_DAY + 2 * HOURS_PER_DAY + 3


def daily_cycle_counts(seed=7, weeks=WEEKS):
    hours = np.arange(weeks * 7 * HOURS_PER_DAY)
    rates = np.where(np.isin(hours % HOURS_PER_DAY, PEAK_HOURS), 60.0, 10.0)
    return np.random.default_rng(seed).poisson(rates)


def make_series(counts):
    hours = [START + timedelta(hours=index) for index in range(len(counts))]
    return Series(
        key=("shop", "production"), hours=hours, counts=np.asarray(counts, dtype=np.int64)
    )


def test_isolation_forest_fits_the_detector_protocol():
    detector: Detector = IsolationForestDetector(START)

    assert detector.name == "isolation"
    assert detector.training_hours == TRAINING_HOURS


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"start": datetime(2026, 10, 1)}, "time zone"),
        ({"start": datetime(2026, 10, 1, 0, 30, tzinfo=UTC)}, "whole hour"),
        ({"training_days": 1}, "training_days"),
        ({"trees": 0}, "trees"),
    ],
)
def test_isolation_forest_refuses_invalid_settings(changes, message):
    settings = {"start": START, **changes}

    with pytest.raises(ValueError, match=message):
        IsolationForestDetector(**settings)


def test_training_hours_have_no_score_and_never_alert():
    counts = daily_cycle_counts()
    counts[TRAINING_HOURS - 5] = 500
    detector = IsolationForestDetector(START)

    scores = detector.score(counts)
    alerts = detector.alerts(counts, threshold=-1.0)

    assert np.isnan(scores[:TRAINING_HOURS]).all()
    assert not np.isnan(scores[TRAINING_HOURS:]).any()
    assert not alerts[:TRAINING_HOURS].any()


def test_series_not_longer_than_the_training_window_is_never_scored():
    detector = IsolationForestDetector(START)
    counts = daily_cycle_counts()[:TRAINING_HOURS]

    assert np.isnan(detector.score(counts)).all()
    assert np.isnan(detector.expected(counts)).all()
    assert not detector.alerts(counts, threshold=-1.0).any()


def test_clear_spike_scores_higher_than_normal_hours():
    counts = daily_cycle_counts()
    counts[SPIKE_HOUR] = 120
    detector = IsolationForestDetector(START)

    scores = detector.score(counts)

    scored = np.arange(TRAINING_HOURS, len(counts))
    normal = scored[scored != SPIKE_HOUR]
    same_hour_of_day = normal[normal % HOURS_PER_DAY == SPIKE_HOUR % HOURS_PER_DAY]
    assert scores[SPIKE_HOUR] > scores[same_hour_of_day].max()
    assert scores[SPIKE_HOUR] > np.median(scores[normal]) + 0.2


def test_spike_is_flagged_and_a_constant_series_is_not():
    spiked = daily_cycle_counts()
    spiked[SPIKE_HOUR] = 120
    constant = np.full(len(spiked), 10)
    detector = IsolationForestDetector(START)

    assert SPIKE_HOUR in np.flatnonzero(detector.alerts(spiked, threshold=0.2))
    assert not detector.alerts(constant, threshold=0.2).any()


def test_a_sudden_drop_has_a_high_score_but_raises_no_alert():
    counts = daily_cycle_counts()
    drop_hour = 4 * 7 * HOURS_PER_DAY + 2 * HOURS_PER_DAY + PEAK_HOURS[0]
    counts[drop_hour] = 0
    detector = IsolationForestDetector(START)

    assert detector.score(counts)[drop_hour] >= 0.1
    assert not detector.alerts(counts, threshold=0.1)[drop_hour]


def test_scores_are_the_same_for_the_same_input_and_seed():
    counts = daily_cycle_counts()

    first = IsolationForestDetector(START).score(counts)
    second = IsolationForestDetector(START).score(counts)

    np.testing.assert_array_equal(first, second)


def test_a_different_random_state_changes_the_scores():
    counts = daily_cycle_counts()

    first = IsolationForestDetector(START, random_state=0).score(counts)
    second = IsolationForestDetector(START, random_state=1).score(counts)

    assert not np.array_equal(first[TRAINING_HOURS:], second[TRAINING_HOURS:])


def test_changing_a_returned_score_does_not_change_later_scores():
    counts = daily_cycle_counts()
    detector = IsolationForestDetector(START)

    first = detector.score(counts)
    first[:] = 99.0

    assert np.nanmax(detector.score(counts)) < 1.0


def test_expected_is_the_training_median_of_the_same_hour_and_day_type():
    counts = daily_cycle_counts()
    detector = IsolationForestDetector(START)

    expected = detector.expected(counts)

    scored_hours = np.arange(TRAINING_HOURS, len(counts))
    peak = expected[scored_hours][np.isin(scored_hours % HOURS_PER_DAY, PEAK_HOURS)]
    quiet = expected[scored_hours][~np.isin(scored_hours % HOURS_PER_DAY, PEAK_HOURS)]
    assert np.isnan(expected[:TRAINING_HOURS]).all()
    assert peak.min() > 45
    assert quiet.max() < 15


def test_hour_of_day_follows_the_start_hour():
    detector = IsolationForestDetector(datetime(2026, 10, 1, 22, 0, tzinfo=UTC))

    assert detector.hour_of_day(5).tolist() == [22, 23, 0, 1, 2]


def test_weekend_flag_follows_the_start_day():
    saturday = datetime(2026, 10, 3, 22, 0, tzinfo=UTC)
    detector = IsolationForestDetector(saturday)

    weekend = detector.is_weekend(2 * HOURS_PER_DAY + 4)

    assert weekend[:26].all()
    assert not weekend[26:].any()


def test_start_in_another_time_zone_is_converted_to_utc():
    local_start = datetime(2026, 10, 4, 1, 0, tzinfo=timezone(timedelta(hours=2)))
    detector = IsolationForestDetector(local_start)

    assert detector.hour_of_day(2).tolist() == [23, 0]
    assert detector.is_weekend(2).all()


def test_previous_mean_uses_the_last_day_and_starts_with_zero():
    detector = IsolationForestDetector(START)
    values = np.arange(1.0, 31.0)

    previous = detector.previous_mean(values)

    assert previous[0] == 0.0
    assert previous[1] == 1.0
    assert previous[3] == 2.0
    assert previous[29] == values[5:29].mean()


def test_find_anomalies_reports_the_flagged_spike_hour():
    counts = daily_cycle_counts()
    counts[SPIKE_HOUR] = 120
    series = make_series(counts)

    anomalies = find_anomalies(IsolationForestDetector(START), [series], threshold=0.2)

    spike = next(anomaly for anomaly in anomalies if anomaly.hour == series.hours[SPIKE_HOUR])
    assert spike.detector == "isolation"
    assert spike.count == 120
    assert spike.score >= 0.2
    assert spike.expected < 20
