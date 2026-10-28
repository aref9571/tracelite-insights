import json
from datetime import UTC, datetime, timedelta

import pytest

from insights.fingerprint import fingerprint
from insights.loader import load_simulation, offline_event_id, offline_issue_id
from insights.simulator.scenarios import default_catalogue
from insights.simulator.traffic import simulate
from insights.simulator.truth import EVENT_FAILURES_FILE, EVENTS_FILE, write_simulation
from insights.store import Store

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=5)
LOADED_AT = datetime(2026, 9, 10, tzinfo=UTC)


@pytest.fixture(scope="module")
def sim_dir(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("simulation") / "sim-1"
    catalogue = default_catalogue(1, START, END)
    events = simulate(catalogue, START, END, seed=1)
    write_simulation(out_dir, catalogue, events, seed=1, start=START, end=END)
    return out_dir


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "insights.duckdb") as opened:
        yield opened


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def fetch_all(store, sql, parameters=()):
    return store.connection.execute(sql, list(parameters)).fetchall()


def copy_simulation(sim_dir, target):
    target.mkdir()
    for name in (EVENTS_FILE, EVENT_FAILURES_FILE):
        (target / name).write_bytes((sim_dir / name).read_bytes())
    return target


def test_load_simulation_stores_every_event_and_reports_counts(sim_dir, store):
    payloads = read_jsonl(sim_dir / EVENTS_FILE)

    report = load_simulation(store, sim_dir, LOADED_AT)

    stored_issues = fetch_all(store, "SELECT count(*) FROM issues")[0][0]
    assert report.events == report.new_events == len(payloads)
    assert report.issues == stored_issues
    assert fetch_all(store, "SELECT count(*) FROM events")[0][0] == len(payloads)


def test_loading_twice_does_not_duplicate_anything(sim_dir, store):
    first = load_simulation(store, sim_dir, LOADED_AT)

    second = load_simulation(store, sim_dir, LOADED_AT + timedelta(hours=1))

    assert second.new_events == 0
    assert second.issues == first.issues
    assert fetch_all(store, "SELECT count(*) FROM events")[0][0] == first.events
    assert fetch_all(store, "SELECT sum(occurrence_count) FROM issues")[0][0] == first.events
    assert fetch_all(store, "SELECT count(*) FROM sim_event_failures")[0][0] == first.events


def test_reload_into_a_new_database_gives_the_same_ids(sim_dir, tmp_path):
    ids = []
    for name in ("first.duckdb", "second.duckdb"):
        with Store(tmp_path / name) as store:
            load_simulation(store, sim_dir, LOADED_AT)
            issue_ids = fetch_all(store, "SELECT id FROM issues ORDER BY id")
            event_ids = fetch_all(store, "SELECT id FROM events ORDER BY id")
            ids.append((issue_ids, event_ids))

    assert ids[0] == ids[1]


def test_issue_counts_and_seen_times_match_their_events(sim_dir, store):
    load_simulation(store, sim_dir, LOADED_AT)

    mismatches = fetch_all(
        store,
        "SELECT i.id FROM issues i JOIN events e ON e.issue_id = i.id "
        "GROUP BY i.id, i.occurrence_count, i.first_seen_at, i.last_seen_at "
        "HAVING count(*) != i.occurrence_count "
        "OR min(e.received_at) != i.first_seen_at OR max(e.received_at) != i.last_seen_at",
    )

    assert mismatches == []


def test_issue_keeps_the_message_of_its_first_event(sim_dir, store):
    load_simulation(store, sim_dir, LOADED_AT)

    mismatches = fetch_all(
        store,
        "SELECT i.id FROM issues i JOIN events e ON e.issue_id = i.id "
        "GROUP BY i.id, i.message "
        "HAVING arg_min(e.message, e.occurred_at) != i.message",
    )

    assert mismatches == []


def test_issue_ids_come_from_scope_and_local_fingerprint(sim_dir, store):
    first_payload = read_jsonl(sim_dir / EVENTS_FILE)[0]
    expected_fingerprint = fingerprint(first_payload["exceptionType"], first_payload["stackTrace"])
    expected_id = offline_issue_id(
        first_payload["application"], first_payload["environment"], expected_fingerprint
    )

    load_simulation(store, sim_dir, LOADED_AT)

    assert fetch_all(
        store,
        "SELECT application, environment, fingerprint FROM issues WHERE id = ?",
        [expected_id],
    ) == [(first_payload["application"], first_payload["environment"], expected_fingerprint)]


def test_each_failure_type_belongs_to_exactly_one_issue(sim_dir, store):
    load_simulation(store, sim_dir, LOADED_AT)

    split_failures = fetch_all(
        store,
        "SELECT f.failure_id FROM sim_event_failures f JOIN events e ON e.id = f.event_id "
        "GROUP BY f.failure_id HAVING count(DISTINCT e.issue_id) > 1",
    )

    assert split_failures == []


def test_sim_event_failures_match_the_event_failures_file(sim_dir, store):
    payloads = read_jsonl(sim_dir / EVENTS_FILE)
    failure_rows = read_jsonl(sim_dir / EVENT_FAILURES_FILE)
    expected = {
        offline_event_id(payload): row["failure_id"]
        for payload, row in zip(payloads, failure_rows, strict=True)
    }

    load_simulation(store, sim_dir, LOADED_AT)

    assert dict(fetch_all(store, "SELECT event_id, failure_id FROM sim_event_failures")) == expected


def test_events_keep_payload_fields_and_use_occurred_at_as_received_at(sim_dir, store):
    first_payload = read_jsonl(sim_dir / EVENTS_FILE)[0]

    load_simulation(store, sim_dir, LOADED_AT)

    [row] = fetch_all(
        store,
        "SELECT exception_type, message, stack_trace, occurred_at, received_at, correlation_id "
        "FROM events WHERE correlation_id = ?",
        [first_payload["correlationId"]],
    )
    occurred_at = datetime.fromisoformat(first_payload["occurredAt"])
    assert row == (
        first_payload["exceptionType"],
        first_payload["message"],
        first_payload["stackTrace"],
        occurred_at,
        occurred_at,
        first_payload["correlationId"],
    )


def test_issues_are_unresolved_and_record_the_load_time(sim_dir, store):
    load_simulation(store, sim_dir, LOADED_AT)

    assert fetch_all(store, "SELECT DISTINCT status, resolved_at, synced_at FROM issues") == [
        ("UNRESOLVED", None, LOADED_AT)
    ]


def test_missing_events_file_raises_file_not_found(tmp_path, store):
    with pytest.raises(FileNotFoundError):
        load_simulation(store, tmp_path, LOADED_AT)


def test_failure_rows_must_match_the_number_of_events(sim_dir, tmp_path, store):
    broken = copy_simulation(sim_dir, tmp_path / "broken")
    lines = (broken / EVENT_FAILURES_FILE).read_text(encoding="utf-8").splitlines(keepends=True)
    (broken / EVENT_FAILURES_FILE).write_text("".join(lines[:-1]), encoding="utf-8")

    with pytest.raises(ValueError, match="rows but events.jsonl has"):
        load_simulation(store, broken, LOADED_AT)


def test_failure_rows_must_be_in_event_order(sim_dir, tmp_path, store):
    broken = copy_simulation(sim_dir, tmp_path / "broken")
    lines = (broken / EVENT_FAILURES_FILE).read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0], lines[1] = lines[1], lines[0]
    (broken / EVENT_FAILURES_FILE).write_text("".join(lines), encoding="utf-8")

    with pytest.raises(ValueError, match="line 1: expected event_index 0"):
        load_simulation(store, broken, LOADED_AT)


def test_invalid_json_line_is_reported_with_its_line_number(sim_dir, tmp_path, store):
    broken = copy_simulation(sim_dir, tmp_path / "broken")
    lines = (broken / EVENTS_FILE).read_text(encoding="utf-8").splitlines(keepends=True)
    lines[2] = "{not json\n"
    (broken / EVENTS_FILE).write_text("".join(lines), encoding="utf-8")

    with pytest.raises(ValueError, match="events.jsonl line 3"):
        load_simulation(store, broken, LOADED_AT)


@pytest.mark.parametrize(
    ("field", "value"),
    [("application", None), ("occurredAt", "2026-09-01T10:00:00")],
)
def test_invalid_event_is_reported_with_its_line_number(sim_dir, tmp_path, store, field, value):
    broken = copy_simulation(sim_dir, tmp_path / "broken")
    payloads = read_jsonl(broken / EVENTS_FILE)
    if value is None:
        del payloads[1][field]
    else:
        payloads[1][field] = value
    (broken / EVENTS_FILE).write_text(
        "".join(json.dumps(payload) + "\n" for payload in payloads), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="events.jsonl line 2"):
        load_simulation(store, broken, LOADED_AT)
    assert fetch_all(store, "SELECT count(*) FROM duckdb_tables()") == [(0,)]
