import os
import json
import uuid
import threading
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.simulator_adapter import get_simulator_adapter
from simulator.simulator_adapter import SimulatorAdapter
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.injector import _rng_for_incident
from simulator.generator import generate_transactions
from agent.events import PaymentEvent
from agent.event_bus import event_bus
from agent.recovery_agent import (
    PolicyFallbackAgent,
    SimulatorToolbox,
    calculate_diagnosis_confidence,
    diagnose_incident,
    rank_candidates,
    determine_agent_mode
)

router = APIRouter(prefix="/api/demo", tags=["demo"])

class IncidentRequest(BaseModel):
    incident_type: str  # GATEWAY_DEGRADATION, BANK_FAILURE, NETWORK_DEGRADATION
    target: str

class SimulateActionRequest(BaseModel):
    action: dict[str, Any]

class ExecuteActionRequest(BaseModel):
    action: dict[str, Any]

class CanaryRunRequest(BaseModel):
    action: dict[str, Any]
    auto_expand: bool = True
    initial_percentage: Optional[float] = None

class TrafficRunner:
    def __init__(self):
        self.running = False
        self.thread = None
        self.tps = 2.0
        self.adapter: Optional[SimulatorAdapter] = None
        self.step_interval = 5.0  # Real-time interval in seconds representing 5 virtual minutes
        self.last_step_time = 0.0
        self.auto_recovery = False  # Allows manual operator analysis by default

    def start(self, adapter: SimulatorAdapter):
        if not self.running:
            self.adapter = adapter
            self.running = True
            self.last_step_time = time.time()
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None

    def _loop(self):
        config = self.adapter.simulator.generator_config
        base_gen = generate_transactions(config)

        profile = None
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        profile_path = os.path.join(root_dir, "data", "processed", "paysim_profile.json")
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r") as f:
                    profile = json.load(f)
            except Exception:
                pass

        while self.running:
            start_time = time.time()

            try:
                raw_tx = next(base_gen)
            except StopIteration:
                base_gen = generate_transactions(config)
                raw_tx = next(base_gen)

            amount = float(raw_tx.amount)
            method = raw_tx.payment_method
            if profile:
                amount = float(round(random.uniform(profile["min_amount"], profile["max_amount"]), 2))
                ratios = profile.get("payment_method_ratios", {})
                if ratios:
                    methods_list = list(ratios.keys())
                    weights = list(ratios.values())
                    method = random.choices(methods_list, weights=weights, k=1)[0]

            try:
                pay = self.adapter.create_payment(
                    amount=amount,
                    currency="INR",
                    payment_method=method,
                    bank=raw_tx.bank,
                    merchant=raw_tx.merchant_id
                )
                self.adapter.process_payment(pay["transaction_id"])
            except Exception:
                pass

            now = time.time()
            if now - self.last_step_time >= self.step_interval:
                self.last_step_time = now
                self._step_simulator()

            elapsed = time.time() - start_time
            sleep_time = max(0.01, (1.0 / self.tps) - elapsed)
            time.sleep(sleep_time)

    def _step_simulator(self):
        if not self.adapter:
            return

        # Advance stateful simulator by 1 step (5 virtual minutes)
        obs, outcome = self.adapter.simulator.step()
        sim_time_iso = self.adapter.simulator.simulation_time.isoformat()

        # Calculate exact 5-minute windowed TPS: transaction_count / 300
        window_tps = round(obs.transaction_volume / 300.0, 1)

        # Build live metrics update event
        rev_evt = PaymentEvent(
            event_type="REVENUE_RISK_UPDATED",
            amount=float(obs.revenue_at_risk),
            status="WARNING" if obs.revenue_at_risk > 0 else "HEALTHY",
            metadata={
                "success_rate": obs.success_rate,
                "failure_rate": obs.failure_rate,
                "latency_ms": obs.latency,
                "transaction_volume": obs.transaction_volume,
                "sim_time": sim_time_iso,
                "window_duration_seconds": 300,
                "tps": window_tps
            }
        )
        event_bus.publish(rev_evt)

        # Broadcast active incidents if detected
        if obs.active_incidents:
            for inc in obs.active_incidents:
                affected_entity = None
                affected_entity_type = None
                inc_type = inc.get("incident_type")

                if inc_type == "GATEWAY_DEGRADATION":
                    affected_entity = inc.get("affected_gateway")
                    affected_entity_type = "GATEWAY"
                elif inc_type == "BANK_UPI_TIMEOUT":
                    affected_entity = inc.get("affected_bank")
                    affected_entity_type = "BANK"
                elif inc_type == "REGIONAL_NETWORK_DEGRADATION":
                    affected_entity = inc.get("affected_location")
                    affected_entity_type = "LOCATION"
                elif inc_type == "MERCHANT_SPECIFIC_FAILURE":
                    affected_entity = inc.get("affected_merchant")
                    affected_entity_type = "MERCHANT"
                elif inc_type == "CARD_AUTH_FAILURE":
                    affected_entity = inc.get("affected_bank") or inc.get("affected_gateway") or "CARD"
                    affected_entity_type = "BANK" if inc.get("affected_bank") else ("GATEWAY" if inc.get("affected_gateway") else "PAYMENT_METHOD")

                confidence = calculate_diagnosis_confidence(obs)
                start_t = inc.get("start_time")
                start_iso = start_t.isoformat() if hasattr(start_t, "isoformat") else str(start_t)

                inc_evt = PaymentEvent(
                    event_type="INCIDENT_DETECTED",
                    gateway=inc.get("affected_gateway") if inc_type in ["GATEWAY_DEGRADATION", "CARD_AUTH_FAILURE"] else None,
                    bank=inc.get("affected_bank") if inc_type in ["BANK_UPI_TIMEOUT", "CARD_AUTH_FAILURE"] else None,
                    payment_method=inc.get("affected_payment_method") if inc_type in ["BANK_UPI_TIMEOUT", "CARD_AUTH_FAILURE"] else None,
                    merchant=inc.get("affected_merchant") if inc_type == "MERCHANT_SPECIFIC_FAILURE" else None,
                    location=inc.get("affected_location") if inc_type == "REGIONAL_NETWORK_DEGRADATION" else None,
                    status="CRITICAL",
                    metadata={
                        "incident_id": inc.get("incident_id"),
                        "incident_type": inc.get("incident_type"),
                        "severity": inc.get("severity"),
                        "anomaly_score": obs.anomaly_score,
                        "confidence": confidence,
                        "affected_entity": affected_entity,
                        "affected_entity_type": affected_entity_type,
                        "started_at": start_iso,
                        "sim_time": sim_time_iso,
                    }
                )
                event_bus.publish(inc_evt)

            # If auto-recovery is enabled, run PolicyFallbackAgent
            if self.auto_recovery:
                toolbox = SimulatorToolbox(self.adapter.simulator)
                fallback = PolicyFallbackAgent(toolbox)
                trace = fallback.run()
                if trace.selected_action:
                    self._broadcast_executed_action(trace)

    def _broadcast_executed_action(self, trace):
        act = trace.selected_action
        cf_eval_data = None
        if trace.counterfactual_evaluation:
            cf = trace.counterfactual_evaluation
            cf_eval_data = {
                "evaluation_id": cf.evaluation_id,
                "action_id": cf.action_id,
                "horizon_steps": cf.horizon_steps,
                "runs": cf.runs,
                "with_action": {
                    "success_rate": cf.with_action.success_rate,
                    "failure_rate": cf.with_action.failure_rate,
                    "average_latency": cf.with_action.average_latency,
                    "revenue_at_risk": float(cf.with_action.revenue_at_risk),
                    "failed_amount": float(cf.with_action.failed_amount)
                },
                "without_action": {
                    "success_rate": cf.without_action.success_rate,
                    "failure_rate": cf.without_action.failure_rate,
                    "average_latency": cf.without_action.average_latency,
                    "revenue_at_risk": float(cf.without_action.revenue_at_risk),
                    "failed_amount": float(cf.without_action.failed_amount)
                },
                "effect": {
                    "success_rate_improvement": cf.effect.success_rate_improvement,
                    "failure_rate_reduction": cf.effect.failure_rate_reduction,
                    "revenue_risk_reduction": float(cf.effect.revenue_risk_reduction)
                },
                "confidence_interval_lower": float(cf.confidence_interval[0]),
                "confidence_interval_upper": float(cf.confidence_interval[1]),
                "success_rate_ci_lower": cf.success_rate_ci[0],
                "success_rate_ci_upper": cf.success_rate_ci[1]
            }

        sim_time_iso = self.adapter.simulator.simulation_time.isoformat() if self.adapter else None

        exec_evt = PaymentEvent(
            event_type="RECOVERY_ACTION_EXECUTED",
            gateway=act["parameters"].get("gateway") or act["parameters"].get("source_gateway"),
            status="EXECUTED",
            metadata={
                "action_type": act["action_type"],
                "parameters": act["parameters"],
                "explanation": act["explanation"],
                "counterfactual_evaluation": cf_eval_data,
                "prediction_telemetry": trace.prediction_telemetry,
                "agent_mode": trace.mode,
                "sim_time": sim_time_iso
            }
        )
        event_bus.publish(exec_evt)

        if trace.status == "RECOVERY_SUCCESSFUL":
            comp_evt = PaymentEvent(
                event_type="RECOVERY_COMPLETED",
                status="SUCCESS",
                metadata={
                    "before_success_rate": trace.before_metrics.get("success_rate", 0.0),
                    "after_success_rate": trace.after_metrics.get("success_rate", 1.0),
                    "sim_time": sim_time_iso
                }
            )
            event_bus.publish(comp_evt)

traffic_runner = TrafficRunner()

@router.post("/traffic/start")
def start_traffic(adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    traffic_runner.start(adapter)
    return {"status": "started", "tps": traffic_runner.tps}

@router.post("/traffic/stop")
def stop_traffic():
    traffic_runner.stop()
    return {"status": "stopped"}

@router.get("/traffic/status")
def get_traffic_status(adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    sim_time = adapter.simulator.simulation_time.isoformat() if adapter and hasattr(adapter, "simulator") else None
    return {
        "running": traffic_runner.running,
        "tps": traffic_runner.tps,
        "sim_time": sim_time
    }

@router.post("/incidents")
def inject_incident(req: IncidentRequest, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    try:
        inc_type = req.incident_type
        target = req.target

        inc_id = f"INC_{uuid.uuid4().hex[:6].upper()}"
        start_time = adapter.simulator.simulation_time

        if inc_type == "GATEWAY_DEGRADATION":
            config = IncidentConfig(
                incident_id=inc_id,
                incident_type=IncidentType.GATEWAY_DEGRADATION,
                start_time=start_time,
                duration_minutes=20,
                recovery_minutes=0,
                severity=Severity.HIGH,
                affected_gateway=target,
                failure_rate_multiplier=6.0,
                latency_multiplier=3.0,
                affected_transaction_percentage=1.0,
                description=f"Gateway degradation injected on {target}"
            )
        elif inc_type == "BANK_FAILURE":
            config = IncidentConfig(
                incident_id=inc_id,
                incident_type=IncidentType.BANK_UPI_TIMEOUT,
                start_time=start_time,
                duration_minutes=20,
                recovery_minutes=0,
                severity=Severity.HIGH,
                affected_bank=target,
                affected_payment_method="UPI",
                failure_rate_multiplier=8.0,
                latency_multiplier=4.0,
                affected_transaction_percentage=1.0,
                description=f"Bank UPI timeouts injected on {target}"
            )
        elif inc_type == "NETWORK_DEGRADATION":
            config = IncidentConfig(
                incident_id=inc_id,
                incident_type=IncidentType.REGIONAL_NETWORK_DEGRADATION,
                start_time=start_time,
                duration_minutes=20,
                recovery_minutes=0,
                severity=Severity.HIGH,
                affected_location=target,
                failure_rate_multiplier=5.0,
                latency_multiplier=3.5,
                affected_transaction_percentage=1.0,
                description=f"Regional network degradation injected in {target}"
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported incident type: {inc_type}")

        adapter.simulator.incidents_config.append(config)
        adapter.simulator.incident_rngs[config.incident_id] = _rng_for_incident(
            adapter.simulator.incident_seed, config.incident_id
        )

        return {
            "status": "injected",
            "incident_id": inc_id,
            "type": inc_type,
            "target": target,
            "sim_time": start_time.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/recovery/candidates")
def get_recovery_candidates(adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    """Generate and rank candidate recovery actions for the current incident state."""
    try:
        toolbox = SimulatorToolbox(adapter.simulator)
        obs = toolbox.observe_result()
        
        if not obs.get("active_incidents"):
            return {
                "active_incident": None,
                "candidates": [],
                "diagnosis": None,
                "confidence": 0.0,
                "agent_mode": determine_agent_mode()
            }

        confidence = calculate_diagnosis_confidence(obs)
        diagnosis = diagnose_incident(obs, confidence)
        candidates = toolbox.list_available_actions()
        ranked = rank_candidates(toolbox, candidates, confidence, diagnosis)

        formatted_candidates = []
        for act, score in ranked[:5]:
            sim_res = toolbox.simulate_action(act)
            exp_sr_imp = sim_res.get("projected_success_rate", 0.0) - obs.get("success_rate", 0.0)
            exp_rev_red = Decimal(obs.get("revenue_at_risk", "0")) - Decimal(sim_res.get("projected_revenue_at_risk", "0"))
            
            target = (
                act.get("parameters", {}).get("gateway") or
                act.get("parameters", {}).get("source_gateway") or
                act.get("parameters", {}).get("affected_bank") or
                act.get("parameters", {}).get("payment_method") or
                "SYSTEM"
            )
            pct = float(act.get("parameters", {}).get("traffic_percentage", 100.0))

            formatted_candidates.append({
                "action_id": f"{act['action_type']}_{target}_{int(pct)}",
                "action": act,
                "action_type": act["action_type"],
                "target": target,
                "traffic_percentage": pct,
                "expected_success_improvement": round(exp_sr_imp * 100, 1),
                "expected_revenue_risk_reduction": float(max(Decimal("0.0"), exp_rev_red)),
                "blast_radius": "LOW" if pct <= 50.0 else "MEDIUM",
                "confidence": round(confidence * 100, 0),
                "score": round(score, 3),
                "reversible": "YES",
                "explanation": act.get("explanation", f"Mitigate {diagnosis.get('root_cause', 'incident')}")
            })

        return {
            "active_incident": obs["active_incidents"][0],
            "diagnosis": diagnosis,
            "confidence": confidence,
            "agent_mode": determine_agent_mode(),
            "candidates": formatted_candidates
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/recovery/simulate")
def simulate_recovery_candidate(req: SimulateActionRequest, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    """Run counterfactual simulation for a candidate action without executing it."""
    try:
        toolbox = SimulatorToolbox(adapter.simulator)
        cf = toolbox.evaluate_counterfactual(req.action, horizon_steps=1, runs=20)
        
        return {
            "evaluation_id": cf.evaluation_id,
            "action_id": cf.action_id,
            "horizon_steps": cf.horizon_steps,
            "runs": cf.runs,
            "with_action": {
                "success_rate": cf.with_action.success_rate,
                "failure_rate": cf.with_action.failure_rate,
                "average_latency": cf.with_action.average_latency,
                "revenue_at_risk": float(cf.with_action.revenue_at_risk),
                "failed_amount": float(cf.with_action.failed_amount)
            },
            "without_action": {
                "success_rate": cf.without_action.success_rate,
                "failure_rate": cf.without_action.failure_rate,
                "average_latency": cf.without_action.average_latency,
                "revenue_at_risk": float(cf.without_action.revenue_at_risk),
                "failed_amount": float(cf.without_action.failed_amount)
            },
            "effect": {
                "success_rate_improvement": cf.effect.success_rate_improvement,
                "failure_rate_reduction": cf.effect.failure_rate_reduction,
                "revenue_risk_reduction": float(cf.effect.revenue_risk_reduction)
            },
            "confidence_interval_lower": float(cf.confidence_interval[0]),
            "confidence_interval_upper": float(cf.confidence_interval[1]),
            "success_rate_ci_lower": cf.success_rate_ci[0],
            "success_rate_ci_upper": cf.success_rate_ci[1]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/recovery/execute")
def execute_recovery_action(req: ExecuteActionRequest, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    """Execute recovery action subject to policy verification."""
    try:
        toolbox = SimulatorToolbox(adapter.simulator)
        obs_before = toolbox.observe_result()

        # Counterfactual check before execution
        cf = toolbox.evaluate_counterfactual(req.action, horizon_steps=1, runs=20)
        
        # Execute action through policy layer
        exec_res = toolbox.execute_action(req.action)
        if exec_res["status"] == "REJECTED":
            raise HTTPException(status_code=400, detail=f"Policy rejected action: {exec_res.get('reason')}")

        obs_after = exec_res["observation"]
        sim_time_iso = adapter.simulator.simulation_time.isoformat()

        # Compute actual prediction telemetry
        pred_with = cf.with_action
        actual_sr = float(obs_after["success_rate"])
        actual_rar = Decimal(str(obs_after["revenue_at_risk"]))

        pred_telemetry = {
            "predicted_success_rate": pred_with.success_rate,
            "actual_success_rate": actual_sr,
            "success_rate_error": actual_sr - pred_with.success_rate,
            "predicted_revenue_at_risk": str(pred_with.revenue_at_risk),
            "actual_revenue_at_risk": str(actual_rar),
            "revenue_at_risk_error": str(actual_rar - pred_with.revenue_at_risk)
        }

        cf_eval_data = {
            "evaluation_id": cf.evaluation_id,
            "action_id": cf.action_id,
            "horizon_steps": cf.horizon_steps,
            "runs": cf.runs,
            "with_action": {
                "success_rate": cf.with_action.success_rate,
                "failure_rate": cf.with_action.failure_rate,
                "average_latency": cf.with_action.average_latency,
                "revenue_at_risk": float(cf.with_action.revenue_at_risk),
                "failed_amount": float(cf.with_action.failed_amount)
            },
            "without_action": {
                "success_rate": cf.without_action.success_rate,
                "failure_rate": cf.without_action.failure_rate,
                "average_latency": cf.without_action.average_latency,
                "revenue_at_risk": float(cf.without_action.revenue_at_risk),
                "failed_amount": float(cf.without_action.failed_amount)
            },
            "effect": {
                "success_rate_improvement": cf.effect.success_rate_improvement,
                "failure_rate_reduction": cf.effect.failure_rate_reduction,
                "revenue_risk_reduction": float(cf.effect.revenue_risk_reduction)
            },
            "confidence_interval_lower": float(cf.confidence_interval[0]),
            "confidence_interval_upper": float(cf.confidence_interval[1]),
            "success_rate_ci_lower": cf.success_rate_ci[0],
            "success_rate_ci_upper": cf.success_rate_ci[1]
        }

        # Broadcast executed event
        exec_evt = PaymentEvent(
            event_type="RECOVERY_ACTION_EXECUTED",
            gateway=req.action["parameters"].get("gateway") or req.action["parameters"].get("source_gateway"),
            status="EXECUTED",
            metadata={
                "action_type": req.action["action_type"],
                "parameters": req.action["parameters"],
                "explanation": req.action.get("explanation", "Manual operator recovery execution"),
                "counterfactual_evaluation": cf_eval_data,
                "prediction_telemetry": pred_telemetry,
                "agent_mode": determine_agent_mode(),
                "sim_time": sim_time_iso
            }
        )
        event_bus.publish(exec_evt)

        # Broadcast recovery completion if improved
        is_recovered = actual_sr >= 0.85 or actual_sr > float(obs_before.get("success_rate", 0.0))
        if is_recovered:
            comp_evt = PaymentEvent(
                event_type="RECOVERY_COMPLETED",
                status="SUCCESS",
                metadata={
                    "before_success_rate": obs_before.get("success_rate", 0.0),
                    "after_success_rate": actual_sr,
                    "sim_time": sim_time_iso
                }
            )
            event_bus.publish(comp_evt)

        return {
            "status": "EXECUTED",
            "action": req.action,
            "before_metrics": {"success_rate": obs_before.get("success_rate"), "revenue_at_risk": obs_before.get("revenue_at_risk")},
            "after_metrics": {"success_rate": obs_after.get("success_rate"), "revenue_at_risk": obs_after.get("revenue_at_risk")},
            "counterfactual_evaluation": cf_eval_data,
            "prediction_telemetry": pred_telemetry
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/recovery/canary/policy")
def get_canary_policy():
    """Retrieve current canary recovery policy configuration."""
    from agent.canary import CanaryPolicy
    policy = CanaryPolicy()
    return {
        "initial_traffic_percentage": policy.initial_traffic_percentage,
        "traffic_stages": policy.traffic_stages,
        "max_traffic_percentage": policy.max_traffic_percentage,
        "min_observation_windows": policy.min_observation_windows,
        "min_success_rate_threshold": policy.min_success_rate_threshold,
        "max_latency_ms": policy.max_latency_ms,
        "max_revenue_risk": str(policy.max_revenue_risk),
        "max_allowed_blast_radius": policy.max_allowed_blast_radius
    }

@router.post("/recovery/canary/run")
def run_canary_recovery(req: CanaryRunRequest, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    """Run progressive Canary Recovery (Control -> Counterfactual -> Canary 5% -> Conditional Expansion)."""
    try:
        from agent.canary import CanaryRecoveryController, CanaryPolicy
        toolbox = SimulatorToolbox(adapter.simulator)
        policy = CanaryPolicy()
        if req.initial_percentage is not None and req.initial_percentage > 0:
            policy.initial_traffic_percentage = req.initial_percentage
            policy.traffic_stages = [req.initial_percentage] + [s for s in policy.traffic_stages if s > req.initial_percentage]

        controller = CanaryRecoveryController(toolbox, policy)

        # Broadcast canary initiated event
        sim_time_iso = adapter.simulator.simulation_time.isoformat()
        start_evt = PaymentEvent(
            event_type="CANARY_STARTED",
            status="IN_PROGRESS",
            metadata={
                "action_type": req.action.get("action_type"),
                "initial_percentage": policy.initial_traffic_percentage,
                "stages": policy.traffic_stages,
                "sim_time": sim_time_iso
            }
        )
        event_bus.publish(start_evt)

        canary_res = controller.run_canary_pipeline(req.action, auto_expand=req.auto_expand)

        # Broadcast canary evaluation outcome event
        eval_evt = PaymentEvent(
            event_type="CANARY_EVALUATED",
            status=canary_res.status,
            metadata={
                "outcome": canary_res.status,
                "stages_executed": len(canary_res.stages_executed),
                "final_traffic_percentage": canary_res.current_traffic_percentage,
                "rolled_back": canary_res.rolled_back,
                "reason": canary_res.decision_reason,
                "sim_time": sim_time_iso
            }
        )
        event_bus.publish(eval_evt)

        # Format counterfactual evaluation if present
        cf_dict = None
        if canary_res.counterfactual_prediction:
            cf = canary_res.counterfactual_prediction
            cf_dict = {
                "evaluation_id": cf.evaluation_id,
                "runs": cf.runs,
                "with_action": {
                    "success_rate": cf.with_action.success_rate,
                    "failure_rate": cf.with_action.failure_rate,
                    "average_latency": cf.with_action.average_latency,
                    "revenue_at_risk": float(cf.with_action.revenue_at_risk),
                },
                "without_action": {
                    "success_rate": cf.without_action.success_rate,
                    "failure_rate": cf.without_action.failure_rate,
                    "average_latency": cf.without_action.average_latency,
                    "revenue_at_risk": float(cf.without_action.revenue_at_risk),
                },
                "effect": {
                    "success_rate_improvement": cf.effect.success_rate_improvement,
                    "revenue_risk_reduction": float(cf.effect.revenue_risk_reduction),
                },
                "success_rate_ci": [cf.success_rate_ci[0], cf.success_rate_ci[1]],
                "confidence_interval": [float(cf.confidence_interval[0]), float(cf.confidence_interval[1])],
            }

        return {
            "canary_id": canary_res.canary_id,
            "status": canary_res.status,
            "current_stage": canary_res.current_stage.value,
            "current_traffic_percentage": canary_res.current_traffic_percentage,
            "decision_reason": canary_res.decision_reason,
            "rolled_back": canary_res.rolled_back,
            "active_action_id": canary_res.active_action_id,
            "three_layer_comparison": {
                "layer_1_control": canary_res.control_metrics,
                "layer_2_counterfactual": cf_dict,
                "layer_3_observed_canary": canary_res.observed_canary_metrics
            },
            "stages": [
                {
                    "stage_index": s.stage_index,
                    "traffic_percentage": s.traffic_percentage,
                    "outcome": s.outcome.value,
                    "reason": s.reason,
                    "success_rate": s.observation_after.get("success_rate", 0.0),
                    "latency": s.observation_after.get("latency", 0.0),
                    "revenue_at_risk": str(s.observation_after.get("revenue_at_risk", "0.00"))
                }
                for s in canary_res.stages_executed
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reset")
def reset_system(adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    try:
        traffic_runner.stop()

        adapter.simulator.incidents_config.clear()
        adapter.simulator.incident_rngs.clear()
        adapter.simulator.active_actions.clear()
        adapter.simulator.action_history.clear()
        adapter.simulator.last_step_transactions.clear()
        adapter.simulator.prior_step_transactions.clear()

        adapter.simulator.reset()
        adapter.simulator.step()

        event_bus.event_history.clear()

        sim_time_iso = adapter.simulator.simulation_time.isoformat()

        reset_evt = PaymentEvent(
            event_type="RECOVERY_COMPLETED",
            status="HEALTHY",
            metadata={
                "message": "System reset to normal baseline successfully",
                "sim_time": sim_time_iso
            }
        )
        event_bus.publish(reset_evt)

        return {"status": "reset_completed", "sim_time": sim_time_iso}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
