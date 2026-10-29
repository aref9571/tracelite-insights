from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import httpx
import typer

from insights import __version__
from insights.config import Settings, get_settings
from insights.loader import load_simulation
from insights.simulator.scenarios import MINIMUM_DAYS, default_catalogue
from insights.simulator.sender import (
    SENT_FILE,
    SenderError,
    TraceLiteSender,
    count_by_status,
    send_events,
)
from insights.simulator.traffic import simulate
from insights.simulator.truth import write_jsonl, write_simulation
from insights.store import Store
from insights.sync import sync
from insights.tracelite_client import TraceLiteClient, TraceLiteConnectionError, TraceLiteError

app = typer.Typer(no_args_is_help=True)


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
