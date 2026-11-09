from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from insights.evaluation.incidents import IncidentWindow, hour_labels
from insights.series import Series

HOURS_PER_DAY = 24


@dataclass(frozen=True)
class Dispersion:
    mean_count: float
    whole_series_ratio: float
    same_hour_ratio: float


def dispersion(series: Series, incidents: Sequence[IncidentWindow]) -> Dispersion:
    counts = series.counts.astype(np.float64)
    quiet = ~hour_labels(series, incidents)
    return Dispersion(
        mean_count=float(counts[quiet].mean()) if quiet.any() else float("nan"),
        whole_series_ratio=variance_to_mean(counts),
        same_hour_ratio=same_hour_ratio(series, counts, quiet),
    )


def variance_to_mean(counts: np.ndarray) -> float:
    if len(counts) < 2 or counts.mean() == 0:
        return float("nan")
    return float(counts.var(ddof=1) / counts.mean())


def same_hour_ratio(series: Series, counts: np.ndarray, quiet: np.ndarray) -> float:
    cells: dict[int, list[float]] = {}
    for hour, count, is_quiet in zip(series.hours, counts, quiet, strict=True):
        if is_quiet:
            cells.setdefault(hour_of_week(hour), []).append(float(count))
    repeated = [values for values in cells.values() if len(values) >= 2]
    if not repeated:
        return float("nan")
    mean_of_means = float(np.mean([np.mean(values) for values in repeated]))
    if mean_of_means == 0:
        return float("nan")
    mean_of_variances = float(np.mean([np.var(values, ddof=1) for values in repeated]))
    return mean_of_variances / mean_of_means


def hour_of_week(hour) -> int:
    return hour.weekday() * HOURS_PER_DAY + hour.hour
