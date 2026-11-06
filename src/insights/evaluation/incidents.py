import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from insights.detection.base import Detector
from insights.series import Series

ONE_HOUR = timedelta(hours=1)
DETECTION_TOLERANCE = ONE_HOUR
HOURS_PER_WEEK = 168


@dataclass(frozen=True)
class IncidentWindow:
    id: str
    series_key: tuple[str, ...]
    start: datetime
    end: datetime

    def contains(self, hour: datetime) -> bool:
        return self.start <= hour < self.end

    def detects(self, hour: datetime) -> bool:
        return self.start <= hour < self.end + DETECTION_TOLERANCE


@dataclass(frozen=True)
class GroundTruth:
    start: datetime
    end: datetime
    incidents: tuple[IncidentWindow, ...]


@dataclass(frozen=True)
class HourMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        return safe_ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        return safe_ratio(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float:
        return safe_ratio(2 * self.precision * self.recall, self.precision + self.recall)


@dataclass(frozen=True)
class IncidentMetrics:
    incidents: int
    detected: int
    false_alerts: int
    monitored_hours: int
    delays_hours: tuple[float, ...]

    @property
    def incident_recall(self) -> float:
        return safe_ratio(self.detected, self.incidents)

    @property
    def false_alerts_per_week(self) -> float:
        return safe_ratio(self.false_alerts, self.monitored_hours / HOURS_PER_WEEK)

    @property
    def median_delay_hours(self) -> float:
        if not self.delays_hours:
            return float("nan")
        return float(np.median(self.delays_hours))


@dataclass(frozen=True)
class Evaluation:
    hours: HourMetrics
    incidents: IncidentMetrics


@dataclass(frozen=True)
class SweepPoint:
    threshold: float
    evaluation: Evaluation


def read_truth(path: Path) -> GroundTruth:
    document = json.loads(path.read_text(encoding="utf-8"))
    incidents = tuple(
        IncidentWindow(
            id=incident["id"],
            series_key=(incident["application"], incident["environment"]),
            start=datetime.fromisoformat(incident["start"]),
            end=datetime.fromisoformat(incident["end"]),
        )
        for incident in document["incidents"]
    )
    return GroundTruth(
        start=datetime.fromisoformat(document["start"]),
        end=datetime.fromisoformat(document["end"]),
        incidents=incidents,
    )


def hour_labels(series: Series, incidents: Sequence[IncidentWindow]) -> np.ndarray:
    own_incidents = [incident for incident in incidents if incident.series_key == series.key]
    return np.array(
        [any(incident.contains(hour) for incident in own_incidents) for hour in series.hours],
        dtype=bool,
    )


def hour_metrics(alerts: np.ndarray, labels: np.ndarray) -> HourMetrics:
    alerts = np.asarray(alerts, dtype=bool)
    labels = np.asarray(labels, dtype=bool)
    if alerts.shape != labels.shape:
        raise ValueError("alerts and labels must have the same shape")
    return HourMetrics(
        true_positives=int(np.sum(alerts & labels)),
        false_positives=int(np.sum(alerts & ~labels)),
        false_negatives=int(np.sum(~alerts & labels)),
    )


def incident_metrics(
    alert_hours: Mapping[tuple[str, ...], Sequence[datetime]],
    incidents: Sequence[IncidentWindow],
    monitored_hours: int,
) -> IncidentMetrics:
    delays = []
    for incident in incidents:
        delay = detection_delay_hours(incident, alert_hours.get(incident.series_key, []))
        if delay is not None:
            delays.append(delay)
    return IncidentMetrics(
        incidents=len(incidents),
        detected=len(delays),
        false_alerts=count_false_alerts(alert_hours, incidents),
        monitored_hours=monitored_hours,
        delays_hours=tuple(delays),
    )


def detection_delay_hours(incident: IncidentWindow, hours: Sequence[datetime]) -> float | None:
    hits = [hour for hour in hours if incident.detects(hour)]
    if not hits:
        return None
    return (min(hits) - incident.start) / ONE_HOUR


def count_false_alerts(
    alert_hours: Mapping[tuple[str, ...], Sequence[datetime]],
    incidents: Sequence[IncidentWindow],
) -> int:
    false_alerts = 0
    for series_key, hours in alert_hours.items():
        own_incidents = [incident for incident in incidents if incident.series_key == series_key]
        false_alerts += sum(
            1 for hour in hours if not any(incident.detects(hour) for incident in own_incidents)
        )
    return false_alerts


def evaluate_detector(
    detector: Detector, series_list: Sequence[Series], truth: GroundTruth, threshold: float
) -> Evaluation:
    all_alerts = []
    all_labels = []
    alert_hours = {}
    monitored_hours = 0
    for series in series_list:
        alerts = detector.alerts(series.counts, threshold)
        all_alerts.append(alerts)
        all_labels.append(hour_labels(series, truth.incidents))
        alert_hours[series.key] = [series.hours[index] for index in np.flatnonzero(alerts)]
        scored_hours = int(np.sum(~np.isnan(detector.score(series.counts))))
        monitored_hours = max(monitored_hours, scored_hours)
    return Evaluation(
        hours=hour_metrics(concatenate(all_alerts), concatenate(all_labels)),
        incidents=incident_metrics(alert_hours, truth.incidents, monitored_hours),
    )


def threshold_sweep(
    detector: Detector,
    series_list: Sequence[Series],
    truth: GroundTruth,
    thresholds: Sequence[float],
) -> list[SweepPoint]:
    return [
        SweepPoint(threshold, evaluate_detector(detector, series_list, truth, threshold))
        for threshold in thresholds
    ]


def best_threshold(points: Sequence[SweepPoint]) -> SweepPoint:
    if not points:
        raise ValueError("a sweep needs at least one threshold")
    best = points[0]
    for point in points[1:]:
        if point.evaluation.hours.f1 > best.evaluation.hours.f1:
            best = point
    return best


def concatenate(arrays: list[np.ndarray]) -> np.ndarray:
    if not arrays:
        return np.array([], dtype=bool)
    return np.concatenate(arrays)


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
