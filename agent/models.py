"""Data models and schemas for the AI Payment Recovery Agent."""

from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BranchMetrics:
    transaction_count: int
    success_rate: float
    failure_rate: float
    average_latency: float
    revenue_at_risk: Decimal
    failed_amount: Decimal


@dataclass
class CounterfactualEffect:
    success_rate_improvement: float
    failure_rate_reduction: float
    revenue_risk_reduction: Decimal
    estimated_revenue_recovered: Decimal


@dataclass
class CounterfactualEvaluation:
    evaluation_id: str
    action_id: str
    horizon_steps: int
    runs: int
    with_action: BranchMetrics
    without_action: BranchMetrics
    effect: CounterfactualEffect
    confidence_interval: tuple[Decimal, Decimal]
    success_rate_ci: tuple[float, float] = (0.0, 0.0)
    confidence_level: float = 0.95


@dataclass
class AgentTrace:
    run_id: str
    incident_id: str | None = None
    mode: str | None = None  # MOCK, POLICY_FALLBACK, REAL_PROVIDER
    iteration: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)
    observation_summary: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None
    diagnosis_confidence: float = 0.0
    candidate_actions: list[list[dict[str, Any]]] = field(default_factory=list)
    candidate_scores: list[dict[str, Any]] = field(default_factory=list)
    selected_action: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    action_result: dict[str, Any] | None = None
    before_metrics: dict[str, Any] | None = None
    after_metrics: dict[str, Any] | None = None
    decision: str | None = None  # CONTINUE, ROLLBACK, STOP
    status: str | None = None  # RECOVERY_SUCCESSFUL, LOW_CONFIDENCE, IN_PROGRESS, FAILED
    confidence: float = 0.0
    reasoning_summary: list[str] = field(default_factory=list)
    counterfactual_evaluation: CounterfactualEvaluation | None = None
    prediction_telemetry: dict[str, Any] | None = None
    canary_result: Any | None = None


@dataclass
class EvaluationMetrics:
    incident_detected: bool = False
    action_selected: bool = False
    action_accepted: bool = False
    recovery_achieved: bool = False
    success_rate_improvement: float = 0.0
    revenue_at_risk_reduction: Decimal = Decimal("0.00")
    estimated_recovered_revenue: Decimal = Decimal("0.00")
    unnecessary_action_rate: float = 0.0
    rollback_rate: float = 0.0
    time_to_recovery_seconds: float = 0.0
