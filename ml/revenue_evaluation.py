"""Post-calculation evaluation using original baseline/scenario truth, never inference inputs."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from ml.financial_models import FinancialImpact
from ml.ground_truth_adapter import EvaluationGroundTruth
from simulator.schema import TransactionRecord


@dataclass(frozen=True, slots=True)
class RevenueEstimateEvaluation:
    incident_id: str
    known_incremental_failed_amount: Decimal
    estimated_revenue_at_risk: Decimal
    estimation_error: Decimal
    absolute_percentage_error: float | None


def evaluate_revenue_estimate(baseline: Iterable[TransactionRecord], scenario: Iterable[TransactionRecord], impact: FinancialImpact, ground_truth: EvaluationGroundTruth) -> RevenueEstimateEvaluation:
    """Compare estimate with newly failed value derived from paired synthetic records."""
    before = {row.transaction_id: row for row in baseline}
    known = sum((row.amount for row in scenario if row.incident_id == ground_truth.incident_id and row.status == "FAILED" and before.get(row.transaction_id) and before[row.transaction_id].status == "SUCCESS"), Decimal("0"))
    error = impact.revenue_at_risk - known
    ape = float(abs(error) / known) if known else None
    return RevenueEstimateEvaluation(impact.incident_id, known, impact.revenue_at_risk, error, ape)
