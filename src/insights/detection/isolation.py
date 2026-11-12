from datetime import UTC, datetime

import numpy as np
from sklearn.ensemble import IsolationForest

from insights.detection.base import as_float_series
from insights.series import is_whole_hour

HOURS_PER_DAY = 24
ROLLING_HOURS = 24
WEEKEND_FIRST_WEEKDAY = 5


class IsolationForestDetector:
    name = "isolation"

    def __init__(
        self,
        start: datetime,
        training_days: int = 14,
        trees: int = 100,
        random_state: int = 0,
    ) -> None:
        if start.tzinfo is None:
            raise ValueError("start must have a time zone")
        if not is_whole_hour(start):
            raise ValueError("start must be a whole hour")
        if training_days * HOURS_PER_DAY <= ROLLING_HOURS:
            raise ValueError("training_days must cover more than the first day")
        if trees < 1:
            raise ValueError("trees must be at least 1")
        self.start = start.astimezone(UTC).replace(tzinfo=None)
        self.training_hours = training_days * HOURS_PER_DAY
        self.trees = trees
        self.random_state = random_state
        self.scores_by_series: dict[bytes, np.ndarray] = {}

    def expected(self, counts: np.ndarray) -> np.ndarray:
        values = as_float_series(counts)
        expected = np.full(len(values), np.nan)
        if len(values) > self.training_hours:
            expected[:] = self.seasonal_expected(values)
            expected[: self.training_hours] = np.nan
        return expected

    def score(self, counts: np.ndarray) -> np.ndarray:
        values = as_float_series(counts)
        key = values.tobytes()
        if key not in self.scores_by_series:
            self.scores_by_series[key] = self.compute_scores(values)
        return self.scores_by_series[key].copy()

    def alerts(self, counts: np.ndarray, threshold: float) -> np.ndarray:
        values = as_float_series(counts)
        return (self.score(values) >= threshold) & (values > self.expected(values))

    def compute_scores(self, values: np.ndarray) -> np.ndarray:
        scores = np.full(len(values), np.nan)
        if len(values) <= self.training_hours:
            return scores
        features = self.features(values)
        forest = IsolationForest(n_estimators=self.trees, random_state=self.random_state)
        forest.fit(features[ROLLING_HOURS : self.training_hours])
        scores[self.training_hours :] = -forest.decision_function(features[self.training_hours :])
        return scores

    def features(self, values: np.ndarray) -> np.ndarray:
        hour_of_day = self.hour_of_day(len(values))
        angle = 2 * np.pi * hour_of_day / HOURS_PER_DAY
        expected = self.seasonal_expected(values)
        return np.column_stack(
            [
                values,
                values - expected,
                values / (self.previous_mean(values) + 1.0),
                np.sin(angle),
                np.cos(angle),
                self.is_weekend(len(values)).astype(np.float64),
            ]
        )

    def seasonal_expected(self, values: np.ndarray) -> np.ndarray:
        hour_of_day = self.hour_of_day(len(values))
        weekend = self.is_weekend(len(values))
        training = np.arange(ROLLING_HOURS, min(self.training_hours, len(values)))
        expected = np.zeros(len(values))
        for is_weekend in (False, True):
            for hour in range(HOURS_PER_DAY):
                in_group = (hour_of_day == hour) & (weekend == is_weekend)
                group_training = values[training][in_group[training]]
                if len(group_training) > 0:
                    expected[in_group] = np.median(group_training)
        return expected

    def previous_mean(self, values: np.ndarray) -> np.ndarray:
        cumulative = np.concatenate([[0.0], np.cumsum(values)])
        indices = np.arange(len(values))
        first = np.maximum(indices - ROLLING_HOURS, 0)
        window = np.maximum(indices - first, 1)
        return (cumulative[indices] - cumulative[first]) / window

    def hour_of_day(self, length: int) -> np.ndarray:
        return (self.start.hour + np.arange(length)) % HOURS_PER_DAY

    def is_weekend(self, length: int) -> np.ndarray:
        hours = np.datetime64(self.start, "h") + np.arange(length)
        weekday = (hours.astype("datetime64[D]").astype(np.int64) + 3) % 7
        return weekday >= WEEKEND_FIRST_WEEKDAY
