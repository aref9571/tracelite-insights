import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from insights.detection.base import as_float_series

STD_FLOOR = 1.0


class RollingZScore:
    name = "zscore"

    def __init__(self, window_hours: int = 168, min_count: int = 3) -> None:
        if window_hours < 1:
            raise ValueError("window_hours must be at least 1")
        if min_count < 0:
            raise ValueError("min_count must not be negative")
        self.window_hours = window_hours
        self.min_count = min_count

    def expected(self, counts: np.ndarray) -> np.ndarray:
        mean, _ = self.baseline(counts)
        return mean

    def score(self, counts: np.ndarray) -> np.ndarray:
        values = as_float_series(counts)
        mean, std = self.baseline(values)
        return (values - mean) / np.maximum(std, STD_FLOOR)

    def alerts(self, counts: np.ndarray, threshold: float) -> np.ndarray:
        values = as_float_series(counts)
        return (self.score(values) >= threshold) & (values >= self.min_count)

    def baseline(self, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values = as_float_series(counts)
        mean = np.full(len(values), np.nan)
        std = np.full(len(values), np.nan)
        if len(values) > self.window_hours:
            previous_hours = sliding_window_view(values[:-1], self.window_hours)
            mean[self.window_hours :] = previous_hours.mean(axis=1)
            std[self.window_hours :] = previous_hours.std(axis=1)
        return mean, std
