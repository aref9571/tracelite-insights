import json

import httpx
import pytest
from typer.testing import CliRunner

from insights import cli
from insights.cli import app
from insights.config import Settings, get_settings
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
