# TraceLite Insights

TraceLite Insights is a companion service for the [TraceLite](https://github.com/aref9571/tracelite) error-monitoring platform. Its goal is to pull error data from TraceLite's API into an analytics store, detect abnormal error spikes with statistical models, find related issues that TraceLite's fingerprinting keeps apart, and measure how well both work against labelled, simulated incidents.

## Status

In development.

| Part | Status |
|:--|:--|
| Configuration, domain types and command-line tool | Done |
| Simulator for labelled TraceLite error traffic with known incidents | Done |
| Import of issues and events from TraceLite into DuckDB | Planned |
| Spike detection | Planned |
| Related-issue analysis | Planned |
| Evaluation against simulated incidents | Planned |
| REST API and dashboard | Planned |

## Requirements

- Python 3.12 or newer
- Git

## Development setup

Create a virtual environment and install the package with its development tools:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Check that the command-line tool is installed:

```bash
insights version
```

Run the checks:

```bash
ruff format --check .
ruff check .
pytest
```

## Configuration

Settings are read from environment variables with the prefix `INSIGHTS_`, or from a `.env` file in the working directory. Copy `.env.example` to `.env` and replace the `<set-me>` placeholders. Never commit `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `INSIGHTS_TRACELITE_BASE_URL` | `http://localhost:8080` | Base URL of the TraceLite server |
| `INSIGHTS_TRACELITE_MANAGEMENT_KEY` | none | TraceLite management key for reading issues and events |
| `INSIGHTS_TRACELITE_INGESTION_KEYS` | `{}` | JSON object mapping each application name to its TraceLite ingestion key |
| `INSIGHTS_DATABASE_PATH` | `data/insights.duckdb` | Path of the DuckDB database file |
| `INSIGHTS_API_KEY` | none | Key that protects the Insights REST API |
| `INSIGHTS_REQUEST_TIMEOUT_SECONDS` | `10` | Timeout for requests to TraceLite, greater than zero |

## Simulated data

The simulator produces realistic, labelled TraceLite error traffic for tests and evaluation. It needs no running TraceLite:

```bash
insights simulate --seed 1 --days 28 --out data/sim-1/
```

| Option | Default | Meaning |
|---|---|---|
| `--seed` | `1` | Seed for all random choices; the same seed and period give the same files |
| `--days` | `28` | Length of the simulated period, at least 4 days |
| `--end` | current hour | End of the period in UTC, for example `2026-10-20T00:00:00`; rounded down to the hour |
| `--out` | `data/sim-1` | Output folder |

It writes three files:

- `events.jsonl`: one event per line, in the JSON format of TraceLite's `POST /api/v1/events`, sorted by `occurredAt`.
- `event_failures.jsonl`: the failure type behind each event, by its line index in `events.jsonl`.
- `truth.json`: the failure families (several failure types with one root cause) and the incidents (time windows with a raised error rate).

Event counts per hour follow a Poisson distribution whose rate has a daily cycle (peak at 14:00 UTC, 40% at night) and a weekly cycle (60% at weekends). Failure and family ids appear only in `event_failures.jsonl` and `truth.json`, never in the events, so the analysis cannot see the answers.

## Sending simulated events to TraceLite

With `--send`, the simulator also posts every event to a running TraceLite through `POST /api/v1/events`. Each event is sent with the ingestion key of its application from `INSIGHTS_TRACELITE_INGESTION_KEYS`:

```bash
insights simulate --seed 1 --days 28 --out data/sim-1/ --send --tracelite-url http://localhost:8080
```

| Option | Default | Meaning |
|---|---|---|
| `--send` | off | Send the events after writing the files |
| `--tracelite-url` | `INSIGHTS_TRACELITE_BASE_URL` | Base URL of the TraceLite server |
| `--max-rps` | `50` | Maximum requests per second |

TraceLite accepts `occurredAt` only up to 30 days in the past and 5 minutes in the future, so all events are shifted by the same amount until the newest one lies one minute before the current time. Events that would still be older than 29 days are skipped. The gaps between events, and therefore the daily and weekly cycles and the incidents, stay the same.

The result of every event is written to `sent.jsonl` in the output folder, by its line index in `events.jsonl`:

| Status | When |
|---|---|
| `sent` | TraceLite answered `201`; the row holds the returned `issue_id` |
| `rejected` | TraceLite answered `400` or `413`; the row holds the problem title |
| `failed` | Any other answer, including `503` after five tries (waiting 0.5, 1, 2 and 4 seconds between them) |
| `skipped` | The shifted event would be older than 29 days |

Every row also holds the shifted `occurred_at`. A `401` or `403` answer, a missing ingestion key, or a server that cannot be reached after five tries stops the run with an error message; keys are never printed.
