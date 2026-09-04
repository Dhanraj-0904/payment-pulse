"""Core AI Recovery Agent and Fallback Policy Agent implementations."""

import os
import uuid
import json
from decimal import Decimal
from typing import Any

from agent.models import AgentTrace, EvaluationMetrics
from agent.tools import SimulatorToolbox
from agent.providers import LLMProvider

# Configurable Recovery Thresholds and Policy Constants
RECOVERY_SUCCESS_RATE_TARGET = 0.90
RECOVERY_REVENUE_RISK_REDUCTION = 0.50
MIN_ACTION_CONFIDENCE = 0.60
MIN_SUCCESS_RATE_IMPROVEMENT = 0.01
MIN_REVENUE_RISK_REDUCTION = Decimal("0.00")


def determine_agent_mode() -> str:
    """Determine agent operation mode based on environment variables."""
    provider_env = os.getenv("LLM_PROVIDER", "mock").upper()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider_env == "MOCK":
        return "MOCK"
    elif api_key:
        return "REAL_PROVIDER"
    else:
        return "POLICY_FALLBACK"


def _ensure_dict(observation: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass
    if is_dataclass(observation):
        return asdict(observation)
    if isinstance(observation, dict):
        obs = {}
        for k, v in observation.items():
            if is_dataclass(v):
                obs[k] = asdict(v)
            elif isinstance(v, (list, tuple)):
                obs[k] = [asdict(item) if is_dataclass(item) else item for item in v]
            else:
                obs[k] = v
        return obs
    return {}


def calculate_diagnosis_confidence(observation: Any) -> float:
    """Calculate incident diagnosis confidence score based on observation metrics."""
    obs = _ensure_dict(observation)

    active = obs.get("active_incidents", [])
    if not active:
        return 0.0

    anomaly_score = float(obs.get("anomaly_score", 0.0))
    evidence_list = obs.get("investigation_evidence", [])

    # Anomaly score contributes 40%
    confidence = anomaly_score * 0.4

    # Investigation evidence quality and volume contributes 40%
    evidence_contribution = 0.0
    for ev in evidence_list:
        quality = ev.get("evidence_quality", "POOR")
        if quality == "GOOD" or quality == "HIGH":
            evidence_contribution += 0.2
        elif quality == "FAIR":
            evidence_contribution += 0.1
        else:
            evidence_contribution += 0.05
        
        # Increment if likely pattern matches known degradation signatures
        likely = ev.get("likely_pattern", "UNKNOWN")
        if likely and likely != "UNKNOWN":
            evidence_contribution += 0.1

    confidence += min(0.4, evidence_contribution)

    # Severity level contributes 20%
    for inc in active:
        sev = inc.get("severity", "LOW").upper()
        if sev == "CRITICAL" or sev == "HIGH":
            confidence += 0.2
        elif sev == "MEDIUM":
            confidence += 0.1
        else:
            confidence += 0.05

    # Penalize if baseline window size was insufficient
    for ev in evidence_list:
        meta = ev.get("metadata", {})
        if meta.get("insufficient_baseline", False) or ev.get("baseline_status") == "INSUFFICIENT_DATA":
            confidence *= 0.5

    return max(0.0, min(1.0, float(confidence)))


def diagnose_incident(observation: Any, confidence: float) -> dict[str, Any]:
    """Extract structural root-cause diagnosis from ML observation metrics."""
    obs = _ensure_dict(observation)

    active = obs.get("active_incidents", [])
    evidence_list = obs.get("investigation_evidence", [])

    root_cause = "UNKNOWN"
    affected_gateway = None
    affected_bank = None
    affected_payment_method = None
    affected_region = None
    affected_merchant = None
    evidence_summary = "No active incidents detected."

    # Prioritize segment diagnosis from the true dynamic revenue impacts (top_affected_segments)
    top_affected = obs.get("top_affected_segments", [])
    for seg_info in top_affected:
        seg = seg_info.get("segment", "")
        if "|" in seg:
            dim, val = seg.split("|", 1)
            if dim == "gateway":
                affected_gateway = val
            elif dim == "bank":
                affected_bank = val
            elif dim == "payment_method":
                affected_payment_method = val
            elif dim == "merchant":
                affected_merchant = val

    if active:
        root_cause = active[0].get("incident_type", "UNKNOWN")

    if evidence_list:
        ev = evidence_list[0]
        likely = ev.get("likely_pattern", "UNKNOWN")
        if likely and likely != "UNKNOWN":
            root_cause = likely

        pattern_str = likely.replace("_", " ").title() if likely else "Unknown Degradation"
        evidence_summary = f"{pattern_str} pattern detected. "

        # Parse degraded dimensions if not already set by top_affected_segments
        if not affected_gateway:
            top_gws = sorted(ev.get("top_gateways", []), key=lambda x: x.get("incident_metric", 0.0), reverse=True)
            for gw in top_gws:
                if gw.get("incident_metric", 0.0) > 0.05:
                    affected_gateway = gw.get("value")
                    break
        if affected_gateway:
            evidence_summary += f"Gateway {affected_gateway} shows failures. "

        if not affected_bank:
            top_banks = sorted(ev.get("top_banks", []), key=lambda x: x.get("incident_metric", 0.0), reverse=True)
            for bank in top_banks:
                if bank.get("incident_metric", 0.0) > 0.05:
                    affected_bank = bank.get("value")
                    break
        if affected_bank:
            evidence_summary += f"Bank {affected_bank} affected. "

        if not affected_payment_method:
            top_methods = sorted(ev.get("top_payment_methods", []), key=lambda x: x.get("incident_metric", 0.0), reverse=True)
            for method in top_methods:
                if method.get("incident_metric", 0.0) > 0.05:
                    affected_payment_method = method.get("value")
                    break
        if affected_payment_method:
            evidence_summary += f"Payment Method {affected_payment_method} degraded. "

        if not affected_merchant:
            top_merch = sorted(ev.get("top_merchants", []), key=lambda x: x.get("incident_metric", 0.0), reverse=True)
            for merch in top_merch:
                if merch.get("incident_metric", 0.0) > 0.05:
                    affected_merchant = merch.get("value")
                    break
        if affected_merchant:
            evidence_summary += f"Merchant {affected_merchant} impacted. "

    return {
        "root_cause": root_cause,
        "affected_gateway": affected_gateway,
        "affected_bank": affected_bank,
        "affected_payment_method": affected_payment_method,
        "affected_region": affected_region,
        "affected_merchant": affected_merchant,
        "confidence": confidence,
        "evidence_summary": evidence_summary,
    }


def is_recovery_successful(before_metrics: dict[str, Any], after_metrics: dict[str, Any]) -> bool:
    """Evaluate whether post-action metrics satisfy the configured recovery criteria."""
    if not before_metrics or not after_metrics:
        return False

    after_success = float(after_metrics.get("success_rate", 0.0))
    if after_success >= RECOVERY_SUCCESS_RATE_TARGET:
        return True

    before_risk = Decimal(str(before_metrics.get("revenue_at_risk", "0")))
    after_risk = Decimal(str(after_metrics.get("revenue_at_risk", "0")))
    before_success = float(before_metrics.get("success_rate", 0.0))

    if before_risk > 0:
        risk_reduction = float(before_risk - after_risk) / float(before_risk)
        if risk_reduction >= RECOVERY_REVENUE_RISK_REDUCTION and after_success > before_success:
            return True

    return False


def rank_candidates(
    toolbox: SimulatorToolbox,
    candidates: list[dict[str, Any]],
    diagnosis_confidence: float,
    diagnosis: dict[str, Any] | None = None,
) -> list[tuple[dict[str, Any], float]]:
    """Rank candidate actions based on dynamic simulation projections, risks, and confidence."""
    current = toolbox.calculate_revenue_impact()
    current_success = current["success_rate"]
    current_rev = Decimal(current["revenue_at_risk"])
    affected_gateway = diagnosis.get("affected_gateway") if diagnosis else None

    ranked_results = []
    for action in candidates:
        sim_res = toolbox.simulate_action(action)
        if not sim_res["is_valid"]:
            continue

        projected_success = sim_res["projected_success_rate"]
        projected_rev = Decimal(sim_res["projected_revenue_at_risk"])

        success_improvement = max(0.0, projected_success - current_success)
        revenue_reduction = max(Decimal("0.00"), current_rev - projected_rev)

        success_score = success_improvement
        revenue_score = float(revenue_reduction / current_rev) if current_rev > 0 else 0.0

        # Action type specific risk assessment
        act_type = action["action_type"]
        if act_type == "ROUTE_TRAFFIC":
            risk = 0.1
        elif act_type == "REDUCE_GATEWAY_TRAFFIC":
            risk = 0.2
        elif act_type == "RATE_LIMIT_MERCHANT":
            risk = 0.6
        else:  # DISABLE_PAYMENT_METHOD
            risk = 0.8

        reversibility = 1.0  # All simulator actions are fully reversible

        # Calculate alignment with diagnosis
        diagnosis_alignment_score = 1.0
        if affected_gateway:
            targets_affected = False
            params = action.get("parameters", {})
            if act_type == "ROUTE_TRAFFIC":
                if params.get("source_gateway") == affected_gateway:
                    targets_affected = True
            elif act_type == "REDUCE_GATEWAY_TRAFFIC":
                if params.get("gateway") == affected_gateway:
                    targets_affected = True
            
            if targets_affected:
                diagnosis_alignment_score = 1.0
            else:
                diagnosis_alignment_score = 0.01

        # Calculate final generic action score
        score = (0.5 * success_score + 0.5 * revenue_score) * diagnosis_confidence * (1.0 - risk) * reversibility * diagnosis_alignment_score
        ranked_results.append((action, score))

    # Sort descending by score
    return sorted(ranked_results, key=lambda x: x[1], reverse=True)


class RecoveryAgent:
    """Operations control agent that evaluates and executes recovery actions using an LLM."""

    def __init__(self, provider: LLMProvider, toolbox: SimulatorToolbox, canary_policy: Any | None = None) -> None:
        self.provider = provider
        self.toolbox = toolbox
        self.canary_policy = canary_policy

    def run(self, run_id: str | None = None, max_iterations: int = 3, use_canary: bool = False) -> AgentTrace:
        """Run the AI recovery loop for the current incident observation."""
        run_id = run_id or f"RUN_{uuid.uuid4().hex[:8].upper()}"
        trace = AgentTrace(run_id=run_id, mode=determine_agent_mode())

        for it in range(max_iterations):
            trace.iteration = it + 1

            # 1. Observe state
            obs = self.toolbox.observe_result()
            trace.observations.append(obs)
            trace.observation_summary = {
                "current_time": obs["current_time"],
                "success_rate": obs["success_rate"],
                "revenue_at_risk": obs["revenue_at_risk"],
                "active_incidents": obs["active_incidents"]
            }

            if not obs["active_incidents"]:
                trace.reasoning_summary.append("No active incidents detected.")
                trace.decision = "STOP"
                trace.status = "NO_INCIDENT"
                break

            trace.incident_id = obs["active_incidents"][0]["incident_id"]

            # 2. Investigate & Confidence calculation
            confidence = calculate_diagnosis_confidence(obs)
            trace.diagnosis_confidence = confidence

            diagnosis = diagnose_incident(obs, confidence)
            trace.diagnosis = diagnosis

            # 3. Handle low confidence check
            if confidence < MIN_ACTION_CONFIDENCE:
                trace.reasoning_summary.append(
                    f"Low diagnosis confidence ({confidence:.2f} < {MIN_ACTION_CONFIDENCE:.2f}). Seeking low impact actions."
                )
                candidates = self.toolbox.list_available_actions()
                ranked = rank_candidates(self.toolbox, candidates, confidence, diagnosis)
                
                # Filter for low impact actions (traffic percentage <= 25%)
                low_impact_ranked = [
                    (act, score) for act, score in ranked
                    if float(act.get("parameters", {}).get("traffic_percentage", 100.0)) <= 25.0
                ]
                
                if not low_impact_ranked or low_impact_ranked[0][1] <= 0.0:
                    trace.decision = "STOP"
                    trace.status = "LOW_CONFIDENCE"
                    trace.selected_action = None
                    break
                else:
                    selected = low_impact_ranked[0][0]
            else:
                # High confidence pathway
                candidates = self.toolbox.list_available_actions()
                ranked = rank_candidates(self.toolbox, candidates, confidence, diagnosis)

                # Record candidate evaluations
                score_logs = []
                for act, score in ranked[:5]:
                    sim_res = self.toolbox.simulate_action(act)
                    score_logs.append({
                        "action": act,
                        "score": score,
                        "success_improvement": sim_res.get("projected_success_rate", 0.0) - obs["success_rate"],
                        "revenue_reduction": float(Decimal(obs["revenue_at_risk"]) - Decimal(sim_res.get("projected_revenue_at_risk", "0"))),
                        "risk": "LOW" if act["action_type"] in ["ROUTE_TRAFFIC", "REDUCE_GATEWAY_TRAFFIC"] else "HIGH",
                        "reversible": "YES",
                        "confidence": confidence,
                    })
                trace.candidate_scores = score_logs
                trace.candidate_actions.append([item[0] for item in ranked[:5]])

                if not ranked or ranked[0][1] <= 0.0:
                    trace.decision = "STOP"
                    trace.status = "NO_EFFECTIVE_ACTION"
                    break

                selected = ranked[0][0]

            # 4. Formulate select decision
            if trace.mode in ["MOCK", "REAL_PROVIDER"]:
                prompt = f"""
                Current observation: {json.dumps(trace.observation_summary)}
                Diagnosis: {json.dumps(diagnosis)}
                Ranked Candidates: {json.dumps(trace.candidate_scores)}
                Analyze parameters and select the best action.
                Return JSON format:
                {{
                    "reasoning": "Brief explanation...",
                    "selected_action": {{ "action_type": "ROUTE_TRAFFIC", ... }},
                    "confidence": 0.95
                }}
                """
                response_str = self.provider.generate(prompt)
                try:
                    res_json = json.loads(response_str)
                    selected = res_json.get("selected_action") or selected
                    reasoning = res_json.get("reasoning", "LLM action selected.")
                    trace.confidence = float(res_json.get("confidence", confidence))
                except Exception as e:
                    reasoning = f"LLM parsing failed: {e}"
                    trace.confidence = confidence
                
                trace.reasoning_summary.append(reasoning)
            else:
                # POLICY_FALLBACK mode
                trace.reasoning_summary.append(f"POLICY_FALLBACK: executing top rated action: {selected.get('explanation')}")
                trace.confidence = confidence

            if selected and "explanation" not in selected:
                selected["explanation"] = f"Recovery action selection based on diagnosis: {diagnosis['root_cause']}"

            trace.selected_action = selected
            trace.before_metrics = {
                "success_rate": obs["success_rate"],
                "revenue_at_risk": obs["revenue_at_risk"],
            }

            # Run counterfactual evaluation
            cf_eval = self.toolbox.evaluate_counterfactual(selected, horizon_steps=1, runs=20)
            trace.counterfactual_evaluation = cf_eval

            # Enforce Effectiveness Rule using confidence interval boundaries:
            # Configurable thresholds:
            ci_lower_sr = cf_eval.success_rate_ci[0]
            ci_lower_rev = cf_eval.confidence_interval[0]

            is_effective_sr = ci_lower_sr >= MIN_SUCCESS_RATE_IMPROVEMENT
            is_effective_rev = ci_lower_rev > MIN_REVENUE_RISK_REDUCTION

            if is_effective_sr or is_effective_rev:
                trace.reasoning_summary.append(
                    "Counterfactual simulation predicts materially lower revenue risk when "
                    "traffic is reduced on the degraded gateway."
                )

                # 5. Execute action (Canary mode or Direct mode)
                if use_canary:
                    from agent.canary import CanaryRecoveryController, CanaryOutcome
                    controller = CanaryRecoveryController(self.toolbox, self.canary_policy)
                    canary_res = controller.run_canary_pipeline(selected, auto_expand=True)
                    trace.canary_result = canary_res
                    trace.tool_calls.append({
                        "tool_name": "run_canary_pipeline",
                        "arguments": {"candidate_action": selected},
                        "result": {
                            "status": canary_res.status,
                            "stages_executed": len(canary_res.stages_executed),
                            "final_traffic_percentage": canary_res.current_traffic_percentage,
                            "reason": canary_res.decision_reason
                        }
                    })

                    obs_after = canary_res.observed_canary_metrics or self.toolbox.observe_result()
                    trace.after_metrics = {
                        "success_rate": obs_after.get("success_rate", 0.0),
                        "revenue_at_risk": obs_after.get("revenue_at_risk", "0.00"),
                    }

                    if canary_res.status == CanaryOutcome.CANARY_PASS.value:
                        trace.decision = "STOP"
                        trace.status = "RECOVERY_SUCCESSFUL"
                        trace.reasoning_summary.append(
                            f"Canary progressive recovery passed and expanded to {canary_res.current_traffic_percentage}%."
                        )
                        break
                    elif canary_res.status == CanaryOutcome.CANARY_FAIL.value:
                        trace.decision = "ROLLBACK"
                        trace.status = "FAILED_EFFECT"
                        trace.reasoning_summary.append(
                            f"Canary progressive recovery failed ({canary_res.decision_reason}). Rolled back."
                        )
                        break
                    else:
                        trace.decision = "STOP"
                        trace.status = "CANARY_INCONCLUSIVE"
                        trace.reasoning_summary.append(
                            f"Canary recovery inconclusive ({canary_res.decision_reason}). Traffic expansion halted."
                        )
                        break

                # Direct execution mode
                exec_res = self.toolbox.execute_action(selected)
                trace.tool_calls.append({
                    "tool_name": "execute_action",
                    "arguments": selected,
                    "result": exec_res,
                })
                trace.action_result = exec_res

                if exec_res["status"] == "REJECTED":
                    trace.decision = "ROLLBACK"
                    trace.status = "ACTION_REJECTED"
                    break

                # 6. Post action evaluation
                obs_after = exec_res["observation"]
                trace.after_metrics = {
                    "success_rate": obs_after["success_rate"],
                    "revenue_at_risk": obs_after["revenue_at_risk"],
                }

                # Add actual-vs-counterfactual prediction telemetry:
                pred_with = cf_eval.with_action
                actual_sr = float(obs_after["success_rate"])
                actual_rar = Decimal(str(obs_after["revenue_at_risk"]))

                trace.prediction_telemetry = {
                    "predicted_success_rate": pred_with.success_rate,
                    "actual_success_rate": actual_sr,
                    "success_rate_error": actual_sr - pred_with.success_rate,
                    "predicted_revenue_at_risk": str(pred_with.revenue_at_risk),
                    "actual_revenue_at_risk": str(actual_rar),
                    "revenue_at_risk_error": str(actual_rar - pred_with.revenue_at_risk),
                }

                # Check if recovery threshold achieved
                if is_recovery_successful(trace.before_metrics, trace.after_metrics):
                    trace.decision = "STOP"
                    trace.status = "RECOVERY_SUCCESSFUL"
                    break

                # If action degraded success rate or was ineffective, roll it back
                before_rate = float(trace.before_metrics["success_rate"])
                after_rate = float(trace.after_metrics["success_rate"])
                if after_rate <= before_rate:
                    trace.decision = "ROLLBACK"
                    trace.status = "FAILED_EFFECT"

                    action_id = exec_res.get("action_id") or exec_res.get("outcome", {}).get("action_id")
                    if action_id:
                        roll_res = self.toolbox.rollback_action(action_id)
                        trace.tool_calls.append({
                            "tool_name": "rollback_action",
                            "arguments": {"action_id": action_id},
                            "result": roll_res,
                        })
                    break
                else:
                    trace.decision = "CONTINUE"
                    trace.status = "IN_PROGRESS"
            else:
                trace.reasoning_summary.append(
                    f"Counterfactual evaluation predicts action is ineffective. "
                    f"Success rate improvement CI lower bound: {ci_lower_sr:.4f} < {MIN_SUCCESS_RATE_IMPROVEMENT:.4f}. "
                    f"Revenue risk reduction CI lower bound: {ci_lower_rev} <= {MIN_REVENUE_RISK_REDUCTION}."
                )
                trace.decision = "STOP"
                trace.status = "ACTION_INEFFECTIVE"
                break
                # Loop will advance to next iteration step

        return trace


class PolicyFallbackAgent:
    """Fallback agent that runs recovery actions based on deterministic candidate scoring."""

    def __init__(self, toolbox: SimulatorToolbox, canary_policy: Any | None = None) -> None:
        self.toolbox = toolbox
        self.canary_policy = canary_policy

    def run(self, run_id: str | None = None, max_iterations: int = 3, use_canary: bool = False) -> AgentTrace:
        """Run the fallback policy agent."""
        # PolicyFallbackAgent utilizes RecoveryAgent but forces POLICY_FALLBACK mode
        os.environ["LLM_PROVIDER"] = "mock"
        # Temporarily clear key to force fallback mode
        old_key = os.environ.pop("LLM_API_KEY", None)
        old_provider = os.environ.get("LLM_PROVIDER")
        os.environ["LLM_PROVIDER"] = "fallback" # forces POLICY_FALLBACK

        agent = RecoveryAgent(None, self.toolbox, self.canary_policy)
        try:
            trace = agent.run(run_id, max_iterations, use_canary=use_canary)
        finally:
            if old_key is not None:
                os.environ["LLM_API_KEY"] = old_key
            if old_provider is not None:
                os.environ["LLM_PROVIDER"] = old_provider
            else:
                os.environ.pop("LLM_PROVIDER", None)

        return trace


def evaluate_agent_recovery(trace: AgentTrace) -> EvaluationMetrics:
    """Evaluate trace outcome metrics."""
    metrics = EvaluationMetrics()

    if trace.incident_id:
        metrics.incident_detected = True

    if trace.selected_action:
        metrics.action_selected = True

    if trace.action_result and trace.action_result.get("status") == "ACCEPTED":
        metrics.action_accepted = True

    if trace.before_metrics and trace.after_metrics:
        before_rate = float(trace.before_metrics["success_rate"])
        after_rate = float(trace.after_metrics["success_rate"])
        metrics.success_rate_improvement = max(0.0, after_rate - before_rate)

        before_risk = Decimal(str(trace.before_metrics["revenue_at_risk"]))
        after_risk = Decimal(str(trace.after_metrics["revenue_at_risk"]))
        metrics.revenue_at_risk_reduction = max(Decimal("0.00"), before_risk - after_risk)
        metrics.estimated_recovered_revenue = metrics.revenue_at_risk_reduction

        if after_rate > before_rate:
            metrics.recovery_achieved = True

    if trace.decision == "ROLLBACK":
        metrics.rollback_rate = 1.0

    if trace.selected_action and not trace.incident_id:
        metrics.unnecessary_action_rate = 1.0

    metrics.time_to_recovery_seconds = 300.0 if metrics.recovery_achieved else 0.0

    return metrics
