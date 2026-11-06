from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from insights.detection.base import Detector, find_anomalies
from insights.detection.seasonal import HOURS_PER_WEEK, SeasonalRobust
from insights.detection.zscore import RollingZScore
from insights.series import Series

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
WEEKS = 5
PEAK_HOURS = (14, 15)
QUIET_HOUR = 3


def daily_cycle_series(seed=7, weeks=WEEKS):
    hours = np.arange(weeks * HOURS_PER_WEEK)
    rates = np.where(np.isin(hours % 24, PEAK_HOURS), 80.0, 10.0)
    return np.random.default_rng(seed).poisson(rates)


def make_series(key, counts):
    hours = [START + timedelta(hours=index) for index in range(len(counts))]
    return Series(key=key, hours=hours, counts=np.asarray(counts, dtype=np.int64))


def one_week_history(first_values, current):
    counts = np.zeros(HOURS_PER_WEEK + 2, dtype=np.int64)
    counts[: len(first_values)] = first_values
    counts[-1] = current
    return counts


def test_seasonal_robust_fits_the_detector_protocol():
    detector: Detector = SeasonalRobust()

    assert detector.name == "seasonal"
    assert detector.warm_up_hours == 3 * HOURS_PER_WEEK + 1


def test_strong_daily_cycle_without_incident_gives_far_fewer_alerts_than_zscore():
    counts = daily_cycle_series()
    seasonal = SeasonalRobust()
    scored = slice(seasonal.warm_up_hours, None)

    seasonal_alerts = int(seasonal.alerts(counts, threshold=3)[scored].sum())
    zscore_alerts = int(RollingZScore().alerts(counts, threshold=3)[scored].sum())

    assert zscore_alerts >= 14
    assert seasonal_alerts * 5 <= zscore_alerts


def test_spike_at_a_usually_quiet_hour_is_caught():
    counts = daily_cycle_series()
    spike_hour = (WEEKS - 1) * HOURS_PER_WEEK + 2 * 24 + QUIET_HOUR
    counts[spike_hour] = 40

    seasonal_alerts = SeasonalRobust().alerts(counts, threshold=4)
    zscore_alerts = RollingZScore().alerts(counts, threshold=4)

    assert seasonal_alerts[spike_hour]
    assert not zscore_alerts[spike_hour]


def test_outlier_in_a_previous_week_hardly_moves_the_expected_value():
    counts = np.full(4 * HOURS_PER_WEEK, 5, dtype=np.int64)
    current = 3 * HOURS_PER_WEEK + 10
    counts[current - HOURS_PER_WEEK] = 500
    detector = SeasonalRobust()

    assert detector.expected(counts)[current] == 5.0
    assert detector.score(counts)[current] == pytest.approx(0.0)
    assert RollingZScore().expected(counts)[current] > 7.0


def test_expected_value_and_spread_by_hand():
    counts = one_week_history([4, 10, 7], current=20)
    detector = SeasonalRobust(weeks=1, neighbour_hours=1)

    median, spread = detector.baseline(counts)

    assert median[-1] == 7.0
    assert spread[-1] == pytest.approx(3 * 1.4826)
    assert detector.score(counts)[-1] == pytest.approx(13 / (3 * 1.4826))


def test_spread_is_floored_at_the_square_root_of_the_expected_value():
    counts = one_week_history([16, 16, 16], current=28)

    score = SeasonalRobust(weeks=1, neighbour_hours=1).score(counts)

    assert score[-1] == pytest.approx(3.0)


def test_score_exactly_at_the_threshold_alerts():
    counts = one_week_history([16, 16, 16], current=28)

    alerts = SeasonalRobust(weeks=1, neighbour_hours=1).alerts(counts, threshold=3)

    assert np.flatnonzero(alerts).tolist() == [len(counts) - 1]


def test_spread_is_floored_at_one_for_a_silent_series():
    counts = one_week_history([0, 0, 0], current=3)

    score = SeasonalRobust(weeks=1, neighbour_hours=1).score(counts)

    assert score[-1] == pytest.approx(3.0)


def test_without_neighbours_only_the_same_hour_of_earlier_weeks_counts():
    counts = np.zeros(2 * HOURS_PER_WEEK + 1, dtype=np.int64)
    counts[[0, HOURS_PER_WEEK]] = [4, 6]
    counts[[1, HOURS_PER_WEEK + 1]] = 100

    expected = SeasonalRobust(weeks=2, neighbour_hours=0).expected(counts)

    assert expected[-1] == 5.0


def test_warm_up_hours_have_nan_scores_and_never_alert():
    counts = daily_cycle_series(weeks=4)
    counts[100] = 500
    detector = SeasonalRobust()

    scores = detector.score(counts)
    alerts = detector.alerts(counts, threshold=4)

    assert np.isnan(scores[: detector.warm_up_hours]).all()
    assert not np.isnan(scores[detector.warm_up_hours :]).any()
    assert not alerts[: detector.warm_up_hours].any()


def test_series_not_longer_than_the_warm_up_is_all_warm_up():
    detector = SeasonalRobust(weeks=1)
    counts = np.full(detector.warm_up_hours, 5, dtype=np.int64)

    assert np.isnan(detector.score(counts)).all()
    assert not detector.alerts(counts, threshold=1).any()


def test_baseline_uses_neither_the_current_hour_nor_later_hours():
    counts = daily_cycle_series()
    current = 4 * HOURS_PER_WEEK + 50
    detector = SeasonalRobust()
    before = detector.expected(counts)[current]

    counts[current:] = 1000

    assert detector.expected(counts)[current] == before


def test_score_does_not_change_the_input():
    counts = daily_cycle_series()
    original = counts.copy()

    SeasonalRobust().score(counts)

    assert np.array_equal(counts, original)


def test_find_anomalies_reports_the_seasonal_expected_value():
    counts = np.full(HOURS_PER_WEEK + 3, 4, dtype=np.int64)
    counts[-1] = 20
    series = make_series(("auth-service", "staging"), counts)

    anomalies = find_anomalies(SeasonalRobust(weeks=1), [series], threshold=4)

    assert len(anomalies) == 1
    assert anomalies[0].detector == "seasonal"
    assert anomalies[0].hour == series.hours[-1]
    assert anomalies[0].expected == 4.0
    assert anomalies[0].score == pytest.approx(8.0)


@pytest.mark.parametrize(
    ("weeks", "neighbour_hours"),
    [(0, 1), (-1, 1), (3, -1), (3, HOURS_PER_WEEK)],
)
def test_invalid_settings_are_refused(weeks, neighbour_hours):
    with pytest.raises(ValueError):
        SeasonalRobust(weeks=weeks, neighbour_hours=neighbour_hours)


def test_counts_must_be_one_dimensional():
    with pytest.raises(ValueError, match="one-dimensional"):
        SeasonalRobust().score(np.zeros((2, 600)))
