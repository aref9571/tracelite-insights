from collections.abc import Iterable
from typing import Protocol

import numpy as np

from insights.domain import Anomaly
from insights.series import Series


class Detector(Protocol):
    name: str

    def expected(self, counts: np.ndarray) -> np.ndarray: ...

    def score(self, counts: np.ndarray) -> np.ndarray: ...

    def alerts(self, counts: np.ndarray, threshold: float) -> np.ndarray: ...


def find_anomalies(
    detector: Detector, series_list: Iterable[Series], threshold: float
) -> list[Anomaly]:
    anomalies = []
    for series in series_list:
        anomalies.extend(series_anomalies(detector, series, threshold))
    return anomalies


def series_anomalies(detector: Detector, series: Series, threshold: float) -> list[Anomaly]:
    expected = detector.expected(series.counts)
    scores = detector.score(series.counts)
    alert_hours = np.flatnonzero(detector.alerts(series.counts, threshold))
    series_key = tuple(str(part) for part in series.key)
    return [
        Anomaly(
            detector=detector.name,
            series_key=series_key,
            hour=series.hours[index],
            count=int(series.counts[index]),
            expected=float(expected[index]),
            score=float(scores[index]),
        )
        for index in alert_hours
    ]


def as_float_series(counts: np.ndarray) -> np.ndarray:
    values = np.asarray(counts, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("counts must be a one-dimensional array")
    return values
