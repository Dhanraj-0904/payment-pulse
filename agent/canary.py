"""Canary Recovery Engine for Payment Pulse.

Implements canary-style progressive traffic shifting on top of the counterfactual evaluation twin.
Enforces strict policy bounds, multi-stage expansion (5% -> 25% -> 50%), automated rollback on degradation,
and produces the three-layer comparison:
1. CONTROL (without action)
2. COUNTERFACTUAL (predicted with action via Monte Carlo replications)
3. OBSERVED CANARY (actual empirical metrics observed during canary execution)
"""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.policy import check_policy, MAX_SINGLE_ACTION_TRAFFIC
from agent.tools import SimulatorToolbox
from agent.models import CounterfactualEvaluation, BranchMetrics


class CanaryOutcome(str, Enum):
    CANARY_PASS = "CANARY_PASS"
    CANARY_FAIL = "CANARY_FAIL"
    CANARY_INCONCLUSIVE = "CANARY_INCONCLUSIVE"


class CanaryStage(str, Enum):
    INITIAL = "INITIAL"          # e.g., 5%
    INTERMEDIATE = "INTERMEDIATE" # e.g., 25%
    FULL = "FULL"                # e.g., 50% (policy ceiling)
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class CanaryPolicy:
    """Configurable safety constraints and thresholds for Canary Recovery."""
    initial_traffic_percentage: float = 5.0
    traffic_stages: list[float] = field(default_factory=lambda: [5.0, 25.0, 50.0])
    max_traffic_percentage: float = 50.0
    min_observation_windows: int = 1
    min_success_rate_threshold: float = 0.85
    min_success_rate_delta: float = -0.02  # Maximum allowable drop below pre-canary baseline (tolerance for noise)
    max_latency_ms: float = 3000.0
    max_revenue_risk: Decimal = Decimal("50000.00")
    max_allowed_blast_radius: str = "LOW"
    timeout_steps: int = 3

    def validate(self) -> tuple[bool, str]:
        """Verify policy configuration adheres to platform safety bounds."""
        if self.initial_traffic_percentage <= 0.0:
            return False, "initial_traffic_percentage must be positive."
        if self.initial_traffic_percentage > self.max_traffic_percentage:
            return False, "initial_traffic_percentage cannot exceed max_traffic_percentage."
        if self.max_traffic_percentage > MAX_SINGLE_ACTION_TRAFFIC:
            return False, f"max_traffic_percentage ({self.max_traffic_percentage}%) exceeds platform maximum ({MAX_SINGLE_ACTION_TRAFFIC}%)."
        for s in self.traffic_stages:
            if s <= 0.0 or s > self.max_traffic_percentage:
                return False, f"traffic stage {s}% is outside valid bounds (0, {self.max_traffic_percentage}]."
        return True, "Valid"

    def __post_init__(self):
        valid, msg = self.validate()
        if not valid:
            raise ValueError(msg)


@dataclass
class CanaryStageExecution:
    """Telemetry captured for a single canary traffic stage."""
    stage_index: int
    traffic_percentage: float
    action_id: str | None
    observation_before: dict[str, Any]
    observation_after: dict[str, Any]
    outcome: CanaryOutcome
    reason: str


@dataclass
class CanaryResult:
    """Comprehensive result of a canary evaluation and expansion workflow."""
    canary_id: str
    target_action: dict[str, Any]
    policy: CanaryPolicy
    status: str  # CANARY_PASS, CANARY_FAIL, CANARY_INCONCLUSIVE
    current_stage: CanaryStage
    current_traffic_percentage: float
    control_metrics: dict[str, Any]
    counterfactual_prediction: CounterfactualEvaluation | None
    observed_canary_metrics: dict[str, Any]
    stages_executed: list[CanaryStageExecution] = field(default_factory=list)
    rolled_back: bool = False
    decision_reason: str = ""
    active_action_id: str | None = None


class CanaryRecoveryController:
    """Coordinates canary-based progressive traffic shifting using SimulatorToolbox.

    STRICT BOUNDARY:
    The agent and controller NEVER mutate SimulatorState, gateway health, or simulator internals.
    All actions pass through SimulatorToolbox.execute_action, SimulatorToolbox.rollback_action,
    and SimulatorToolbox.evaluate_counterfactual.
    """

    def __init__(self, toolbox: SimulatorToolbox, policy: CanaryPolicy | None = None) -> None:
        self.toolbox = toolbox
        self.policy = policy or CanaryPolicy()
        valid, msg = self.policy.validate()
        if not valid:
            raise ValueError(f"Invalid CanaryPolicy: {msg}")

    def run_canary_pipeline(
        self,
        candidate_action: dict[str, Any],
        auto_expand: bool = True
    ) -> CanaryResult:
        """Run the full canary lifecycle:

        1. Counterfactual evaluation (Control vs Predicted With-Action)
        2. Initial canary execution (e.g. 5%)
        3. Evaluation against pre-canary baseline
        4. Conditional expansion (25% -> 50%) or rollback
        """
        canary_id = f"CANARY_{uuid.uuid4().hex[:8].upper()}"

        # 1. Observe baseline state
        obs_baseline = self.toolbox.observe_result()
        control_metrics = {
            "success_rate": obs_baseline["success_rate"],
            "failure_rate": obs_baseline["failure_rate"],
            "latency": obs_baseline["latency"],
            "revenue_at_risk": str(obs_baseline["revenue_at_risk"]),
            "transaction_volume": obs_baseline["transaction_volume"],
        }

        # 2. Run Counterfactual Evaluation on the target candidate action (immutable dry-run)
        cf_eval = self.toolbox.evaluate_counterfactual(candidate_action, horizon_steps=1, runs=20)

        # 3. Form initial canary action
        initial_pct = self.policy.traffic_stages[0] if self.policy.traffic_stages else self.policy.initial_traffic_percentage
        canary_action = copy.deepcopy(candidate_action)
        if "parameters" not in canary_action:
            canary_action["parameters"] = {}
        canary_action["parameters"]["traffic_percentage"] = initial_pct
        canary_action["explanation"] = f"Canary progressive recovery stage 1 ({initial_pct}%): {candidate_action.get('explanation', 'Mitigate degradation')}"

        result = CanaryResult(
            canary_id=canary_id,
            target_action=candidate_action,
            policy=self.policy,
            status="IN_PROGRESS",
            current_stage=CanaryStage.INITIAL,
            current_traffic_percentage=initial_pct,
            control_metrics=control_metrics,
            counterfactual_prediction=cf_eval,
            observed_canary_metrics={},
            stages_executed=[],
            rolled_back=False,
            decision_reason=""
        )

        # 4. Validate initial canary action against safety policy
        passed, reason = check_policy(canary_action, obs_baseline)
        if not passed:
            result.status = CanaryOutcome.CANARY_FAIL.value
            result.decision_reason = f"Canary policy check rejected: {reason}"
            return result

        # 5. Execute initial canary stage
        exec_res = self.toolbox.execute_action(canary_action)
        if not exec_res.get("accepted"):
            result.status = CanaryOutcome.CANARY_FAIL.value
            result.decision_reason = f"Toolbox rejected execution: {exec_res.get('reason')}"
            return result

        action_id = exec_res.get("action_id")
        result.active_action_id = action_id
        obs_stage_1 = exec_res["observation"]

        # Evaluate stage 1
        outcome_1, reason_1 = self._evaluate_observation(obs_baseline, obs_stage_1)
        result.observed_canary_metrics = {
            "success_rate": obs_stage_1["success_rate"],
            "failure_rate": obs_stage_1["failure_rate"],
            "latency": obs_stage_1["latency"],
            "revenue_at_risk": str(obs_stage_1["revenue_at_risk"]),
            "transaction_volume": obs_stage_1["transaction_volume"],
        }

        stage_1_exec = CanaryStageExecution(
            stage_index=0,
            traffic_percentage=initial_pct,
            action_id=action_id,
            observation_before=obs_baseline,
            observation_after=obs_stage_1,
            outcome=outcome_1,
            reason=reason_1
        )
        result.stages_executed.append(stage_1_exec)

        # Handle stage 1 failure
        if outcome_1 == CanaryOutcome.CANARY_FAIL:
            result.status = CanaryOutcome.CANARY_FAIL.value
            result.decision_reason = f"Canary stage 1 ({initial_pct}%) failed: {reason_1}. Initiating rollback."
            if action_id:
                self.toolbox.rollback_action(action_id)
                result.rolled_back = True
                result.active_action_id = None
                result.current_stage = CanaryStage.ROLLED_BACK
            return result

        # Handle stage 1 inconclusive
        if outcome_1 == CanaryOutcome.CANARY_INCONCLUSIVE:
            result.status = CanaryOutcome.CANARY_INCONCLUSIVE.value
            result.decision_reason = f"Canary stage 1 ({initial_pct}%) inconclusive: {reason_1}. Halting automatic expansion."
            return result

        # Stage 1 Passed
        result.status = CanaryOutcome.CANARY_PASS.value
        result.decision_reason = f"Canary stage 1 ({initial_pct}%) verified: {reason_1}."

        # If not auto-expanding, return at stage 1
        if not auto_expand or len(self.policy.traffic_stages) <= 1:
            return result

        # 6. Progressive Expansion Stages (e.g., 25% -> 50%)
        for idx in range(1, len(self.policy.traffic_stages)):
            next_pct = self.policy.traffic_stages[idx]

            # Rollback previous stage action to replace with expanded percentage
            if result.active_action_id:
                self.toolbox.rollback_action(result.active_action_id)
                result.active_action_id = None

            expanded_action = copy.deepcopy(candidate_action)
            if "parameters" not in expanded_action:
                expanded_action["parameters"] = {}
            expanded_action["parameters"]["traffic_percentage"] = next_pct
            expanded_action["explanation"] = f"Canary progressive expansion stage {idx + 1} ({next_pct}%): {candidate_action.get('explanation', '')}"

            obs_current = self.toolbox.observe_result()
            passed_exp, reason_exp = check_policy(expanded_action, obs_current)
            if not passed_exp:
                result.decision_reason = f"Expansion to {next_pct}% stopped by policy: {reason_exp}"
                break

            exp_res = self.toolbox.execute_action(expanded_action)
            if not exp_res.get("accepted"):
                result.decision_reason = f"Expansion execution to {next_pct}% rejected: {exp_res.get('reason')}"
                break

            new_action_id = exp_res.get("action_id")
            result.active_action_id = new_action_id
            obs_expanded = exp_res["observation"]

            exp_outcome, exp_reason = self._evaluate_observation(obs_baseline, obs_expanded)

            stage_exec = CanaryStageExecution(
                stage_index=idx,
                traffic_percentage=next_pct,
                action_id=new_action_id,
                observation_before=obs_current,
                observation_after=obs_expanded,
                outcome=exp_outcome,
                reason=exp_reason
            )
            result.stages_executed.append(stage_exec)
            result.current_traffic_percentage = next_pct
            result.current_stage = CanaryStage.FULL if idx == len(self.policy.traffic_stages) - 1 else CanaryStage.INTERMEDIATE
            result.observed_canary_metrics = {
                "success_rate": obs_expanded["success_rate"],
                "failure_rate": obs_expanded["failure_rate"],
                "latency": obs_expanded["latency"],
                "revenue_at_risk": str(obs_expanded["revenue_at_risk"]),
                "transaction_volume": obs_expanded["transaction_volume"],
            }

            if exp_outcome == CanaryOutcome.CANARY_FAIL:
                result.status = CanaryOutcome.CANARY_FAIL.value
                result.decision_reason = f"Expansion stage {idx + 1} ({next_pct}%) failed: {exp_reason}. Rolling back."
                if new_action_id:
                    self.toolbox.rollback_action(new_action_id)
                    result.rolled_back = True
                    result.active_action_id = None
                    result.current_stage = CanaryStage.ROLLED_BACK
                break
            elif exp_outcome == CanaryOutcome.CANARY_INCONCLUSIVE:
                result.status = CanaryOutcome.CANARY_INCONCLUSIVE.value
                result.decision_reason = f"Expansion stage {idx + 1} ({next_pct}%) inconclusive: {exp_reason}. Halting further expansion."
                break
            else:
                result.status = CanaryOutcome.CANARY_PASS.value
                result.decision_reason = f"Expansion stage {idx + 1} ({next_pct}%) passed successfully: {exp_reason}."

        return result

    def _evaluate_observation(
        self,
        obs_baseline: dict[str, Any],
        obs_canary: dict[str, Any]
    ) -> tuple[CanaryOutcome, str]:
        """Compare canary observation against baseline using configured policy thresholds."""
        vol = obs_canary.get("transaction_volume", 0)
        if vol < 10:
            return CanaryOutcome.CANARY_INCONCLUSIVE, f"Sample volume ({vol} txns) too low for statistical confidence"

        canary_sr = float(obs_canary.get("success_rate", 0.0))
        baseline_sr = float(obs_baseline.get("success_rate", 0.0))
        canary_lat = float(obs_canary.get("latency", 0.0))

        # Check latency threshold
        if canary_lat > self.policy.max_latency_ms:
            return CanaryOutcome.CANARY_FAIL, f"Latency {canary_lat:.1f}ms breached maximum allowed {self.policy.max_latency_ms}ms"

        # Check for degradation below baseline (beyond allowable noise tolerance)
        if canary_sr < baseline_sr + self.policy.min_success_rate_delta:
            return CanaryOutcome.CANARY_FAIL, f"Success rate dropped from baseline {baseline_sr:.1%} to {canary_sr:.1%}"

        # If success rate improved or held above threshold
        if canary_sr >= self.policy.min_success_rate_threshold or canary_sr > baseline_sr:
            return CanaryOutcome.CANARY_PASS, f"Success rate ({canary_sr:.1%}) maintained above threshold ({self.policy.min_success_rate_threshold:.1%}) or improved"

        return CanaryOutcome.CANARY_INCONCLUSIVE, f"Success rate {canary_sr:.1%} showed no conclusive movement"
