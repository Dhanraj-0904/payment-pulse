"""Structured comparison evidence and deterministic root-cause hints."""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from ml.incident_detection import DetectedIncident
from simulator.schema import TransactionRecord


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    dimension: str
    value: str
    baseline_metric: float | None
    incident_metric: float
    delta: float | None
    percentage_change: float | None


@dataclass(frozen=True, slots=True)
class InvestigationEvidence:
    incident_id: str
    top_banks: tuple[EvidenceItem, ...]
    top_payment_methods: tuple[EvidenceItem, ...]
    top_gateways: tuple[EvidenceItem, ...]
    top_merchants: tuple[EvidenceItem, ...]
    top_locations: tuple[EvidenceItem, ...]
    top_error_codes: tuple[EvidenceItem, ...]
    likely_pattern: str | None
    baseline_status: str
    evidence_quality: str


def _window_rows(records: list[TransactionRecord], start, end) -> list[TransactionRecord]:
    return [record for record in records if start <= record.timestamp < end]


def _evidence(rows_before, rows_during, attribute: str, is_error: bool = False) -> tuple[EvidenceItem, ...]:
    before_count = len(rows_before)
    during_count = len(rows_during) or 1
    before = Counter((record.error_code if is_error else getattr(record, attribute)) for record in rows_before)
    during = Counter((record.error_code if is_error else getattr(record, attribute)) for record in rows_during)
    values = set(before) | set(during)
    items = []
    for value in values:
        if value is None:
            continue
        current = during[value] / during_count
        if before_count > 0:
            baseline = before[value] / before_count
            delta = current - baseline
            percentage_change = delta / baseline if baseline else None
        else:
            baseline = None
            delta = None
            percentage_change = None
        items.append(
            EvidenceItem(
                dimension=attribute if not is_error else "error_code",
                value=str(value),
                baseline_metric=baseline,
                incident_metric=current,
                delta=delta,
                percentage_change=percentage_change,
            )
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (item.delta if item.delta is not None else 0.0, item.incident_metric),
            reverse=True,
        )[:5]
    )


def _hint(incident: DetectedIncident, evidence: dict[str, tuple[EvidenceItem, ...]]) -> str | None:
    errors = {item.value: (item.delta if item.delta is not None else 0.0) for item in evidence["error_code"]}
    errors_raw = {item.value: item.incident_metric for item in evidence["error_code"]}

    def check_error(code: str, threshold: float) -> bool:
        delta_val = errors.get(code, 0.0)
        raw_val = errors_raw.get(code, 0.0)
        return delta_val > threshold or (delta_val == 0.0 and raw_val > threshold)

    if incident.segment_level == "BANK_PAYMENT_METHOD" and "UPI" in incident.segment_key and check_error("TIMEOUT", 0.02):
        return "BANK_UPI_TIMEOUT"
    if incident.segment_level == "GATEWAY" and check_error("NETWORK_ERROR", 0.02):
        return "GATEWAY_DEGRADATION"
    if incident.segment_level in {"PAYMENT_METHOD", "BANK_PAYMENT_METHOD"} and "CARD" in incident.segment_key and check_error("AUTH_FAILED", 0.02):
        return "CARD_AUTH_DEGRADATION"
    if incident.segment_level == "LOCATION" and check_error("NETWORK_ERROR", 0.02):
        return "REGIONAL_NETWORK_DEGRADATION"
    if incident.segment_level == "MERCHANT":
        return "MERCHANT_CONFIGURATION_FAILURE"
    return None


def generate_investigation_evidence(records: Iterable[TransactionRecord], incident: DetectedIncident) -> InvestigationEvidence:
    """Compare detected window(s) with an equal-length prior period; no ground truth used."""
    rows = list(records)
    duration = incident.end_time - incident.start_time
    before = _window_rows(rows, incident.start_time - duration, incident.start_time)
    if not before:
        before = [r for r in rows if r.timestamp < incident.start_time]
    during = _window_rows(rows, incident.start_time, incident.end_time)
    baseline_available = len(before) > 0
    baseline_status = "OK" if baseline_available else "INSUFFICIENT_DATA"
    evidence_quality = "HIGH" if baseline_available else "POOR_INSUFFICIENT_BASELINE"
    evidence = {
        "bank": _evidence(before, during, "bank"),
        "payment_method": _evidence(before, during, "payment_method"),
        "gateway": _evidence(before, during, "gateway"),
        "merchant_id": _evidence(before, during, "merchant_id"),
        "location": _evidence(before, during, "location"),
        "error_code": _evidence(before, during, "error_code", is_error=True),
    }
    return InvestigationEvidence(
        incident_id=incident.incident_id,
        top_banks=evidence["bank"],
        top_payment_methods=evidence["payment_method"],
        top_gateways=evidence["gateway"],
        top_merchants=evidence["merchant_id"],
        top_locations=evidence["location"],
        top_error_codes=evidence["error_code"],
        likely_pattern=_hint(incident, evidence),
        baseline_status=baseline_status,
        evidence_quality=evidence_quality,
    )
