from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import httpx
import typer

from insights import __version__
from insights.config import Settings, get_settings
from insights.detection.base import Detector, find_anomalies
from insights.detection.seasonal import SeasonalRobust
from insights.detection.zscore import RollingZScore
from insights.evaluation.incidents import (
    Evaluation,
    GroundTruth,
    evaluate_detector,
    read_truth,
    threshold_sweep,
)
from insights.evaluation.report import F1_COLUMN, best_row, markdown_table, report_row
from insights.loader import load_simulation
from insights.series import Series, event_period, hourly_counts
from insights.simulator.scenarios import MINIMUM_DAYS, default_catalogue
from insights.simulator.sender import (
    SENT_FILE,
    SenderError,
    TraceLiteSender,
    count_by_status,
    send_events,
)
from insights.simulator.traffic import simulate
from insights.simulator.truth import TRUTH_FILE, write_jsonl, write_simulation
from insights.store import Store
from insights.sync import sync
from insights.tracelite_client import TraceLiteClient, TraceLiteConnectionError, TraceLiteError

app = typer.Typer(no_args_is_help=True)

DETECTORS: dict[str, type[Detector]] = {"zscore": RollingZScore, "seasonal": SeasonalRobust}
EVALUATION_END = datetime(2026, 10, 20)
SWEEP_THRESHOLDS = tuple(2.0 + 0.5 * step for step in range(13))


@app.callback()
def main() -> None:
    pass


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command("simulate")
def simulate_command(
    seed: Annotated[int, typer.Option(help="Seed for all random choices.")] = 1,
    days: Annotated[
        int, typer.Option(min=MINIMUM_DAYS, help="Length of the simulated period in days.")
    ] = 28,
    out: Annotated[Path, typer.Option(help="Folder for the output files.")] = Path("data/sim-1"),
    end: Annotated[
        datetime | None,
        typer.Option(help="End of the simulated period in UTC. Defaults to the current hour."),
    ] = None,
    send: Annotated[
        bool, typer.Option("--send", help="Also send the events to a running TraceLite.")
    ] = False,
    tracelite_url: Annotated[
        str | None,
        typer.Option(help="TraceLite base URL. Defaults to INSIGHTS_TRACELITE_BASE_URL."),
    ] = None,
    max_rps: Annotated[
        int, typer.Option(min=1, help="Maximum requests per second when sending.")
    ] = 50,
) -> None:
    end_hour = start_of_hour(end.replace(tzinfo=UTC) if end else datetime.now(UTC))
    start = end_hour - timedelta(days=days)
    catalogue = default_catalogue(seed, start, end_hour)
    events = simulate(catalogue, start, end_hour, seed)
    write_simulation(out, catalogue, events, seed, start, end_hour)
    typer.echo(f"Wrote {len(events)} events and {len(catalogue.incidents)} incidents to {out}")
    if send:
        payloads = [event.payload() for event in events]
        send_to_tracelite(payloads, out, tracelite_url, max_rps)


@app.command("load")
def load_command(
    sim_dir: Annotated[Path, typer.Argument(help="Folder written by insights simulate.")],
    database: Annotated[
        Path | None,
        typer.Option(help="DuckDB database file. Defaults to INSIGHTS_DATABASE_PATH."),
    ] = None,
) -> None:
    database_path = database or get_settings().database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with Store(database_path) as store:
        try:
            report = load_simulation(store, sim_dir, datetime.now(UTC))
        except (OSError, ValueError) as error:
            typer.echo(f"Error: cannot load {sim_dir}: {error}", err=True)
            raise typer.Exit(1) from error
    typer.echo(
        f"Loaded {report.events} events ({report.new_events} new) "
        f"in {report.issues} issues into {database_path}"
    )


@app.command("sync")
def sync_command(
    database: Annotated[
        Path | None,
        typer.Option(help="DuckDB database file. Defaults to INSIGHTS_DATABASE_PATH."),
    ] = None,
    tracelite_url: Annotated[
        str | None,
        typer.Option(help="TraceLite base URL. Defaults to INSIGHTS_TRACELITE_BASE_URL."),
    ] = None,
) -> None:
    settings = get_settings()
    if settings.tracelite_management_key is None:
        typer.echo("Error: INSIGHTS_TRACELITE_MANAGEMENT_KEY is not set", err=True)
        raise typer.Exit(1)
    base_url = tracelite_url or settings.tracelite_base_url
    database_path = database or settings.database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with build_http_client(settings) as http_client, Store(database_path) as store:
        client = TraceLiteClient(base_url, settings.tracelite_management_key, http_client)
        try:
            report = sync(client, store, datetime.now(UTC))
        except (TraceLiteError, TraceLiteConnectionError) as error:
            typer.echo(f"Error: sync from {base_url} failed: {error}", err=True)
            raise typer.Exit(1) from error
    typer.echo(
        f"Synced {report.issues_seen} issues ({report.issues_read} read) and "
        f"{report.new_events} new events from {base_url} into {database_path} "
        f"in {report.duration_seconds:.1f} s"
    )


@app.command("detect")
def detect_command(
    detector: Annotated[
        str, typer.Option(help=f"Detector to run: {', '.join(DETECTORS)}.")
    ] = "zscore",
    threshold: Annotated[
        float, typer.Option(help="Score at or above which an hour is an anomaly.")
    ] = 4.0,
    database: Annotated[
        Path | None,
        typer.Option(help="DuckDB database file. Defaults to INSIGHTS_DATABASE_PATH."),
    ] = None,
) -> None:
    check_detector(detector)
    database_path = database or get_settings().database_path
    if not database_path.exists():
        typer.echo(f"Error: database {database_path} does not exist", err=True)
        raise typer.Exit(1)
    with Store(database_path) as store:
        store.create_schema()
        period = event_period(store)
        if period is None:
            typer.echo(f"No events in {database_path}; nothing to detect")
            return
        start, end = period
        series_list = hourly_counts(store, start, end)
        anomalies = find_anomalies(DETECTORS[detector](), series_list, threshold)
        written = store.replace_anomalies(detector, anomalies)
    hours = (end - start) // timedelta(hours=1)
    typer.echo(
        f"Stored {written} {detector} anomalies at threshold {threshold:g} "
        f"from {len(series_list)} series of {hours} hours in {database_path}"
    )


@app.command("evaluate")
def evaluate_command(
    seeds: Annotated[
        str, typer.Option(help="Comma-separated simulation seeds to evaluate on.")
    ] = "2,3,4,5",
    detector: Annotated[
        str, typer.Option(help=f"Detector to evaluate: {', '.join(DETECTORS)}.")
    ] = "zscore",
    threshold: Annotated[
        float, typer.Option(help="Score at or above which an hour is an anomaly.")
    ] = 4.0,
    days: Annotated[
        int, typer.Option(min=MINIMUM_DAYS, help="Length of each simulated period in days.")
    ] = 28,
    end: Annotated[
        datetime, typer.Option(help="End of each simulated period in UTC.")
    ] = EVALUATION_END,
) -> None:
    check_detector(detector)
    try:
        seed_list = parse_seeds(seeds)
    except ValueError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error
    end_hour = start_of_hour(end.replace(tzinfo=UTC))
    evaluations = []
    for seed in seed_list:
        evaluation = evaluate_seed(seed, days, end_hour, DETECTORS[detector](), threshold)
        typer.echo(seed_summary(seed, evaluation))
        evaluations.append(evaluation)
    typer.echo("")
    typer.echo(markdown_table([report_row(detector, threshold, evaluations)]), nl=False)


@app.command("tune")
def tune_command(
    seeds: Annotated[
        str, typer.Option(help="Comma-separated simulation seeds to tune on.")
    ] = "1,6,7,8,9",
    detector: Annotated[
        str, typer.Option(help=f"Detector to tune: {', '.join(DETECTORS)}.")
    ] = "zscore",
    days: Annotated[
        int, typer.Option(min=MINIMUM_DAYS, help="Length of the simulated period in days.")
    ] = 28,
    end: Annotated[
        datetime, typer.Option(help="End of the simulated period in UTC.")
    ] = EVALUATION_END,
) -> None:
    check_detector(detector)
    try:
        seed_list = parse_seeds(seeds)
    except ValueError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error
    end_hour = start_of_hour(end.replace(tzinfo=UTC))
    evaluations = {threshold: [] for threshold in SWEEP_THRESHOLDS}
    for seed in seed_list:
        series_list, truth = simulate_series(seed, days, end_hour)
        for point in threshold_sweep(DETECTORS[detector](), series_list, truth, SWEEP_THRESHOLDS):
            evaluations[point.threshold].append(point.evaluation)
    rows = [report_row(detector, threshold, evaluations[threshold]) for threshold in evaluations]
    best = best_row(rows)
    typer.echo(markdown_table(rows), nl=False)
    typer.echo("")
    seed_text = ",".join(map(str, seed_list))
    typer.echo(
        f"Best mean F1 for {detector} on seeds {seed_text}: threshold {best.threshold:g} "
        f"(F1 {best.summaries[F1_COLUMN].mean:.2f})"
    )


def check_detector(name: str) -> None:
    if name not in DETECTORS:
        typer.echo(
            f"Error: unknown detector {name!r}; choose one of: {', '.join(DETECTORS)}",
            err=True,
        )
        raise typer.Exit(1)


def parse_seeds(text: str) -> list[int]:
    parts = [part.strip() for part in text.split(",")]
    if not all(part.isdigit() for part in parts):
        raise ValueError(f"seeds must be comma-separated whole numbers, got {text!r}")
    seeds = [int(part) for part in parts]
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"seeds contain a seed twice: {text!r}")
    return seeds


def evaluate_seed(
    seed: int, days: int, end: datetime, detector: Detector, threshold: float
) -> Evaluation:
    series_list, truth = simulate_series(seed, days, end)
    return evaluate_detector(detector, series_list, truth, threshold)


def simulate_series(seed: int, days: int, end: datetime) -> tuple[list[Series], GroundTruth]:
    start = end - timedelta(days=days)
    catalogue = default_catalogue(seed, start, end)
    events = simulate(catalogue, start, end, seed)
    with TemporaryDirectory() as work_dir:
        sim_dir = Path(work_dir) / "sim"
        write_simulation(sim_dir, catalogue, events, seed, start, end)
        truth = read_truth(sim_dir / TRUTH_FILE)
        with Store(Path(work_dir) / "insights.duckdb") as store:
            load_simulation(store, sim_dir, datetime.now(UTC))
            series_list = hourly_counts(store, truth.start, truth.end)
    return series_list, truth


def seed_summary(seed: int, evaluation: Evaluation) -> str:
    hours = evaluation.hours
    incidents = evaluation.incidents
    return (
        f"Seed {seed}: precision {hours.precision:.2f}, recall {hours.recall:.2f}, "
        f"F1 {hours.f1:.2f}, {incidents.detected} of {incidents.incidents} incidents detected, "
        f"{incidents.false_alerts} false alerts in {incidents.monitored_hours} monitored hours"
    )


def send_to_tracelite(
    payloads: list[dict[str, str]], out: Path, tracelite_url: str | None, max_rps: int
) -> None:
    settings = get_settings()
    base_url = tracelite_url or settings.tracelite_base_url
    with build_http_client(settings) as client:
        sender = TraceLiteSender(base_url, settings.tracelite_ingestion_keys, client, max_rps)
        try:
            outcomes = send_events(sender, payloads, datetime.now(UTC))
        except SenderError as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(1) from error
    write_jsonl(out / SENT_FILE, (outcome.row() for outcome in outcomes))
    counts = count_by_status(outcomes)
    summary = ", ".join(f"{count} {status}" for status, count in counts.items())
    typer.echo(f"Sent to {base_url}: {summary}")


def build_http_client(settings: Settings) -> httpx.Client:
    return httpx.Client(timeout=settings.request_timeout_seconds)


def start_of_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
