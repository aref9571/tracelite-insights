import math

import pytest

from insights.evaluation.incidents import Evaluation, HourMetrics, IncidentMetrics
from insights.evaluation.report import (
    ReportRow,
    Summary,
    best_row,
    markdown_table,
    metric_values,
    report_row,
    summarise,
)


def make_evaluation(true_positives, false_positives, false_negatives, detected, delays):
    return Evaluation(
        hours=HourMetrics(true_positives, false_positives, false_negatives),
        incidents=IncidentMetrics(
            incidents=4,
            detected=detected,
            false_alerts=false_positives,
            monitored_hours=168,
            delays_hours=tuple(delays),
        ),
    )


def test_metric_values_follow_the_column_order():
    evaluation = make_evaluation(2, 1, 1, detected=2, delays=[0.0, 2.0])

    values = metric_values(evaluation)

    assert values == pytest.approx((2 / 3, 2 / 3, 2 / 3, 0.5, 1.0, 1.0))


def test_summarise_gives_mean_and_sample_standard_deviation():
    summary = summarise([0.2, 0.4, 0.6])

    assert summary.mean == pytest.approx(0.4)
    assert summary.std == pytest.approx(0.2)


def test_summarise_ignores_seeds_without_a_value():
    summary = summarise([2.0, float("nan"), 4.0])

    assert summary.mean == pytest.approx(3.0)
    assert summary.std == pytest.approx(math.sqrt(2))


def test_summarise_of_one_value_has_zero_spread():
    assert summarise([0.5]) == Summary(mean=0.5, std=0.0)


def test_summarise_without_any_value_is_not_a_number():
    summary = summarise([float("nan")])

    assert math.isnan(summary.mean)
    assert math.isnan(summary.std)


def test_report_row_summarises_each_metric_across_seeds():
    evaluations = [
        make_evaluation(1, 1, 0, detected=1, delays=[1.0]),
        make_evaluation(3, 1, 0, detected=3, delays=[0.0, 0.0, 3.0]),
    ]

    row = report_row("zscore", 4.0, evaluations)

    assert row.detector == "zscore"
    assert row.threshold == 4.0
    assert row.summaries[0].mean == pytest.approx((0.5 + 0.75) / 2)
    assert row.summaries[3].mean == pytest.approx(0.5)
    assert row.summaries[5] == Summary(mean=0.5, std=pytest.approx(math.sqrt(0.5)))


def test_report_row_needs_at_least_one_evaluation():
    with pytest.raises(ValueError, match="at least one evaluation"):
        report_row("zscore", 4.0, [])


def test_markdown_table_has_a_header_and_one_line_per_row():
    row = ReportRow(
        detector="zscore",
        threshold=4.0,
        summaries=(
            Summary(0.5, 0.25),
            Summary(0.125, 0.0),
            Summary(0.2, 0.05),
            Summary(0.3, 0.1),
            Summary(1.5, 0.5),
            Summary(float("nan"), float("nan")),
        ),
    )

    table = markdown_table([row])

    assert table.splitlines() == [
        "| Detector | Threshold | Precision | Recall | F1 | Incident recall "
        "| False alerts/week | Median delay (h) |",
        "|:--|--:|--:|--:|--:|--:|--:|--:|",
        "| zscore | 4 | 0.50 ± 0.25 | 0.12 ± 0.00 | 0.20 ± 0.05 | 0.30 ± 0.10 "
        "| 1.50 ± 0.50 | n/a |",
    ]


def f1_row(threshold, f1):
    summaries = tuple(Summary(f1, 0.0) for _ in range(6))
    return ReportRow(detector="zscore", threshold=threshold, summaries=summaries)


def test_best_row_has_the_highest_mean_f1():
    rows = [f1_row(2.0, 0.1), f1_row(2.5, 0.3), f1_row(3.0, 0.2)]

    assert best_row(rows).threshold == 2.5


def test_best_row_looks_only_at_the_f1_column():
    low_f1 = ReportRow("zscore", 2.0, (Summary(0.9, 0), Summary(0.9, 0), Summary(0.1, 0)) * 2)
    high_f1 = ReportRow("zscore", 3.0, (Summary(0.1, 0), Summary(0.1, 0), Summary(0.5, 0)) * 2)

    assert best_row([low_f1, high_f1]).threshold == 3.0


def test_best_row_keeps_the_first_of_equal_f1_values():
    rows = [f1_row(2.0, 0.1), f1_row(2.5, 0.3), f1_row(3.0, 0.3)]

    assert best_row(rows).threshold == 2.5


def test_best_row_needs_at_least_one_row():
    with pytest.raises(ValueError, match="at least one row"):
        best_row([])
