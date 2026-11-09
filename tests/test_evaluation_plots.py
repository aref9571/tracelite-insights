from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from insights.evaluation.incidents import IncidentWindow
from insights.evaluation.plots import plot_precision_recall, plot_series_with_alerts
from insights.evaluation.report import ReportRow, Summary
from insights.series import Series

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
KEY = ("checkout-service", "production")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def hour(offset):
    return START + timedelta(hours=offset)


def make_series(counts, key=KEY):
    hours = [hour(index) for index in range(len(counts))]
    return Series(key=key, hours=hours, counts=np.array(counts, dtype=np.int64))


def make_row(detector, threshold, precision, recall):
    summaries = (Summary(precision, 0.0), Summary(recall, 0.0)) + (Summary(0.0, 0.0),) * 4
    return ReportRow(detector=detector, threshold=threshold, summaries=summaries)


def incident(start_hour, end_hour, key=KEY):
    return IncidentWindow(
        id="incident-01", series_key=key, start=hour(start_hour), end=hour(end_hour)
    )


def test_series_plot_creates_a_png_file(tmp_path):
    series = make_series([5, 6, 4, 30, 28, 5, 6, 4])
    alerts = np.array([False, False, False, True, True, False, False, False])
    path = tmp_path / "series.png"

    plot_series_with_alerts(series, alerts, [incident(3, 5)], path)

    assert path.stat().st_size > 0
    assert path.read_bytes().startswith(PNG_SIGNATURE)


def test_series_plot_creates_missing_folders(tmp_path):
    path = tmp_path / "docs" / "img" / "series.png"

    plot_series_with_alerts(make_series([1, 2, 3]), np.zeros(3, dtype=bool), [], path)

    assert path.exists()


def test_series_plot_without_alerts_and_without_incidents_still_works(tmp_path):
    path = tmp_path / "series.png"

    plot_series_with_alerts(make_series([1, 2, 3, 4]), np.zeros(4, dtype=bool), [], path)

    assert path.stat().st_size > 0


def test_series_plot_ignores_incidents_of_other_series(tmp_path):
    other = incident(1, 2, key=("auth-service", "staging"))
    path = tmp_path / "series.png"

    plot_series_with_alerts(make_series([1, 2, 3, 4]), np.zeros(4, dtype=bool), [other], path)

    assert path.stat().st_size > 0


def test_series_plot_refuses_alerts_of_the_wrong_length(tmp_path):
    with pytest.raises(ValueError, match="one value per hour"):
        plot_series_with_alerts(
            make_series([1, 2, 3]), np.zeros(2, dtype=bool), [], tmp_path / "series.png"
        )

    assert not (tmp_path / "series.png").exists()


def test_precision_recall_plot_creates_a_png_file(tmp_path):
    sweeps = {
        "zscore": [make_row("zscore", 3.0, 0.1, 0.4), make_row("zscore", 4.0, 0.3, 0.2)],
        "seasonal": [make_row("seasonal", 3.0, 0.2, 0.3), make_row("seasonal", 4.0, 0.4, 0.1)],
    }
    path = tmp_path / "pr.png"

    plot_precision_recall(sweeps, {"zscore": 4.0, "seasonal": 3.0}, path)

    assert path.read_bytes().startswith(PNG_SIGNATURE)
    assert path.stat().st_size > 0


def test_precision_recall_plot_works_without_a_chosen_threshold(tmp_path):
    sweeps = {"zscore": [make_row("zscore", 3.0, 0.1, 0.4)]}
    path = tmp_path / "pr.png"

    plot_precision_recall(sweeps, {}, path)

    assert path.stat().st_size > 0


def test_precision_recall_plot_ignores_a_chosen_threshold_outside_the_sweep(tmp_path):
    sweeps = {"zscore": [make_row("zscore", 3.0, 0.1, 0.4)]}
    path = tmp_path / "pr.png"

    plot_precision_recall(sweeps, {"zscore": 9.0}, path)

    assert path.stat().st_size > 0


def test_plots_have_the_same_size_for_different_data(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    plot_series_with_alerts(make_series([1, 2, 3, 4]), np.zeros(4, dtype=bool), [], first)
    plot_series_with_alerts(make_series([9] * 200), np.zeros(200, dtype=bool), [], second)

    assert first.read_bytes()[16:24] == second.read_bytes()[16:24]
