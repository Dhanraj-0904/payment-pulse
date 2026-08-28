"""Typed, deterministic financial-impact results for simulated payment incidents."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ConfidenceMetadata:
    score: float
    level: str
    baseline_sample_size: int
    incident_sample_size: int
    baseline_stability: float
    notes: str


@dataclass(frozen=True, slots=True)
class SegmentImpact:
    dimension: str
    value: str
    transaction_count: int
    baseline_failure_rate: float
    incident_failure_rate: float
    incremental_failures: Decimal
    revenue_at_risk: Decimal
    potentially_recoverable_revenue: Decimal


@dataclass(frozen=True, slots=True)
class TimeWindowImpact:
    window_start: str
    window_end: str
    transaction_count: int
    revenue_at_risk: Decimal
    potentially_recoverable_revenue: Decimal


@dataclass(frozen=True, slots=True)
class FinancialImpact:
    incident_id: str
    baseline_transaction_count: int
    incident_transaction_count: int
    baseline_success_rate: float
    incident_success_rate: float
    baseline_failure_rate: float
    incident_failure_rate: float
    baseline_average_transaction_amount: Decimal
    incident_average_transaction_amount: Decimal
    baseline_failed_amount: Decimal
    incident_failed_amount: Decimal
    expected_failures: Decimal
    actual_failures: int
    incremental_failures: Decimal
    failure_based_revenue_at_risk: Decimal
    value_based_revenue_at_risk: Decimal
    revenue_at_risk: Decimal
    potentially_recoverable_revenue: Decimal
    recoverability_rate: Decimal
    confidence: ConfidenceMetadata
    dimensional_impact: tuple[SegmentImpact, ...]
    time_series_impact: tuple[TimeWindowImpact, ...]
    top_affected_segment: str | None
