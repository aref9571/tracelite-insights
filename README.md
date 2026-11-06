# TraceLite Insights

TraceLite Insights is a companion service for the [TraceLite](https://github.com/aref9571/tracelite) error-monitoring platform. Its goal is to pull error data from TraceLite's API into an analytics store, detect abnormal error spikes with statistical models, find related issues that TraceLite's fingerprinting keeps apart, and measure how well both work against labelled, simulated incidents.

## Status

In development.

| Part | Status |
|:--|:--|
| Configuration, domain types and command-line tool | Done |
| Simulator for labelled TraceLite error traffic with known incidents | Done |
| DuckDB store and offline import of simulated data | Done |
| Import of issues and events from a running TraceLite | Done |
| Spike detection | In progress |
| Related-issue analysis | Planned |
| Evaluation against simulated incidents | In progress |
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

## Loading simulated data into DuckDB

`insights load` imports a simulation folder into the DuckDB database without a running TraceLite:

```bash
insights load data/sim-1/
```

| Option | Default | Meaning |
|---|---|---|
| `--database` | `INSIGHTS_DATABASE_PATH` | DuckDB database file; created with its folder if it does not exist |

The loader groups events into issues the way TraceLite does: one issue per application, environment and fingerprint, where the fingerprint is a SHA-256 hash of the exception type and the `class.method` names of the stack frames (see TraceLite's ADR 0002). Issue and event ids are UUIDs derived from that content, so loading the same folder again adds nothing. Without TraceLite there is no receive time, so `received_at` is the event's `occurredAt`.

The database has three tables:

- `issues`: one row per issue, with its occurrence count and first and last seen times.
- `events`: one row per event, linked to its issue by `issue_id`.
- `sim_event_failures`: the failure type behind each simulated event, used only for evaluation.
- `anomalies`: the hours flagged by `insights detect`.

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

## Syncing from a running TraceLite

`insights sync` reads issues and events from TraceLite's management API into the same DuckDB tables. It needs `INSIGHTS_TRACELITE_MANAGEMENT_KEY`:

```bash
insights sync
```

Each run reads only issues that changed since the last run, and only their new events. [docs/development.md](docs/development.md) explains how to run TraceLite locally, send simulated events to it and sync them.

## Detecting error spikes

`insights detect` builds one series of hourly error counts per application and environment, over all hours from the first to the last stored event, and stores every hour it flags as an anomaly:

```bash
insights detect --detector zscore --threshold 4
```

| Option | Default | Meaning |
|---|---|---|
| `--detector` | `zscore` | Detector to run: `zscore` or `seasonal` |
| `--threshold` | `4` | Score at or above which an hour is flagged |
| `--database` | `INSIGHTS_DATABASE_PATH` | DuckDB database file; it must already exist |

Each run replaces the earlier anomalies of the same detector. Rows of other detectors are kept.

The `zscore` detector compares each hour with the 168 hours (one week) before it. The score is `(count - mean) / max(std, 1)`, where the mean and standard deviation come from those previous hours only, so a spike does not raise its own baseline. The floor of 1 keeps quiet series with an almost constant count from producing huge scores. An hour is flagged only if its score reaches the threshold and it has at least 3 errors. The first 168 hours of a series have no score.

The `seasonal` detector compares each hour with the same hour of the week in the 3 weeks before it, together with the hour before and the hour after in each of those weeks (9 values). The expected count is the median of these values and the spread is their median absolute deviation (MAD) × 1.4826, but at least `sqrt(expected)` and at least 1. The score is `(count - expected) / spread`. Median and MAD are hardly moved by one unusual value in an earlier week, and comparing Monday 14:00 with earlier Mondays at 14:00 follows the daily and weekly pattern. The first 3 weeks and 1 hour of a series have no score.

## Evaluating detectors

`insights evaluate` measures a detector against the incidents of the simulator. For each seed it simulates 28 days, loads them into a temporary DuckDB database, runs the detector and compares the flagged hours with `truth.json`:

```bash
insights evaluate --seeds 2,3,4,5 --detector zscore --threshold 4
```

| Option | Default | Meaning |
|---|---|---|
| `--seeds` | `2,3,4,5` | Comma-separated simulation seeds |
| `--detector` | `zscore` | Detector to evaluate: `zscore` or `seasonal` |
| `--threshold` | `4` | Score at or above which an hour is flagged |
| `--days` | `28` | Length of each simulated period, at least 4 days |
| `--end` | `2026-10-20T00:00:00` | End of each simulated period in UTC; fixed so that the results can be reproduced |

It prints one line per seed and a Markdown table with the mean ± standard deviation over the seeds of precision, recall and F1 per hour, incident recall, false alerts per week and median detection delay. [docs/evaluation.md](docs/evaluation.md) defines the metrics and records the results.

`insights tune` chooses a threshold on a seed that is not used for reporting. It simulates one seed, evaluates the detector at the thresholds 2, 2.5, ..., 8, prints a table with one line per threshold and names the threshold with the best hour-level F1:

```bash
insights tune --seed 1 --detector seasonal --days 56
```

It takes `--seed` (default `1`), `--detector`, `--days` and `--end` with the same meaning and defaults as `insights evaluate`. When several thresholds share the best F1, the lowest one is chosen.
