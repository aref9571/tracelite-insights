from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import httpx
import typer

from insights import __version__
from insights.config import Settings, get_settings
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
