import json
from datetime import UTC, datetime, timedelta

import pytest

from insights.simulator.scenarios import default_catalogue
from insights.simulator.traffic import simulate
from insights.simulator.truth import (
    EVENT_FAILURES_FILE,
    EVENTS_FILE,
    TRUTH_FILE,
    write_simulation,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=5)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def simulation(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("simulation") / "sim-1"
    catalogue = default_catalogue(1, START, END)
    events = simulate(catalogue, START, END, seed=1)
    write_simulation(out_dir, catalogue, events, seed=1, start=START, end=END)
    return out_dir, catalogue, events


def test_write_simulation_creates_the_three_files(simulation):
    out_dir, _, _ = simulation

    assert sorted(path.name for path in out_dir.iterdir()) == sorted(
        [EVENTS_FILE, EVENT_FAILURES_FILE, TRUTH_FILE]
    )


def test_events_file_holds_one_payload_per_event_in_order(simulation):
    out_dir, _, events = simulation

    assert read_jsonl(out_dir / EVENTS_FILE) == [event.payload() for event in events]


def test_event_failures_file_maps_each_event_index_to_its_failure(simulation):
    out_dir, _, events = simulation

    rows = read_jsonl(out_dir / EVENT_FAILURES_FILE)

    assert rows == [
        {"event_index": index, "failure_id": event.failure_id} for index, event in enumerate(events)
    ]


def test_events_file_contains_no_failure_or_family_ids(simulation):
    out_dir, catalogue, _ = simulation
    text = (out_dir / EVENTS_FILE).read_text(encoding="utf-8")
    hidden_ids = [failure.id for failure in catalogue.failure_types]
    hidden_ids += [family.id for family in catalogue.families]

    assert not any(hidden_id in text for hidden_id in hidden_ids)
    assert '"failure_id"' not in text
    assert '"family_id"' not in text


def test_truth_file_lists_period_families_failure_types_and_incidents(simulation):
    out_dir, catalogue, _ = simulation

    truth = json.loads((out_dir / TRUTH_FILE).read_text(encoding="utf-8"))

    assert truth["seed"] == 1
    assert truth["start"] == "2026-09-01T00:00:00Z"
    assert truth["end"] == "2026-09-06T00:00:00Z"
    assert len(truth["families"]) == len(catalogue.families)
    assert len(truth["failure_types"]) == len(catalogue.failure_types)
    assert len(truth["incidents"]) == len(catalogue.incidents)


def test_truth_family_members_match_failure_type_family_ids(simulation):
    out_dir, _, _ = simulation
    truth = json.loads((out_dir / TRUTH_FILE).read_text(encoding="utf-8"))
    family_of = {failure["id"]: failure["family_id"] for failure in truth["failure_types"]}

    for family in truth["families"]:
        assert family["failure_ids"]
        assert all(family_of[failure_id] == family["id"] for failure_id in family["failure_ids"])


def test_truth_incidents_keep_catalogue_values(simulation):
    out_dir, catalogue, _ = simulation
    truth = json.loads((out_dir / TRUTH_FILE).read_text(encoding="utf-8"))
    first = catalogue.incidents[0]

    assert truth["incidents"][0] == {
        "id": first.id,
        "application": first.application,
        "environment": first.environment,
        "failure_ids": list(first.failure_ids),
        "start": first.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": first.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "multiplier": first.multiplier,
    }


def test_write_simulation_overwrites_previous_output(tmp_path):
    catalogue = default_catalogue(1, START, END)
    first_events = simulate(catalogue, START, END, seed=1)
    write_simulation(tmp_path, catalogue, first_events, seed=1, start=START, end=END)

    write_simulation(tmp_path, catalogue, first_events[:3], seed=1, start=START, end=END)

    assert len(read_jsonl(tmp_path / EVENTS_FILE)) == 3
    assert len(read_jsonl(tmp_path / EVENT_FAILURES_FILE)) == 3
