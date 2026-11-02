import numpy as np
import pytest

from insights.detection.zscore import RollingZScore

WINDOW = 24


def flat_series(hours, value=5):
    return np.full(hours, value, dtype=np.int64)


def test_flat_series_with_one_big_spike_gives_exactly_one_alert_at_the_spike():
    counts = flat_series(72)
    counts[50] = 40

    alerts = RollingZScore(window_hours=WINDOW).alerts(counts, threshold=4)

    assert np.flatnonzero(alerts).tolist() == [50]


def test_warm_up_hours_have_nan_scores_and_never_alert():
    counts = flat_series(30)
    counts[3] = 100
    detector = RollingZScore(window_hours=WINDOW)

    scores = detector.score(counts)
    alerts = detector.alerts(counts, threshold=4)

    assert np.isnan(scores[:WINDOW]).all()
    assert not np.isnan(scores[WINDOW:]).any()
    assert not alerts[:WINDOW].any()


def test_series_not_longer_than_the_window_is_all_warm_up():
    detector = RollingZScore(window_hours=WINDOW)

    scores = detector.score(flat_series(WINDOW))

    assert np.isnan(scores).all()
    assert not detector.alerts(flat_series(WINDOW), threshold=4).any()


def test_counts_below_min_count_never_alert_even_with_a_high_score():
    counts = np.zeros(48, dtype=np.int64)
    counts[40] = 2
    detector = RollingZScore(window_hours=WINDOW, min_count=3)

    assert detector.score(counts)[40] == pytest.approx(2.0)
    assert not detector.alerts(counts, threshold=2).any()


def test_count_equal_to_min_count_can_alert():
    counts = np.zeros(48, dtype=np.int64)
    counts[40] = 3

    alerts = RollingZScore(window_hours=WINDOW, min_count=3).alerts(counts, threshold=3)

    assert np.flatnonzero(alerts).tolist() == [40]


def test_score_equal_to_threshold_alerts():
    counts = flat_series(30)
    counts[WINDOW] = 9

    alerts = RollingZScore(window_hours=WINDOW).alerts(counts, threshold=4)

    assert alerts[WINDOW]


def test_window_excludes_the_current_hour_so_a_spike_does_not_inflate_its_own_baseline():
    counts = flat_series(30)
    counts[WINDOW] = 50
    detector = RollingZScore(window_hours=WINDOW)

    assert detector.expected(counts)[WINDOW] == pytest.approx(5.0)
    assert detector.score(counts)[WINDOW] == pytest.approx(45.0)


def test_baseline_uses_exactly_the_previous_window_hours():
    counts = np.arange(10, dtype=np.int64)
    detector = RollingZScore(window_hours=4)

    expected = detector.expected(counts)

    assert expected[4] == pytest.approx(np.mean([0, 1, 2, 3]))
    assert expected[9] == pytest.approx(np.mean([5, 6, 7, 8]))


def test_score_divides_by_the_standard_deviation_of_the_previous_hours():
    counts = np.array([2, 8, 2, 8, 20], dtype=np.int64)

    score = RollingZScore(window_hours=4).score(counts)[4]

    assert score == pytest.approx((20 - 5) / 3)


def test_standard_deviation_is_floored_at_one_for_quiet_series():
    counts = np.array([0, 0, 0, 0, 3], dtype=np.int64)

    score = RollingZScore(window_hours=4).score(counts)[4]

    assert score == pytest.approx(3.0)


def test_hours_after_a_spike_do_not_alert_while_the_spike_is_in_their_window():
    counts = flat_series(72)
    counts[30] = 60
    counts[31:40] = 8

    alerts = RollingZScore(window_hours=WINDOW).alerts(counts, threshold=4)

    assert np.flatnonzero(alerts).tolist() == [30]


def test_score_and_alerts_have_one_value_per_hour():
    counts = flat_series(50)
    detector = RollingZScore(window_hours=WINDOW)

    assert detector.score(counts).shape == (50,)
    assert detector.alerts(counts, threshold=4).dtype == np.bool_
    assert detector.alerts(counts, threshold=4).shape == (50,)


def test_score_does_not_change_the_input_counts():
    counts = flat_series(30)
    counts[WINDOW] = 50
    original = counts.copy()

    RollingZScore(window_hours=WINDOW).score(counts)

    assert np.array_equal(counts, original)
    assert counts.dtype == np.int64


def test_empty_series_gives_empty_scores_and_alerts():
    counts = np.array([], dtype=np.int64)
    detector = RollingZScore()

    assert detector.score(counts).shape == (0,)
    assert detector.alerts(counts, threshold=4).shape == (0,)


def test_default_window_is_one_week_and_default_min_count_is_three():
    detector = RollingZScore()

    assert detector.window_hours == 168
    assert detector.min_count == 3
    assert detector.name == "zscore"


@pytest.mark.parametrize(
    ("window_hours", "min_count"),
    [(0, 3), (-1, 3), (24, -1)],
)
def test_invalid_settings_are_rejected(window_hours, min_count):
    with pytest.raises(ValueError):
        RollingZScore(window_hours=window_hours, min_count=min_count)


def test_counts_must_be_one_dimensional():
    with pytest.raises(ValueError, match="one-dimensional"):
        RollingZScore(window_hours=2).score(np.zeros((3, 3)))
