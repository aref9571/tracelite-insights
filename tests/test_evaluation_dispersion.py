import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from insights.evaluation.dispersion import dispersion, hour_of_week
from insights.evaluation.incidents import IncidentWindow
from insights.series import Series

START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
KEY = ("checkout-service", "production")


def hour(offset):
    return START + timedelta(hours=offset)


def make_series(counts):
    hours = [hour(index) for index in range(len(counts))]
    return Series(key=KEY, hours=hours, counts=np.array(counts, dtype=np.int64))


def test_hour_of_week_counts_from_monday_midnight():
    monday_midnight = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)

    assert hour_of_week(monday_midnight) == 0
    assert hour_of_week(monday_midnight + timedelta(hours=14)) == 14
    assert hour_of_week(monday_midnight + timedelta(days=6, hours=23)) == 167


def test_whole_series_ratio_is_sample_variance_over_mean():
    result = dispersion(make_series([2, 4, 6]), [])

    assert result.whole_series_ratio == pytest.approx(4 / 4)
    assert result.mean_count == pytest.approx(4.0)


def test_a_repeating_weekly_pattern_has_a_same_hour_ratio_of_zero():
    pattern = [1, 5, 9, 2] * 6
    counts = pattern * 14

    result = dispersion(make_series(counts), [])

    assert result.same_hour_ratio == pytest.approx(0.0)
    assert result.whole_series_ratio > 1.0


def test_same_hour_ratio_uses_the_variance_inside_each_hour_of_the_week():
    week = 168
    counts = [10] * week + [14] * week

    result = dispersion(make_series(counts), [])

    assert result.same_hour_ratio == pytest.approx(8 / 12)


def test_incident_hours_are_left_out_of_the_same_hour_ratio_and_the_mean():
    week = 168
    counts = [10] * week + [10] * week + [10] * week
    counts[week + 5] = 500
    window = IncidentWindow(
        id="incident-01", series_key=KEY, start=hour(week + 5), end=hour(week + 6)
    )

    result = dispersion(make_series(counts), [window])

    assert result.same_hour_ratio == pytest.approx(0.0)
    assert result.mean_count == pytest.approx(10.0)
    assert result.whole_series_ratio > 1.0


def test_a_series_shorter_than_two_weeks_has_no_same_hour_ratio():
    result = dispersion(make_series([3] * 100), [])

    assert math.isnan(result.same_hour_ratio)


def test_a_series_of_zeros_has_no_ratio():
    result = dispersion(make_series([0] * 400), [])

    assert math.isnan(result.whole_series_ratio)
    assert math.isnan(result.same_hour_ratio)
    assert result.mean_count == 0.0


def test_a_series_that_is_one_long_incident_has_no_mean():
    window = IncidentWindow(id="incident-01", series_key=KEY, start=hour(0), end=hour(400))

    result = dispersion(make_series([3] * 400), [window])

    assert math.isnan(result.mean_count)
    assert math.isnan(result.same_hour_ratio)


def test_poisson_counts_have_a_same_hour_ratio_near_one():
    rng = np.random.default_rng(7)
    counts = rng.poisson(12, size=168 * 40)

    result = dispersion(make_series(counts), [])

    assert result.same_hour_ratio == pytest.approx(1.0, abs=0.1)


def test_counts_with_extra_noise_have_a_same_hour_ratio_above_one():
    rng = np.random.default_rng(7)
    rates = rng.gamma(shape=4.0, scale=3.0, size=168 * 40)
    counts = rng.poisson(rates)

    result = dispersion(make_series(counts), [])

    assert result.same_hour_ratio > 2.0
