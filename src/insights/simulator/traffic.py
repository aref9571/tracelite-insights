import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from string import Formatter

import numpy as np

from insights.simulator.scenarios import Catalogue, FailureType, Incident, hour_starts

PEAK_HOUR_UTC = 14
NIGHT_FACTOR = 0.4
WEEKEND_FACTOR = 0.6
SECONDS_PER_HOUR = 3600

PLACEHOLDER_VALUES: dict[str, Callable[[np.random.Generator], str]] = {
    "attempt": lambda rng: str(rng.integers(2, 6)),
    "attribute": lambda rng: str(rng.choice(["weight", "colour", "size", "material"])),
    "count": lambda rng: str(rng.integers(2, 32)),
    "coupon": lambda rng: f"SAVE{rng.integers(5, 60)}",
    "ip": lambda rng: f"10.0.{rng.integers(0, 256)}.{rng.integers(1, 255)}",
    "key_id": lambda rng: f"key-{rng.integers(1, 9)}",
    "ms": lambda rng: str(rng.integers(250, 30001)),
    "node": lambda rng: str(rng.integers(1, 7)),
    "order_id": lambda rng: f"o-{rng.integers(100000, 1000000)}",
    "payment_id": lambda rng: f"pay_{rng.integers(0, 16**10):010x}",
    "position": lambda rng: str(rng.integers(8, 120)),
    "query": lambda rng: str(rng.choice(["running shoes", "desk lamp", "usb-c cable", "kettle"])),
    "seconds": lambda rng: str(rng.integers(1, 31)),
    "session_id": lambda rng: f"{rng.integers(0, 16**12):012x}",
    "sku": lambda rng: f"SKU-{rng.integers(10000, 100000)}",
    "user_id": lambda rng: f"u-{rng.integers(1000, 100000)}",
}


@dataclass(frozen=True)
class SimulatedEvent:
    failure_id: str
    application: str
    environment: str
    exception_type: str
    message: str
    stack_trace: str
    occurred_at: datetime
    correlation_id: str

    def payload(self) -> dict[str, str]:
        return {
            "application": self.application,
            "environment": self.environment,
            "exceptionType": self.exception_type,
            "message": self.message,
            "stackTrace": self.stack_trace,
            "occurredAt": format_timestamp(self.occurred_at),
            "correlationId": self.correlation_id,
        }


def simulate(
    catalogue: Catalogue, start: datetime, end: datetime, seed: int
) -> list[SimulatedEvent]:
    rng = np.random.default_rng(seed)
    events = []
    for hour_start in hour_starts(start, end):
        for failure in catalogue.failure_types:
            rate = hourly_rate(failure, hour_start, catalogue.incidents)
            for _ in range(int(rng.poisson(rate))):
                events.append(simulate_event(failure, hour_start, rng))
    events.sort(key=lambda event: event.occurred_at)
    return events


def hourly_rate(
    failure: FailureType, hour_start: datetime, incidents: tuple[Incident, ...]
) -> float:
    return (
        failure.base_rate_per_hour
        * daily_factor(hour_start)
        * weekly_factor(hour_start)
        * incident_multiplier(failure.id, hour_start, incidents)
    )


def daily_factor(moment: datetime) -> float:
    angle = 2 * math.pi * (moment.hour - PEAK_HOUR_UTC) / 24
    return NIGHT_FACTOR + (1 - NIGHT_FACTOR) * (1 + math.cos(angle)) / 2


def weekly_factor(moment: datetime) -> float:
    return WEEKEND_FACTOR if moment.weekday() >= 5 else 1.0


def incident_multiplier(
    failure_id: str, hour_start: datetime, incidents: tuple[Incident, ...]
) -> float:
    return max(
        (incident.multiplier for incident in incidents if incident.covers(failure_id, hour_start)),
        default=1.0,
    )


def simulate_event(
    failure: FailureType, hour_start: datetime, rng: np.random.Generator
) -> SimulatedEvent:
    occurred_at = hour_start + timedelta(seconds=int(rng.integers(0, SECONDS_PER_HOUR)))
    message = fill_template(failure.message_template, rng)
    return SimulatedEvent(
        failure_id=failure.id,
        application=failure.application,
        environment=failure.environment,
        exception_type=failure.exception_type,
        message=message,
        stack_trace=java_stack_trace(failure.exception_type, message, failure.stack_frames),
        occurred_at=occurred_at,
        correlation_id=f"req-{rng.integers(0, 16**12):012x}",
    )


def placeholder_names(template: str) -> list[str]:
    return [name for _, name, _, _ in Formatter().parse(template) if name]


def fill_template(template: str, rng: np.random.Generator) -> str:
    values = {name: PLACEHOLDER_VALUES[name](rng) for name in placeholder_names(template)}
    return template.format(**values)


def java_stack_trace(exception_type: str, message: str, stack_frames: tuple[str, ...]) -> str:
    lines = [f"{exception_type}: {message}"]
    lines.extend(f"\tat {frame}" for frame in stack_frames)
    return "\n".join(lines)


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
