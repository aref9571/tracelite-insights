from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np
import pytest

from insights.detection.base import Detector, find_anomalies
from insights.detection.zscore import RollingZScore
from insights.domain import Anomaly
from insights.series import Series

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
ISSUE_ID = UUID("6f1c2a52-6a2e-4f4e-9f63-2b9f0c3c1d11")


def make_series(key, counts):
    hours = [START + timedelta(hours=index) for index in range(len(counts))]
    return Series(key=key, hours=hours, counts=np.array(counts, dtype=np.int64))


def spike_series(key, spike_hour, spike=40, hours=30):
    counts = [5] * hours
    counts[spike_hour] = spike
    return make_series(key, counts)


def test_rolling_zscore_fits_the_detector_protocol():
    detector: Detector = RollingZScore()

    assert detector.name == "zscore"


def test_find_anomalies_builds_one_anomaly_per_alert_hour():
    series = spike_series(("checkout-service", "production"), spike_hour=26)

    anomalies = find_anomalies(RollingZScore(window_hours=24), [series], threshold=4)

    assert anomalies == [
        Anomaly(
            detector="zscore",
            series_key=("checkout-service", "production"),
            hour=START + timedelta(hours=26),
            count=40,
            expected=5.0,
            score=35.0,
        )
    ]


def test_find_anomalies_covers_every_series_in_order():
    first = spike_series(("auth-service", "production"), spike_hour=25)
    second = spike_series(("catalog-service", "staging"), spike_hour=27)
    quiet = make_series(("checkout-service", "production"), [5] * 30)

    anomalies = find_anomalies(RollingZScore(window_hours=24), [first, quiet, second], 4)

    assert [(anomaly.series_key, anomaly.hour.hour) for anomaly in anomalies] == [
        (("auth-service", "production"), 1),
        (("catalog-service", "staging"), 3),
    ]


def test_find_anomalies_turns_issue_ids_into_text_keys():
    series = spike_series((ISSUE_ID,), spike_hour=26)

    anomalies = find_anomalies(RollingZScore(window_hours=24), [series], threshold=4)

    assert anomalies[0].series_key == (str(ISSUE_ID),)


def test_find_anomalies_returns_plain_python_numbers():
    series = spike_series(("checkout-service", "production"), spike_hour=26)

    anomaly = find_anomalies(RollingZScore(window_hours=24), [series], threshold=4)[0]

    assert type(anomaly.count) is int
    assert type(anomaly.expected) is float
    assert type(anomaly.score) is float


def test_find_anomalies_without_alerts_or_series_is_empty():
    quiet = make_series(("checkout-service", "production"), [5] * 30)

    assert find_anomalies(RollingZScore(window_hours=24), [quiet], threshold=4) == []
    assert find_anomalies(RollingZScore(window_hours=24), [], threshold=4) == []


def test_anomaly_needs_an_hour_with_a_time_zone():
    with pytest.raises(ValueError):
        Anomaly(
            detector="zscore",
            series_key=("checkout-service", "production"),
            hour=datetime(2026, 10, 1, 0, 0),
            count=40,
            expected=5.0,
            score=35.0,
        )
