"""Deterministic Revenue-at-Risk calculations; no ground truth is read here."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import stdev
from typing import Iterable

from ml.financial_models import ConfidenceMetadata, FinancialImpact, SegmentImpact, TimeWindowImpact
from ml.incident_detection import DetectedIncident
from simulator.schema import TransactionRecord

MONEY = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return max(Decimal("0"), value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _scope(records: list[TransactionRecord], incident: DetectedIncident) -> list[TransactionRecord]:
    if incident.segment_level == "GLOBAL":
        return records
    fields = {
        "BANK": ("bank",), "PAYMENT_METHOD": ("payment_method",), "GATEWAY": ("gateway",),
        "MERCHANT": ("merchant_id",), "LOCATION": ("location",), "BANK_PAYMENT_METHOD": ("bank", "payment_method"),
    }.get(incident.segment_level, ())
    expected = incident.segment_key.split("|")
    return [record for record in records if all(str(getattr(record, field)) == value for field, value in zip(fields, expected))]


def _rates(records: list[TransactionRecord]) -> tuple[float, float, Decimal, Decimal]:
    if not records:
        return 0.0, 0.0, Decimal("0"), Decimal("0")
    total = sum((record.amount for record in records), Decimal("0"))
    failed_amount = sum((record.amount for record in records if record.status == "FAILED"), Decimal("0"))
    failure_rate = sum(record.status == "FAILED" for record in records) / len(records)
    return 1 - failure_rate, failure_rate, total / len(records), failed_amount


def _confidence(before: list[TransactionRecord], during: list[TransactionRecord], baseline_failure: float, incident_failure: float) -> ConfidenceMetadata:
    sizes = min(len(before) / 100, 1.0) * 0.45 + min(len(during) / 100, 1.0) * 0.35
    chunks = [sum(row.status == "FAILED" for row in before[index:index + 20]) / len(before[index:index + 20]) for index in range(0, len(before), 20) if before[index:index + 20]]
    stability = max(0.0, 1.0 - (stdev(chunks) if len(chunks) > 1 else 0.5))
    signal = min(abs(incident_failure - baseline_failure) / 0.20, 1.0) * 0.10
    score = round(min(1.0, sizes + stability * 0.10 + signal), 2)
    level = "HIGH" if score >= 0.75 else "MEDIUM" if score >= 0.45 else "LOW"
    return ConfidenceMetadata(score, level, len(before), len(during), round(stability, 2), "Prototype confidence based on sample size, stability, and effect size.")


def _periods(records: list[TransactionRecord], incident: DetectedIncident) -> tuple[list[TransactionRecord], list[TransactionRecord]]:
    scoped = _scope(records, incident)
    duration = incident.end_time - incident.start_time
    before = [row for row in scoped if incident.start_time - duration <= row.timestamp < incident.start_time]
    during = [row for row in scoped if incident.start_time <= row.timestamp < incident.end_time]
    return before, during


def _core(before: list[TransactionRecord], during: list[TransactionRecord], recoverability_rate: Decimal):
    baseline_success, baseline_failure, baseline_average, baseline_failed = _rates(before)
    incident_success, incident_failure, incident_average, incident_failed = _rates(during)
    expected_failures = Decimal(str(len(during) * baseline_failure))
    actual_failures = sum(row.status == "FAILED" for row in during)
    incremental = max(Decimal("0"), Decimal(actual_failures) - expected_failures)
    failure_based = _money(incremental * baseline_average)
    incident_total = sum((row.amount for row in during), Decimal("0"))
    actual_success = sum((row.amount for row in during if row.status == "SUCCESS"), Decimal("0"))
    value_based = _money(incident_total * Decimal(str(baseline_success)) - actual_success)
    primary = failure_based
    return baseline_success, baseline_failure, baseline_average, baseline_failed, incident_success, incident_failure, incident_average, incident_failed, expected_failures, actual_failures, incremental, failure_based, value_based, primary


def _segment_impacts(before: list[TransactionRecord], during: list[TransactionRecord], baseline_failure: float, baseline_average: Decimal, recoverability: Decimal) -> tuple[SegmentImpact, ...]:
    impacts: list[SegmentImpact] = []
    for field, label in (("bank", "bank"), ("payment_method", "payment_method"), ("gateway", "gateway"), ("merchant_id", "merchant"), ("location", "location")):
        previous, current = defaultdict(list), defaultdict(list)
        for row in before: previous[str(getattr(row, field))].append(row)
        for row in during: current[str(getattr(row, field))].append(row)
        for value, rows in current.items():
            prior = previous[value]
            _, prior_failure, prior_average, _ = _rates(prior)
            _, current_failure, _, _ = _rates(rows)
            expected = Decimal(str(len(rows) * (prior_failure if prior else baseline_failure)))
            incremental = max(Decimal("0"), Decimal(sum(row.status == "FAILED" for row in rows)) - expected)
            risk = _money(incremental * (prior_average if prior else baseline_average))
            impacts.append(SegmentImpact(label, value, len(rows), prior_failure if prior else baseline_failure, current_failure, incremental, risk, _money(risk * recoverability)))
    return tuple(sorted(impacts, key=lambda item: item.revenue_at_risk, reverse=True)[:10])


def _time_series(during: list[TransactionRecord], baseline_failure: float, baseline_average: Decimal, recoverability: Decimal, window_minutes: int) -> tuple[TimeWindowImpact, ...]:
    buckets = defaultdict(list)
    for row in during:
        seconds = window_minutes * 60
        epoch = int(row.timestamp.timestamp()) // seconds * seconds
        buckets[epoch].append(row)
    result = []
    for epoch, rows in sorted(buckets.items()):
        actual = Decimal(sum(row.status == "FAILED" for row in rows))
        incremental = max(Decimal("0"), actual - Decimal(str(len(rows) * baseline_failure)))
        risk = _money(incremental * baseline_average)
        start = rows[0].timestamp.fromtimestamp(epoch, tz=rows[0].timestamp.tzinfo)
        result.append(TimeWindowImpact(start.isoformat(), (start + timedelta(minutes=window_minutes)).isoformat(), len(rows), risk, _money(risk * recoverability)))
    return tuple(result)


def calculate_revenue_at_risk(records: Iterable[TransactionRecord], incident: DetectedIncident, recoverability_rate: Decimal | float = Decimal("0.60"), window_minutes: int = 5) -> FinancialImpact:
    """Calculate one canonical detected incident exactly once, without ground truth inputs."""
    rate = Decimal(str(recoverability_rate))
    if not Decimal("0") <= rate <= Decimal("1"):
        raise ValueError("recoverability_rate must be between 0 and 1")
    before, during = _periods(list(records), incident)
    values = _core(before, during, rate)
    (bs, bf, ba, bfa, ins, inf, ia, ifa, expected, actual, incremental, failure_risk, value_risk, primary) = values
    dimensional = _segment_impacts(before, during, bf, ba, rate)
    return FinancialImpact(incident.incident_id, len(before), len(during), bs, ins, bf, inf, _money(ba), _money(ia), _money(bfa), _money(ifa), expected, actual, incremental, failure_risk, value_risk, primary, _money(primary * rate), rate, _confidence(before, during, bf, inf), dimensional, _time_series(during, bf, ba, rate, window_minutes), f"{dimensional[0].dimension}|{dimensional[0].value}" if dimensional else None)


def calculate_revenue_for_incidents(records: Iterable[TransactionRecord], incidents: Iterable[DetectedIncident], recoverability_rate: Decimal | float = Decimal("0.60")) -> list[FinancialImpact]:
    """Deduplicate canonical IDs to prevent financial double counting."""
    unique = {incident.incident_id: incident for incident in incidents}
    data = list(records)
    return [calculate_revenue_at_risk(data, incident, recoverability_rate) for incident in unique.values()]
