"""Configuration for deterministic baseline transaction generation."""

import os
from dataclasses import dataclass
from datetime import datetime


def _environment_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _environment_timestamp(name: str, default: str) -> datetime:
    value = os.getenv(name, default).replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{name} must include a UTC offset")
    return timestamp


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    transaction_count: int
    random_seed: int
    start_timestamp: datetime
    transaction_frequency_seconds: int

    def __post_init__(self) -> None:
        if self.transaction_count < 1:
            raise ValueError("transaction_count must be at least 1")
        if self.transaction_frequency_seconds < 1:
            raise ValueError("transaction_frequency_seconds must be at least 1")
        if self.start_timestamp.tzinfo is None:
            raise ValueError("start_timestamp must include a UTC offset")

    @classmethod
    def from_environment(cls) -> "GeneratorConfig":
        return cls(
            transaction_count=_environment_int("TRANSACTION_COUNT", 500000),
            random_seed=_environment_int("RANDOM_SEED", 42),
            start_timestamp=_environment_timestamp("START_TIMESTAMP", "2026-01-01T00:00:00+00:00"),
            transaction_frequency_seconds=_environment_int("TRANSACTION_FREQUENCY_SECONDS", 1),
        )
