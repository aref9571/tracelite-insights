import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from insights.evaluation.incidents import Evaluation

COLUMNS = (
    "Precision",
    "Recall",
    "F1",
    "Incident recall",
    "False alerts/week",
    "Median delay (h)",
)


F1_COLUMN = COLUMNS.index("F1")


@dataclass(frozen=True)
class Summary:
    mean: float
    std: float


@dataclass(frozen=True)
class ReportRow:
    detector: str
    threshold: float
    summaries: tuple[Summary, ...]


def metric_values(evaluation: Evaluation) -> tuple[float, ...]:
    return (
        evaluation.hours.precision,
        evaluation.hours.recall,
        evaluation.hours.f1,
        evaluation.incidents.incident_recall,
        evaluation.incidents.false_alerts_per_week,
        evaluation.incidents.median_delay_hours,
    )


def summarise(values: Sequence[float]) -> Summary:
    known = np.array([value for value in values if not math.isnan(value)], dtype=np.float64)
    if len(known) == 0:
        return Summary(mean=float("nan"), std=float("nan"))
    if len(known) == 1:
        return Summary(mean=float(known[0]), std=0.0)
    return Summary(mean=float(known.mean()), std=float(known.std(ddof=1)))


def report_row(detector: str, threshold: float, evaluations: Sequence[Evaluation]) -> ReportRow:
    if not evaluations:
        raise ValueError("a report row needs at least one evaluation")
    columns = zip(*(metric_values(evaluation) for evaluation in evaluations), strict=True)
    return ReportRow(
        detector=detector,
        threshold=threshold,
        summaries=tuple(summarise(values) for values in columns),
    )


def best_row(rows: Sequence[ReportRow]) -> ReportRow:
    if not rows:
        raise ValueError("choosing the best row needs at least one row")
    best = rows[0]
    for row in rows[1:]:
        if row.summaries[F1_COLUMN].mean > best.summaries[F1_COLUMN].mean:
            best = row
    return best


def format_summary(summary: Summary) -> str:
    if math.isnan(summary.mean):
        return "n/a"
    return f"{summary.mean:.2f} ± {summary.std:.2f}"


def markdown_table(rows: Sequence[ReportRow]) -> str:
    header = ["Detector", "Threshold", *COLUMNS]
    lines = [
        "| " + " | ".join(header) + " |",
        "|:--|" + "--:|" * (len(header) - 1),
    ]
    for row in rows:
        cells = [row.detector, f"{row.threshold:g}", *map(format_summary, row.summaries)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
