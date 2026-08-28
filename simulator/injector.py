"""Deterministic, probabilistic incident injection over immutable baseline records."""

import hashlib
import random
from dataclasses import replace
from typing import Iterable

from simulator.ground_truth import IncidentGroundTruth, create_ground_truth
from simulator.incidents import IncidentConfig
from simulator.schema import TransactionRecord


def _rng_for_incident(incident_seed: int, incident_id: str) -> random.Random:
    digest = hashlib.sha256(f"{incident_seed}:{incident_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _matches(record: TransactionRecord, config: IncidentConfig) -> bool:
    return all(
        expected is None or actual == expected
        for actual, expected in (
            (record.bank, config.affected_bank),
            (record.payment_method, config.affected_payment_method),
            (record.gateway, config.affected_gateway),
            (record.merchant_id, config.affected_merchant),
            (record.location, config.affected_location),
        )
    )


def _intensity(record: TransactionRecord, config: IncidentConfig) -> float:
    if config.start_time <= record.timestamp < config.end_time:
        return 1.0
    if config.end_time <= record.timestamp < config.recovery_end_time and config.recovery_minutes:
        elapsed = (record.timestamp - config.end_time).total_seconds()
        return max(0.0, 1.0 - elapsed / (config.recovery_minutes * 60))
    return 0.0


def _apply_record(record: TransactionRecord, config: IncidentConfig, rng: random.Random) -> TransactionRecord:
    intensity = _intensity(record, config)
    if intensity == 0 or not _matches(record, config) or rng.random() > config.affected_transaction_percentage * intensity:
        return record
    # Normal baseline failures are roughly 3.5%. Add the incident's excess probability,
    # retaining stochastic variation rather than failing every selected transaction.
    excess_failure_probability = min(0.90, 0.035 * (config.failure_rate_multiplier - 1) * intensity)
    becomes_failed = record.status == "SUCCESS" and rng.random() < excess_failure_probability
    status = "FAILED" if becomes_failed else record.status
    error_code = record.error_code
    if becomes_failed:
        error_code = config.affected_error_code
    elif status == "FAILED" and rng.random() < 0.82 * intensity:
        error_code = config.affected_error_code
    latency_multiplier = 1 + (config.latency_multiplier - 1) * intensity
    latency_ms = max(record.latency_ms, int(round(record.latency_ms * latency_multiplier * rng.uniform(0.90, 1.10))))
    return replace(
        record,
        status=status,
        error_code=error_code,
        latency_ms=latency_ms,
        incident_id=config.incident_id,
    )


def inject_incident(
    baseline_records: Iterable[TransactionRecord], config: IncidentConfig, incident_seed: int
) -> tuple[list[TransactionRecord], IncidentGroundTruth]:
    """Return a new scenario dataset and evaluation-only ground truth for one incident."""
    rng = _rng_for_incident(incident_seed, config.incident_id)
    scenario_records = [_apply_record(record, config, rng) for record in baseline_records]
    affected_count = sum(record.incident_id == config.incident_id for record in scenario_records)
    return scenario_records, create_ground_truth(config, affected_count)


def inject_scenario(
    baseline_records: Iterable[TransactionRecord], configs: Iterable[IncidentConfig], incident_seed: int
) -> tuple[list[TransactionRecord], list[IncidentGroundTruth]]:
    """Apply a sequence of incidents without mutating the baseline input collection."""
    records = list(baseline_records)
    ground_truth: list[IncidentGroundTruth] = []
    for offset, config in enumerate(configs):
        records, truth = inject_incident(records, config, incident_seed + offset)
        ground_truth.append(truth)
    return records, ground_truth
