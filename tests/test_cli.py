import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner

from insights import cli
from insights.cli import app
from insights.config import Settings, get_settings
from insights.domain import Event, Issue, IssueStatus
from insights.evaluation.incidents import evaluate_detector
from insights.store import Store

runner = CliRunner()


def test_version_command_prints_package_version_and_exits_successfully():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0.dev0"


def test_simulate_command_writes_the_three_files(tmp_path):
    out_dir = tmp_path / "sim-1"

    result = runner.invoke(
        app,
        [
            "simulate",
            "--seed",
            "1",
            "--days",
            "5",
            "--end",
            "2026-10-20T00:00:00",
            "--out",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0
    assert "10 incidents" in result.stdout
    assert sorted(path.name for path in out_dir.iterdir()) == [
        "event_failures.jsonl",
        "events.jsonl",
        "truth.json",
    ]
    truth = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))
    assert truth["start"] == "2026-10-15T00:00:00Z"
    assert truth["end"] == "2026-10-20T00:00:00Z"


def test_simulate_command_rounds_end_down_to_whole_hour(tmp_path):
    result = runner.invoke(
        app,
        ["simulate", "--days", "5", "--end", "2026-10-20T08:45:12", "--out", str(tmp_path)],
    )

    assert result.exit_code == 0
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    assert truth["end"] == "2026-10-20T08:00:00Z"


def test_simulate_command_with_same_seed_writes_identical_files(tmp_path):
    arguments = ["simulate", "--seed", "3", "--days", "5", "--end", "2026-10-20T00:00:00"]

    runner.invoke(app, [*arguments, "--out", str(tmp_path / "first")])
    runner.invoke(app, [*arguments, "--out", str(tmp_path / "second")])

    for name in ["events.jsonl", "event_failures.jsonl", "truth.json"]:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_simulate_command_rejects_too_few_days(tmp_path):
    result = runner.invoke(app, ["simulate", "--days", "3", "--out", str(tmp_path)])

    assert result.exit_code == 2
    assert not any(tmp_path.iterdir())


INGESTION_KEYS = {
    "checkout-service": "checkout_key_0123456789abcdef0123456789",
    "catalog-service": "catalog_key_0123456789abcdef01234567890",
    "auth-service": "auth_key_0123456789abcdef0123456789abcd",
}


@pytest.fixture
def tracelite(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INSIGHTS_TRACELITE_INGESTION_KEYS", json.dumps(INGESTION_KEYS))
    get_settings.cache_clear()
    requests = []
    responses = {"status": 201}

    def handler(request):
        requests.append(request)
        if responses["status"] == 201:
            return httpx.Response(201, json={"eventId": "e", "issueId": "i", "receivedAt": "r"})
        return httpx.Response(responses["status"], json={"title": "Unauthorized"})

    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("insights.simulator.sender.time.sleep", lambda seconds: None)
    yield requests, responses
    get_settings.cache_clear()


def test_simulate_with_send_posts_every_event_and_writes_sent_file(tracelite, tmp_path):
    requests, _ = tracelite
    out_dir = tmp_path / "sim"

    result = runner.invoke(
        app,
        [
            "simulate",
            "--days",
            "4",
            "--out",
            str(out_dir),
            "--send",
            "--tracelite-url",
            "http://tracelite.test",
            "--max-rps",
            "100000",
        ],
    )

    assert result.exit_code == 0
    events = (out_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    sent_rows = [json.loads(line) for line in (out_dir / "sent.jsonl").read_text().splitlines()]
    assert len(requests) == len(events) == len(sent_rows)
    assert {row["status"] for row in sent_rows} == {"sent"}
    assert str(requests[0].url) == "http://tracelite.test/api/v1/events"
    assert f"{len(events)} sent, 0 rejected, 0 failed, 0 skipped" in result.stdout


def test_simulate_with_send_stops_when_tracelite_refuses_the_key(tracelite, tmp_path):
    requests, responses = tracelite
    responses["status"] = 401

    result = runner.invoke(
        app, ["simulate", "--days", "4", "--out", str(tmp_path / "sim"), "--send"]
    )

    assert result.exit_code == 1
    assert "refused the ingestion key" in result.output
    assert len(requests) == 1
    assert not any(key in result.output for key in INGESTION_KEYS.values())
    assert not (tmp_path / "sim" / "sent.jsonl").exists()


def test_simulate_with_send_reports_missing_ingestion_keys(tracelite, monkeypatch, tmp_path):
    requests, _ = tracelite
    monkeypatch.setenv("INSIGHTS_TRACELITE_INGESTION_KEYS", "{}")
    get_settings.cache_clear()

    result = runner.invoke(
        app, ["simulate", "--days", "4", "--out", str(tmp_path / "sim"), "--send"]
    )

    assert result.exit_code == 1
    assert "No ingestion key configured for: auth-service" in result.output
    assert requests == []


def test_http_client_uses_the_configured_timeout():
    with cli.build_http_client(Settings(request_timeout_seconds=2.5)) as client:
        assert client.timeout == httpx.Timeout(2.5)


@pytest.fixture
def simulated_dir(tmp_path):
    out_dir = tmp_path / "sim-1"
    runner.invoke(
        app, ["simulate", "--days", "4", "--end", "2026-10-20T00:00:00", "--out", str(out_dir)]
    )
    return out_dir


def count_events(database):
    with Store(database) as store:
        return store.connection.execute("SELECT count(*) FROM events").fetchone()[0]


def test_load_command_creates_the_database_and_reports_counts(simulated_dir, tmp_path):
    database = tmp_path / "db" / "insights.duckdb"
    event_count = len((simulated_dir / "events.jsonl").read_text(encoding="utf-8").splitlines())

    result = runner.invoke(app, ["load", str(simulated_dir), "--database", str(database)])

    assert result.exit_code == 0
    assert f"Loaded {event_count} events ({event_count} new)" in result.stdout
    assert count_events(database) == event_count


def test_load_command_twice_reports_no_new_events(simulated_dir, tmp_path):
    database = tmp_path / "insights.duckdb"
    runner.invoke(app, ["load", str(simulated_dir), "--database", str(database)])

    result = runner.invoke(app, ["load", str(simulated_dir), "--database", str(database)])

    assert result.exit_code == 0
    assert "(0 new)" in result.stdout


def test_load_command_uses_the_configured_database_path(simulated_dir, tmp_path, monkeypatch):
    database = tmp_path / "configured" / "insights.duckdb"
    monkeypatch.setenv("INSIGHTS_DATABASE_PATH", str(database))
    get_settings.cache_clear()

    result = runner.invoke(app, ["load", str(simulated_dir)])
    get_settings.cache_clear()

    assert result.exit_code == 0
    assert str(database) in result.stdout
    assert count_events(database) > 0


def test_load_command_reports_a_missing_folder(tmp_path):
    missing = tmp_path / "missing"

    result = runner.invoke(
        app, ["load", str(missing), "--database", str(tmp_path / "insights.duckdb")]
    )

    assert result.exit_code == 1
    assert f"Error: cannot load {missing}" in result.output
    assert "events.jsonl" in result.output


SYNC_ISSUE_ID = "00000000-0000-4000-8000-000000000001"
MANAGEMENT_KEY = "management_key_0123456789abcdef01234567"


def sync_issue_json():
    return {
        "id": SYNC_ISSUE_ID,
        "application": "checkout-service",
        "environment": "production",
        "fingerprint": "f" * 64,
        "exceptionType": "java.lang.IllegalStateException",
        "message": "Cart is empty",
        "status": "UNRESOLVED",
        "occurrenceCount": 2,
        "firstSeenAt": "2026-10-13T09:00:00Z",
        "lastSeenAt": "2026-10-13T10:00:00Z",
        "resolvedAt": None,
    }


def sync_event_json(number):
    return {
        "id": f"0d9c8b7a-6f5e-4d3c-8b2a-{number:012d}",
        "exceptionType": "java.lang.IllegalStateException",
        "message": "Cart is empty",
        "stackTrace": "java.lang.IllegalStateException: Cart is empty",
        "occurredAt": f"2026-10-13T{8 + number:02d}:59:58Z",
        "receivedAt": f"2026-10-13T{9 + number:02d}:00:00Z",
        "correlationId": None,
        "context": None,
    }


def page_json(items):
    return {"items": items, "page": 0, "size": 100, "totalItems": len(items), "totalPages": 1}


@pytest.fixture
def management_api(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INSIGHTS_TRACELITE_MANAGEMENT_KEY", MANAGEMENT_KEY)
    get_settings.cache_clear()
    requests = []
    responses = {"status": 200}

    def handler(request):
        requests.append(request)
        if responses["status"] != 200:
            return httpx.Response(responses["status"], json={"title": "Unauthorized"})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=page_json([sync_event_json(1), sync_event_json(0)]))
        return httpx.Response(200, json=page_json([sync_issue_json()]))

    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    yield requests, responses
    get_settings.cache_clear()


def test_sync_command_stores_issues_and_events_and_reports_counts(management_api, tmp_path):
    requests, _ = management_api
    database = tmp_path / "db" / "insights.duckdb"

    result = runner.invoke(
        app, ["sync", "--database", str(database), "--tracelite-url", "http://tracelite:8080"]
    )

    assert result.exit_code == 0
    assert "Synced 1 issues (1 read) and 2 new events from http://tracelite:8080" in result.stdout
    assert count_events(database) == 2
    assert {request.url.host for request in requests} == {"tracelite"}
    assert all(request.headers["X-API-Key"] == MANAGEMENT_KEY for request in requests)
    assert MANAGEMENT_KEY not in result.output


def test_sync_command_twice_reports_no_new_events(management_api, tmp_path):
    database = tmp_path / "insights.duckdb"
    runner.invoke(app, ["sync", "--database", str(database)])

    result = runner.invoke(app, ["sync", "--database", str(database)])

    assert result.exit_code == 0
    assert "Synced 1 issues (0 read) and 0 new events" in result.stdout


def test_sync_command_reports_a_refused_key(management_api, tmp_path):
    _, responses = management_api
    responses["status"] = 401

    result = runner.invoke(app, ["sync", "--database", str(tmp_path / "insights.duckdb")])

    assert result.exit_code == 1
    assert "Error: sync from http://localhost:8080 failed: 401 Unauthorized" in result.output
    assert MANAGEMENT_KEY not in result.output


def test_sync_command_needs_a_management_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSIGHTS_TRACELITE_MANAGEMENT_KEY", raising=False)
    get_settings.cache_clear()

    result = runner.invoke(app, ["sync", "--database", str(tmp_path / "insights.duckdb")])
    get_settings.cache_clear()

    assert result.exit_code == 1
    assert "INSIGHTS_TRACELITE_MANAGEMENT_KEY is not set" in result.output


DETECT_START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
SPIKE_HOUR = 170


def create_database_with_spike(database):
    issue = Issue(
        id=uuid4(),
        application="checkout-service",
        environment="production",
        fingerprint="f" * 64,
        exception_type="java.lang.IllegalStateException",
        status=IssueStatus.UNRESOLVED,
        occurrence_count=1,
        first_seen_at=DETECT_START,
        last_seen_at=DETECT_START,
    )
    events_per_hour = [3] * 180
    events_per_hour[SPIKE_HOUR] = 30
    events = [
        Event(
            id=uuid4(),
            issue_id=issue.id,
            exception_type=issue.exception_type,
            occurred_at=DETECT_START + timedelta(hours=hour, minutes=minute),
            received_at=DETECT_START + timedelta(hours=hour, minutes=minute),
        )
        for hour, count in enumerate(events_per_hour)
        for minute in range(count)
    ]
    with Store(database) as store:
        store.create_schema()
        store.upsert_issues([issue], DETECT_START)
        store.insert_events(events)


def stored_anomaly_hours(database):
    with Store(database) as store:
        rows = store.connection.execute(
            "SELECT detector, series_key, hour FROM anomalies ORDER BY hour"
        ).fetchall()
    return rows


def test_detect_command_stores_the_spike_and_reports_counts(tmp_path):
    database = tmp_path / "insights.duckdb"
    create_database_with_spike(database)

    result = runner.invoke(
        app, ["detect", "--detector", "zscore", "--threshold", "4", "--database", str(database)]
    )

    assert result.exit_code == 0
    assert "Stored 1 zscore anomalies at threshold 4 from 1 series of 180 hours" in result.stdout
    assert stored_anomaly_hours(database) == [
        (
            "zscore",
            ["checkout-service", "production"],
            DETECT_START + timedelta(hours=SPIKE_HOUR),
        )
    ]


def test_detect_command_twice_replaces_the_earlier_anomalies(tmp_path):
    database = tmp_path / "insights.duckdb"
    create_database_with_spike(database)
    runner.invoke(app, ["detect", "--threshold", "4", "--database", str(database)])

    result = runner.invoke(app, ["detect", "--threshold", "100", "--database", str(database)])

    assert result.exit_code == 0
    assert "Stored 0 zscore anomalies at threshold 100" in result.stdout
    assert stored_anomaly_hours(database) == []


def test_detect_command_reports_an_empty_database(tmp_path):
    database = tmp_path / "insights.duckdb"
    with Store(database) as store:
        store.create_schema()

    result = runner.invoke(app, ["detect", "--database", str(database)])

    assert result.exit_code == 0
    assert "No events" in result.stdout


def test_detect_command_reports_a_missing_database(tmp_path):
    database = tmp_path / "missing.duckdb"

    result = runner.invoke(app, ["detect", "--database", str(database)])

    assert result.exit_code == 1
    assert f"Error: database {database} does not exist" in result.output
    assert not database.exists()


def test_detect_command_refuses_an_unknown_detector(tmp_path):
    result = runner.invoke(
        app, ["detect", "--detector", "magic", "--database", str(tmp_path / "x.duckdb")]
    )

    assert result.exit_code == 1
    assert "unknown detector 'magic'; choose one of: zscore, seasonal" in result.output


def test_evaluate_command_prints_one_line_per_seed_and_the_summary_table():
    result = runner.invoke(app, ["evaluate", "--seeds", "1,2", "--days", "8", "--threshold", "4"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Seed 1: precision ")
    assert lines[1].startswith("Seed 2: precision ")
    assert "of 10 incidents detected" in lines[0]
    assert "in 24 monitored hours" in lines[0]
    assert lines[3].startswith("| Detector | Threshold | Precision | Recall | F1 |")
    assert lines[5].startswith("| zscore | 4 | ")


def test_evaluating_the_same_seed_twice_gives_the_same_evaluation():
    end = datetime(2026, 10, 20, tzinfo=UTC)

    first = evaluate_once(3, 8, end)
    second = evaluate_once(3, 8, end)

    assert first == second
    assert first.incidents.incidents == 10


def evaluate_once(seed, days, end):
    series_list, truth = cli.simulate_series(seed, days, end)
    return evaluate_detector(cli.RollingZScore(), series_list, truth, 4.0)


def test_evaluate_command_without_plots_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["evaluate", "--seeds", "1", "--days", "8"])

    assert result.exit_code == 0
    assert list(tmp_path.iterdir()) == []


def test_evaluate_command_with_plots_writes_both_pngs(tmp_path):
    folder = tmp_path / "img"

    result = runner.invoke(
        app, ["evaluate", "--seeds", "1,2", "--days", "8", "--plots", str(folder)]
    )

    assert result.exit_code == 0
    assert sorted(path.name for path in folder.iterdir()) == [
        "precision-recall.png",
        "series-alerts-zscore.png",
    ]
    assert all(path.stat().st_size > 0 for path in folder.iterdir())
    assert f"Wrote {folder / 'series-alerts-zscore.png'} and " in result.stdout


def test_evaluate_command_names_the_series_plot_after_the_detector(tmp_path):
    result = runner.invoke(
        app,
        [
            "evaluate",
            "--seeds",
            "1",
            "--detector",
            "seasonal",
            "--days",
            "8",
            "--plots",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "series-alerts-seasonal.png").exists()


def test_busiest_series_is_the_one_with_most_incidents():
    series_list, truth = cli.simulate_series(2, 8, datetime(2026, 10, 20, tzinfo=UTC))

    busiest = cli.busiest_series(series_list, truth)

    counts = {
        series.key: sum(1 for incident in truth.incidents if incident.series_key == series.key)
        for series in series_list
    }
    assert counts[busiest.key] == max(counts.values())


def test_sweep_rows_gives_one_row_per_threshold():
    simulations = [
        cli.simulate_series(seed, 8, datetime(2026, 10, 20, tzinfo=UTC)) for seed in (1, 2)
    ]

    rows = cli.sweep_rows("zscore", simulations)

    assert [row.threshold for row in rows] == list(cli.SWEEP_THRESHOLDS)
    assert all(row.detector == "zscore" for row in rows)


def test_dispersion_command_prints_one_row_per_series():
    result = runner.invoke(app, ["dispersion", "--seeds", "1,2", "--days", "14"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0].startswith("| Series | Errors/hour outside incidents |")
    assert len(lines) == 2 + 6
    assert lines[2].startswith("| auth-service / production | ")


@pytest.mark.parametrize("seeds", ["1,x", "", "1,1"])
def test_dispersion_command_refuses_invalid_seeds(seeds):
    result = runner.invoke(app, ["dispersion", "--seeds", seeds, "--days", "4"])

    assert result.exit_code == 1
    assert "Error: seeds" in result.output


@pytest.mark.parametrize("seeds", ["2,x", "", "2,,3", "-1", "2,3,2"])
def test_evaluate_command_refuses_invalid_seeds(seeds):
    result = runner.invoke(app, ["evaluate", "--seeds", seeds, "--days", "4"])

    assert result.exit_code == 1
    assert "Error: seeds" in result.output


def test_evaluate_command_refuses_an_unknown_detector():
    result = runner.invoke(app, ["evaluate", "--detector", "magic"])

    assert result.exit_code == 1
    assert "unknown detector 'magic'; choose one of: zscore, seasonal" in result.output


def test_tune_command_prints_the_mean_sweep_and_the_best_threshold():
    result = runner.invoke(app, ["tune", "--seeds", "1,6", "--days", "8"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0].startswith("| Detector | Threshold | Precision | Recall | F1 |")
    thresholds = [line.split(" | ")[1] for line in lines[2:15]]
    assert thresholds == [f"{2 + step / 2:g}" for step in range(13)]
    assert all(line.startswith("| zscore | ") for line in lines[2:15])
    assert lines[15] == ""
    assert lines[16].startswith("Best mean F1 for zscore on seeds 1,6: threshold ")


def test_tune_command_mean_over_one_seed_has_zero_spread():
    result = runner.invoke(app, ["tune", "--seeds", "3", "--days", "8"])

    assert result.exit_code == 0
    assert "± 0.00 |" in result.stdout.splitlines()[2]


@pytest.mark.parametrize("seeds", ["1,x", "", "1,1"])
def test_tune_command_refuses_invalid_seeds(seeds):
    result = runner.invoke(app, ["tune", "--seeds", seeds, "--days", "4"])

    assert result.exit_code == 1
    assert "Error: seeds" in result.output


def test_tune_command_accepts_the_seasonal_detector():
    result = runner.invoke(app, ["tune", "--seeds", "1", "--detector", "seasonal", "--days", "4"])

    assert result.exit_code == 0
    assert "Best mean F1 for seasonal on seeds 1: threshold 2 (F1 0.00)" in result.stdout


def test_tune_command_refuses_an_unknown_detector():
    result = runner.invoke(app, ["tune", "--detector", "magic"])

    assert result.exit_code == 1
    assert "unknown detector 'magic'; choose one of: zscore, seasonal" in result.output


def test_simulate_series_gives_one_series_per_application_and_environment():
    end = datetime(2026, 10, 20, tzinfo=UTC)

    series_list, truth = cli.simulate_series(2, 4, end)

    assert len(series_list) == 6
    assert all(len(series.counts) == 96 for series in series_list)
    assert truth.start == end - timedelta(days=4)
    assert len(truth.incidents) == 10
