import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from insights.simulator.scenarios import Catalogue
from insights.simulator.traffic import SimulatedEvent, format_timestamp

EVENTS_FILE = "events.jsonl"
EVENT_FAILURES_FILE = "event_failures.jsonl"
TRUTH_FILE = "truth.json"


def write_simulation(
    out_dir: Path,
    catalogue: Catalogue,
    events: list[SimulatedEvent],
    seed: int,
    start: datetime,
    end: datetime,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / EVENTS_FILE, (event.payload() for event in events))
    write_jsonl(out_dir / EVENT_FAILURES_FILE, event_failure_rows(events))
    truth = truth_document(catalogue, seed, start, end)
    (out_dir / TRUTH_FILE).write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row) + "\n")


def event_failure_rows(events: list[SimulatedEvent]) -> Iterable[dict[str, Any]]:
    for index, event in enumerate(events):
        yield {"event_index": index, "failure_id": event.failure_id}


def truth_document(
    catalogue: Catalogue, seed: int, start: datetime, end: datetime
) -> dict[str, Any]:
    return {
        "seed": seed,
        "start": format_timestamp(start),
        "end": format_timestamp(end),
        "families": [
            {
                "id": family.id,
                "description": family.description,
                "failure_ids": [
                    failure.id
                    for failure in catalogue.failure_types
                    if failure.family_id == family.id
                ],
            }
            for family in catalogue.families
        ],
        "failure_types": [
            {
                "id": failure.id,
                "application": failure.application,
                "environment": failure.environment,
                "exception_type": failure.exception_type,
                "family_id": failure.family_id,
            }
            for failure in catalogue.failure_types
        ],
        "incidents": [
            {
                "id": incident.id,
                "application": incident.application,
                "environment": incident.environment,
                "failure_ids": list(incident.failure_ids),
                "start": format_timestamp(incident.start),
                "end": format_timestamp(incident.end),
                "multiplier": incident.multiplier,
            }
            for incident in catalogue.incidents
        ],
    }
