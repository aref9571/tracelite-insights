from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
from matplotlib.figure import Figure

from insights.evaluation.incidents import IncidentWindow
from insights.evaluation.report import ReportRow
from insights.series import Series

FIGURE_SIZE = (10.0, 4.5)
DPI = 120
INCIDENT_COLOUR = "#f2b134"
ALERT_COLOUR = "#c0392b"
COUNT_COLOUR = "#34495e"
MINIMUM_INCIDENT_WIDTH = 0.004
PRECISION_COLUMN = 0
RECALL_COLUMN = 1


def plot_series_with_alerts(
    series: Series, alerts: np.ndarray, incidents: Sequence[IncidentWindow], path: Path
) -> None:
    alerts = np.asarray(alerts, dtype=bool)
    if alerts.shape != series.counts.shape:
        raise ValueError("alerts must have one value per hour of the series")
    figure = Figure(figsize=FIGURE_SIZE, layout="constrained")
    axes = figure.subplots()
    axes.plot(series.hours, series.counts, color=COUNT_COLOUR, linewidth=0.8, label="Errors")
    draw_incidents(axes, series, incidents)
    alert_indices = np.flatnonzero(alerts)
    axes.scatter(
        [series.hours[index] for index in alert_indices],
        series.counts[alert_indices],
        color=ALERT_COLOUR,
        marker="v",
        s=40,
        zorder=3,
        label="Alert",
    )
    application, environment = series.key
    axes.set_title(f"{application} ({environment})")
    axes.set_xlabel("Time (UTC)")
    axes.set_ylabel("Errors per hour")
    axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    axes.margins(x=0.01)
    axes.grid(alpha=0.3)
    axes.legend(loc="upper left")
    save(figure, path)


def draw_incidents(axes, series: Series, incidents: Sequence[IncidentWindow]) -> None:
    own_incidents = [incident for incident in incidents if incident.series_key == series.key]
    minimum_width = (series.hours[-1] - series.hours[0]) * MINIMUM_INCIDENT_WIDTH
    for position, incident in enumerate(own_incidents):
        centre = incident.start + (incident.end - incident.start) / 2
        width = max(incident.end - incident.start, minimum_width)
        axes.axvspan(
            centre - width / 2,
            centre + width / 2,
            color=INCIDENT_COLOUR,
            alpha=0.5,
            label="Incident" if position == 0 else None,
        )


def plot_precision_recall(
    sweeps: Mapping[str, Sequence[ReportRow]], chosen: Mapping[str, float], path: Path
) -> None:
    figure = Figure(figsize=FIGURE_SIZE, layout="constrained")
    axes = figure.subplots()
    for detector, rows in sweeps.items():
        recall = [row.summaries[RECALL_COLUMN].mean for row in rows]
        precision = [row.summaries[PRECISION_COLUMN].mean for row in rows]
        line = axes.plot(recall, precision, marker="o", markersize=4, label=detector)[0]
        mark_chosen(axes, detector, rows, chosen, line.get_color())
    axes.set_xlim(0.0, 1.0)
    axes.set_ylim(0.0, 1.0)
    axes.set_xlabel("Recall (share of incident hours flagged)")
    axes.set_ylabel("Precision (share of flagged hours inside an incident)")
    axes.set_title("Precision and recall for thresholds 2 to 8")
    axes.grid(alpha=0.3)
    axes.legend(loc="upper right")
    save(figure, path)


def mark_chosen(
    axes, detector: str, rows: Sequence[ReportRow], chosen: Mapping[str, float], colour: str
) -> None:
    threshold = chosen.get(detector)
    for row in rows:
        if row.threshold == threshold:
            recall = row.summaries[RECALL_COLUMN].mean
            precision = row.summaries[PRECISION_COLUMN].mean
            axes.scatter([recall], [precision], s=140, facecolors="none", edgecolors=colour)
            axes.annotate(
                f"{threshold:g}",
                (recall, precision),
                textcoords="offset points",
                xytext=(8, 8),
                color=colour,
            )


def save(figure: Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=DPI)
