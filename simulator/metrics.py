"""Descriptive before/during incident metrics; not a revenue-at-risk model."""

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Iterable

from simulator.incidents import IncidentConfig
from simulator.schema import TransactionRecord


@dataclass(frozen=True, slots=True)
class IncidentMetrics:
    incident_id: str
    transaction_count: int
    affected_transaction_count: int
    success_rate_before: float
    success_rate_during: float
    failure_rate_before: float
    failure_rate_during: float
    average_latency_before_ms: float
    average_latency_during_ms: float
    median_latency_before_ms: float
    median_latency_during_ms: float
    failed_amount_during: Decimal
    estimated_incremental_failed_amount: Decimal


def _population(records: list[TransactionRecord], config: IncidentConfig) -> list[TransactionRecord]:
    return [
        record
        for record in records
        if all(
            expected is None or actual == expected
            for actual, expected in (
                (record.bank, config.affected_bank),
                (record.payment_method, config.affected_payment_method),
                (record.gateway, config.affected_gateway),
                (record.merchant_id, config.affected_merchant),
                (record.location, config.affected_location),
            )
        )
    ]


def _rates(records: list[TransactionRecord]) -> tuple[float, float, float, float]:
    if not records:
        return 0.0, 0.0, 0.0, 0.0
    success_rate = sum(record.status == "SUCCESS" for record in records) / len(records)
    latencies = [record.latency_ms for record in records]
    return success_rate, 1 - success_rate, sum(latencies) / len(latencies), float(median(latencies))


def calculate_incident_metrics(records: Iterable[TransactionRecord], config: IncidentConfig) -> IncidentMetrics:
    """Calculate a matched pre-incident window versus the active incident window."""
    population = _population(list(records), config)
    pre_start = config.start_time - (config.end_time - config.start_time)
    before = [record for record in population if pre_start <= record.timestamp < config.start_time]
    during = [record for record in population if config.start_time <= record.timestamp < config.end_time]
    before_success, before_failure, before_average, before_median = _rates(before)
    during_success, during_failure, during_average, during_median = _rates(during)
    failed_amount = sum((record.amount for record in during if record.status == "FAILED"), Decimal("0"))
    expected_failures = Decimal(str(before_failure)) * sum(
        (record.amount for record in during), Decimal("0")
    )
    incremental = max(Decimal("0"), failed_amount - Decimal(str(expected_failures))).quantize(Decimal("0.01"))
    return IncidentMetrics(
        incident_id=config.incident_id,
        transaction_count=len(during),
        affected_transaction_count=sum(record.incident_id == config.incident_id for record in during),
        success_rate_before=before_success,
        success_rate_during=during_success,
        failure_rate_before=before_failure,
        failure_rate_during=during_failure,
        average_latency_before_ms=before_average,
        average_latency_during_ms=during_average,
        median_latency_before_ms=before_median,
        median_latency_during_ms=during_median,
        failed_amount_during=failed_amount,
        estimated_incremental_failed_amount=incremental,
    )
