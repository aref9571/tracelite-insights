# TraceLite Insights

TraceLite Insights is a companion service for the TraceLite error-monitoring platform. It pulls error data from TraceLite's API into an analytics store, detects abnormal error spikes with statistical models, finds related issues that TraceLite's fingerprinting keeps apart, and measures how well both work against labelled, simulated incidents. A small dashboard and REST API show the results.

**Status:** in development.

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
