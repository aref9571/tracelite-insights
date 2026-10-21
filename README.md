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
