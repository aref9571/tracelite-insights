from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from insights import __version__
from insights.simulator.scenarios import MINIMUM_DAYS, default_catalogue
from insights.simulator.traffic import simulate
from insights.simulator.truth import write_simulation

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
) -> None:
    end_hour = start_of_hour(end.replace(tzinfo=UTC) if end else datetime.now(UTC))
    start = end_hour - timedelta(days=days)
    catalogue = default_catalogue(seed, start, end_hour)
    events = simulate(catalogue, start, end_hour, seed)
    write_simulation(out, catalogue, events, seed, start, end_hour)
    typer.echo(f"Wrote {len(events)} events and {len(catalogue.incidents)} incidents to {out}")


def start_of_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
