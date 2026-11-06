import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from insights.detection.zscore import RollingZScore
from insights.evaluation.incidents import (
    Evaluation,
    GroundTruth,
    HourMetrics,
    IncidentMetrics,
    IncidentWindow,
    SweepPoint,
    best_threshold,
    evaluate_detector,
    hour_labels,
    hour_metrics,
    incident_metrics,
    read_truth,
    threshold_sweep,
)
from insights.series import Series

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
CHECKOUT = ("checkout-service", "production")
AUTH = ("auth-service", "staging")


def hour(offset):
    return START + timedelta(hours=offset)


def window(series_key, start_hour, end_hour, incident_id="incident-01"):
    return IncidentWindow(
        id=incident_id, series_key=series_key, start=hour(start_hour), end=hour(end_hour)
    )


def make_series(key, counts):
    hours = [hour(index) for index in range(len(counts))]
    return Series(key=key, hours=hours, counts=np.array(counts, dtype=np.int64))


class FixedAlerts:
    name = "fixed"

    def __init__(self, alerts_by_length, warm_up_hours=0):
        self.alerts_by_length = alerts_by_length
        self.warm_up_hours = warm_up_hours

    def expected(self, counts):
        return np.zeros(len(counts))

    def score(self, counts):
        scores = np.zeros(len(counts))
        scores[: self.warm_up_hours] = np.nan
        return scores

    def alerts(self, counts, threshold):
        alerts = np.zeros(len(counts), dtype=bool)
        alerts[self.alerts_by_length[len(counts)]] = True
        return alerts


def test_two_true_alerts_one_false_alert_and_one_missed_hour_give_two_thirds():
    labels = np.array([True, True, True, False, False])
    alerts = np.array([True, True, False, True, False])

    metrics = hour_metrics(alerts, labels)

    assert metrics == HourMetrics(true_positives=2, false_positives=1, false_negatives=1)
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(2 / 3)


def test_f1_is_the_harmonic_mean_of_precision_and_recall():
    metrics = HourMetrics(true_positives=1, false_positives=0, false_negatives=3)

    assert metrics.precision == 1.0
    assert metrics.recall == 0.25
    assert metrics.f1 == pytest.approx(2 * 1.0 * 0.25 / 1.25)


def test_no_alerts_gives_zero_precision_recall_and_f1():
    metrics = hour_metrics(np.zeros(4, dtype=bool), np.array([True, False, False, False]))

    assert (metrics.precision, metrics.recall, metrics.f1) == (0.0, 0.0, 0.0)


def test_hour_metrics_refuse_alerts_and_labels_of_different_length():
    with pytest.raises(ValueError, match="same shape"):
        hour_metrics(np.zeros(3, dtype=bool), np.zeros(4, dtype=bool))


def test_hour_labels_mark_only_hours_inside_the_series_own_windows():
    series = make_series(CHECKOUT, [0] * 8)
    incidents = [window(CHECKOUT, 2, 4), window(AUTH, 5, 7, "incident-02")]

    labels = hour_labels(series, incidents)

    assert np.flatnonzero(labels).tolist() == [2, 3]


def test_incident_is_detected_by_an_alert_in_the_tolerance_hour_after_its_end():
    incident = window(CHECKOUT, 10, 12)

    metrics = incident_metrics({CHECKOUT: [hour(12)]}, [incident], monitored_hours=168)

    assert metrics.detected == 1
    assert metrics.delays_hours == (2.0,)
    assert metrics.false_alerts == 0


def test_alert_two_hours_after_the_end_is_a_false_alert_and_misses_the_incident():
    incident = window(CHECKOUT, 10, 12)

    metrics = incident_metrics({CHECKOUT: [hour(13)]}, [incident], monitored_hours=168)

    assert metrics.detected == 0
    assert metrics.false_alerts == 1
    assert metrics.incident_recall == 0.0
    assert math.isnan(metrics.median_delay_hours)


def test_alert_before_the_incident_start_does_not_detect_it():
    incident = window(CHECKOUT, 10, 12)

    metrics = incident_metrics({CHECKOUT: [hour(9)]}, [incident], monitored_hours=168)

    assert metrics.detected == 0
    assert metrics.false_alerts == 1


def test_alert_in_another_series_does_not_detect_the_incident():
    incident = window(CHECKOUT, 10, 12)

    metrics = incident_metrics({AUTH: [hour(10)]}, [incident], monitored_hours=168)

    assert metrics.detected == 0
    assert metrics.false_alerts == 1


def test_detection_delay_is_measured_from_the_start_to_the_first_alert():
    incidents = [
        window(CHECKOUT, 10, 14, "incident-01"),
        window(CHECKOUT, 50, 52, "incident-02"),
        window(AUTH, 30, 31, "incident-03"),
    ]
    alert_hours = {CHECKOUT: [hour(12), hour(11), hour(50)], AUTH: [hour(30)]}

    metrics = incident_metrics(alert_hours, incidents, monitored_hours=168)

    assert metrics.detected == 3
    assert metrics.delays_hours == (1.0, 0.0, 0.0)
    assert metrics.median_delay_hours == 0.0


def test_incident_recall_and_false_alerts_per_week_from_hand_counts():
    incidents = [
        window(CHECKOUT, 10, 12, "incident-01"),
        window(CHECKOUT, 100, 101, "incident-02"),
        window(AUTH, 200, 202, "incident-03"),
        window(AUTH, 300, 301, "incident-04"),
    ]
    alert_hours = {CHECKOUT: [hour(10), hour(50), hour(60)], AUTH: [hour(201), hour(250)]}

    metrics = incident_metrics(alert_hours, incidents, monitored_hours=336)

    assert metrics == IncidentMetrics(
        incidents=4, detected=2, false_alerts=3, monitored_hours=336, delays_hours=(0.0, 1.0)
    )
    assert metrics.incident_recall == 0.5
    assert metrics.false_alerts_per_week == 1.5
    assert metrics.median_delay_hours == 0.5


def test_no_monitored_hours_give_zero_false_alerts_per_week():
    metrics = incident_metrics({}, [], monitored_hours=0)

    assert metrics.false_alerts_per_week == 0.0
    assert metrics.incident_recall == 0.0


def test_evaluate_detector_pools_hours_of_all_series():
    checkout = make_series(CHECKOUT, [0] * 10)
    auth = make_series(AUTH, [0] * 10)
    truth = GroundTruth(
        start=hour(0),
        end=hour(10),
        incidents=(window(CHECKOUT, 2, 4, "incident-01"), window(AUTH, 6, 7, "incident-02")),
    )
    detector = FixedAlerts({10: [2, 8]}, warm_up_hours=3)

    evaluation = evaluate_detector(detector, [checkout, auth], truth, threshold=4)

    assert evaluation.hours == HourMetrics(true_positives=1, false_positives=3, false_negatives=2)
    assert evaluation.incidents.detected == 1
    assert evaluation.incidents.false_alerts == 3
    assert evaluation.incidents.monitored_hours == 7


def test_evaluate_detector_counts_an_incident_of_a_missing_series_as_missed():
    checkout = make_series(CHECKOUT, [0] * 10)
    truth = GroundTruth(start=hour(0), end=hour(10), incidents=(window(AUTH, 2, 4),))

    evaluation = evaluate_detector(FixedAlerts({10: []}), [checkout], truth, threshold=4)

    assert evaluation.incidents.incidents == 1
    assert evaluation.incidents.detected == 0


def test_evaluate_detector_with_rolling_zscore_finds_the_labelled_spike():
    counts = [5] * 40
    counts[30] = 50
    counts[31] = 45
    truth = GroundTruth(start=hour(0), end=hour(40), incidents=(window(CHECKOUT, 30, 32),))

    evaluation = evaluate_detector(
        RollingZScore(window_hours=24), [make_series(CHECKOUT, counts)], truth, threshold=4
    )

    assert evaluation.hours.precision == 1.0
    assert evaluation.incidents.detected == 1
    assert evaluation.incidents.median_delay_hours == 0.0
    assert evaluation.incidents.monitored_hours == 16


def test_evaluate_detector_without_series_scores_nothing():
    truth = GroundTruth(start=hour(0), end=hour(10), incidents=())

    evaluation = evaluate_detector(FixedAlerts({}), [], truth, threshold=4)

    assert evaluation.hours == HourMetrics(true_positives=0, false_positives=0, false_negatives=0)
    assert evaluation.incidents.monitored_hours == 0


def sweep_point(threshold, true_positives, false_positives, false_negatives):
    hours = HourMetrics(true_positives, false_positives, false_negatives)
    incidents = IncidentMetrics(
        incidents=0, detected=0, false_alerts=0, monitored_hours=0, delays_hours=()
    )
    return SweepPoint(threshold, Evaluation(hours=hours, incidents=incidents))


def test_threshold_sweep_evaluates_every_threshold_in_order():
    counts = [5] * 40
    counts[30] = 50
    counts[31] = 50
    series = make_series(CHECKOUT, counts)
    truth = GroundTruth(start=hour(0), end=hour(40), incidents=(window(CHECKOUT, 30, 32),))
    detector = RollingZScore(window_hours=24)

    points = threshold_sweep(detector, [series], truth, [2.0, 5.0, 100.0])

    assert [point.threshold for point in points] == [2.0, 5.0, 100.0]
    assert points[1].evaluation == evaluate_detector(detector, [series], truth, 5.0)
    assert [point.evaluation.hours.true_positives for point in points] == [2, 1, 0]


def test_threshold_sweep_without_thresholds_is_empty():
    truth = GroundTruth(start=hour(0), end=hour(10), incidents=())

    assert threshold_sweep(FixedAlerts({}), [], truth, []) == []


def test_best_threshold_has_the_highest_hour_level_f1():
    points = [sweep_point(2.0, 2, 8, 0), sweep_point(3.0, 2, 1, 0), sweep_point(4.0, 1, 0, 1)]

    assert best_threshold(points).threshold == 3.0


def test_best_threshold_keeps_the_first_of_equal_f1_values():
    points = [sweep_point(2.0, 0, 3, 2), sweep_point(2.5, 1, 1, 1), sweep_point(3.0, 1, 1, 1)]

    assert best_threshold(points).threshold == 2.5


def test_best_threshold_needs_at_least_one_point():
    with pytest.raises(ValueError, match="at least one threshold"):
        best_threshold([])


def test_read_truth_reads_period_and_incident_windows(tmp_path):
    path = tmp_path / "truth.json"
    path.write_text(
        json.dumps(
            {
                "seed": 1,
                "start": "2026-10-01T00:00:00Z",
                "end": "2026-10-29T00:00:00Z",
                "families": [],
                "failure_types": [],
                "incidents": [
                    {
                        "id": "incident-01",
                        "application": "checkout-service",
                        "environment": "production",
                        "failure_ids": ["checkout-db-timeout"],
                        "start": "2026-10-05T13:00:00Z",
                        "end": "2026-10-05T16:00:00Z",
                        "multiplier": 8.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    truth = read_truth(path)

    assert truth == GroundTruth(
        start=START,
        end=datetime(2026, 10, 29, tzinfo=UTC),
        incidents=(
            IncidentWindow(
                id="incident-01",
                series_key=CHECKOUT,
                start=datetime(2026, 10, 5, 13, tzinfo=UTC),
                end=datetime(2026, 10, 5, 16, tzinfo=UTC),
            ),
        ),
    )
