import numpy as np

from insights.detection.base import as_float_series

HOURS_PER_WEEK = 168
MAD_TO_STD = 1.4826
SPREAD_FLOOR = 1.0


class SeasonalRobust:
    name = "seasonal"

    def __init__(self, weeks: int = 3, neighbour_hours: int = 1) -> None:
        if weeks < 1:
            raise ValueError("weeks must be at least 1")
        if not 0 <= neighbour_hours < HOURS_PER_WEEK:
            raise ValueError(f"neighbour_hours must be between 0 and {HOURS_PER_WEEK - 1}")
        self.weeks = weeks
        self.neighbour_hours = neighbour_hours

    @property
    def warm_up_hours(self) -> int:
        return self.weeks * HOURS_PER_WEEK + self.neighbour_hours

    def expected(self, counts: np.ndarray) -> np.ndarray:
        median, _ = self.baseline(counts)
        return median

    def score(self, counts: np.ndarray) -> np.ndarray:
        values = as_float_series(counts)
        median, spread = self.baseline(values)
        return (values - median) / spread

    def alerts(self, counts: np.ndarray, threshold: float) -> np.ndarray:
        return self.score(counts) >= threshold

    def baseline(self, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values = as_float_series(counts)
        median = np.full(len(values), np.nan)
        spread = np.full(len(values), np.nan)
        if len(values) > self.warm_up_hours:
            history = self.same_hours_in_previous_weeks(values)
            history_median = np.median(history, axis=1)
            mad = np.median(np.abs(history - history_median[:, np.newaxis]), axis=1)
            median[self.warm_up_hours :] = history_median
            spread[self.warm_up_hours :] = np.maximum(
                np.maximum(mad * MAD_TO_STD, np.sqrt(history_median)), SPREAD_FLOOR
            )
        return median, spread

    def same_hours_in_previous_weeks(self, values: np.ndarray) -> np.ndarray:
        hours = np.arange(self.warm_up_hours, len(values))
        offsets = np.array(
            [
                -week * HOURS_PER_WEEK + shift
                for week in range(1, self.weeks + 1)
                for shift in range(-self.neighbour_hours, self.neighbour_hours + 1)
            ]
        )
        return values[hours[:, np.newaxis] + offsets]
