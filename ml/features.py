"""Time-windowed, segmented payment-health feature aggregation."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import mean, median
from typing import Iterable

from simulator.schema import TransactionRecord

SEGMENTS = {
    "GLOBAL": (),
    "BANK": ("bank",),
    "PAYMENT_METHOD": ("payment_method",),
    "GATEWAY": ("gateway",),
    "BANK_PAYMENT_METHOD": ("bank", "payment_method"),
    "MERCHANT": ("merchant_id",),
    "LOCATION": ("location",),
}


@dataclass(frozen=True, slots=True)
class WindowFeatures:
    window_start: datetime
    window_end: datetime
    segment_level: str
    segment_key: str
    transaction_count: int
    success_count: int
    failure_count: int
    success_rate: float
    failure_rate: float
    mean_latency: float
    median_latency: float
    p95_latency: float
    p99_latency: float
    total_amount: Decimal
    failed_amount: Decimal
    error_code_counts: dict[str, int]
    payment_method_counts: dict[str, int]
    bank_counts: dict[str, int]
    gateway_counts: dict[str, int]
    merchant_counts: dict[str, int]
    location_counts: dict[str, int]

    @property
    def timeout_rate(self) -> float:
        return self.error_code_counts.get("TIMEOUT", 0) / self.transaction_count

    @property
    def network_error_rate(self) -> float:
        return self.error_code_counts.get("NETWORK_ERROR", 0) / self.transaction_count

    @property
    def auth_failed_rate(self) -> float:
        return self.error_code_counts.get("AUTH_FAILED", 0) / self.transaction_count

    def inference_values(self) -> dict[str, float]:
        """Explicit inference inputs; ground-truth fields cannot enter this mapping."""
        return {
            "transaction_count": float(self.transaction_count),
            "success_rate": self.success_rate,
            "failure_rate": self.failure_rate,
            "mean_latency": self.mean_latency,
            "median_latency": self.median_latency,
            "p95_latency": self.p95_latency,
            "p99_latency": self.p99_latency,
            "timeout_rate": self.timeout_rate,
            "network_error_rate": self.network_error_rate,
            "auth_failed_rate": self.auth_failed_rate,
        }


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, int((len(values) - 1) * fraction))
    return float(values[index])


def _window_start(timestamp: datetime, minutes: int) -> datetime:
    seconds = minutes * 60
    epoch = int(timestamp.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(epoch, tz=timestamp.tzinfo)


def _key(record: TransactionRecord, dimensions: tuple[str, ...]) -> str:
    return "ALL" if not dimensions else "|".join(str(getattr(record, dimension)) for dimension in dimensions)


def aggregate_transactions(records: Iterable[TransactionRecord], window_minutes: int = 5) -> list[WindowFeatures]:
    """Create global and localized health windows without reading incident labels."""
    groups: dict[tuple[datetime, str, str], list[TransactionRecord]] = defaultdict(list)
    for record in records:
        start = _window_start(record.timestamp, window_minutes)
        for level, dimensions in SEGMENTS.items():
            groups[(start, level, _key(record, dimensions))].append(record)
    results: list[WindowFeatures] = []
    for (start, level, key), rows in groups.items():
        latency = [row.latency_ms for row in rows]
        errors = Counter(row.error_code for row in rows if row.error_code)
        successes = sum(row.status == "SUCCESS" for row in rows)
        total = sum((row.amount for row in rows), Decimal("0"))
        failed = sum((row.amount for row in rows if row.status == "FAILED"), Decimal("0"))
        results.append(
            WindowFeatures(
                window_start=start,
                window_end=start + timedelta(minutes=window_minutes),
                segment_level=level,
                segment_key=key,
                transaction_count=len(rows),
                success_count=successes,
                failure_count=len(rows) - successes,
                success_rate=successes / len(rows),
                failure_rate=(len(rows) - successes) / len(rows),
                mean_latency=mean(latency),
                median_latency=float(median(latency)),
                p95_latency=_percentile(latency, 0.95),
                p99_latency=_percentile(latency, 0.99),
                total_amount=total,
                failed_amount=failed,
                error_code_counts=dict(errors),
                payment_method_counts=dict(Counter(row.payment_method for row in rows)),
                bank_counts=dict(Counter(row.bank for row in rows)),
                gateway_counts=dict(Counter(row.gateway for row in rows)),
                merchant_counts=dict(Counter(row.merchant_id for row in rows)),
                location_counts=dict(Counter(row.location for row in rows)),
            )
        )
    return sorted(results, key=lambda feature: (feature.window_start, feature.segment_level, feature.segment_key))
