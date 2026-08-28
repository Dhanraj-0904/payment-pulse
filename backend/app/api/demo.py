import os
import json
import uuid
import threading
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.simulator_adapter import get_simulator_adapter
from simulator.simulator_adapter import SimulatorAdapter
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.injector import _rng_for_incident
from simulator.generator import generate_transactions
from agent.events import PaymentEvent
from agent.event_bus import event_bus
from agent.recovery_agent import PolicyFallbackAgent
from agent.tools import SimulatorToolbox

router = APIRouter(prefix="/api/demo", tags=["demo"])

class IncidentRequest(BaseModel):
    incident_type: str  # GATEWAY_DEGRADATION, BANK_FAILURE, NETWORK_DEGRADATION
    target: str

class TrafficRunner:
    def __init__(self):
        self.running = False
        self.thread = None
        self.tps = 2.0
        self.adapter: Optional[SimulatorAdapter] = None
        self.step_interval = 5.0  # Real-time interval in seconds representing 5 virtual minutes
        self.last_step_time = 0.0

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
        # Infinite transaction wrapper generator
        config = self.adapter.simulator.generator_config
        base_gen = generate_transactions(config)

        # Load PaySim profile calibration if exists
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

            # Apply calibration factors if loaded
            amount = float(raw_tx.amount)
            method = raw_tx.payment_method
            if profile:
                # Calibrate amount (bounded stochastic distribution)
                amount = float(round(random.uniform(profile["min_amount"], profile["max_amount"]), 2))
                # Calibrate method selection based on ratios
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

            # Check if it's time to advance simulator steps
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

        # Advance the stateful simulator by 1 step (5 minutes)
        obs, outcome = self.adapter.simulator.step()

        # Build live metrics update event
        rev_evt = PaymentEvent(
            event_type="REVENUE_RISK_UPDATED",
            amount=float(obs.revenue_at_risk),
            status="WARNING" if obs.revenue_at_risk > 0 else "HEALTHY",
            metadata={
                "success_rate": obs.success_rate,
                "failure_rate": obs.failure_rate,
                "latency_ms": obs.latency,
                "transaction_volume": obs.transaction_volume
            }
        )
        event_bus.publish(rev_evt)

        # Build gateway health updates and detect anomalies
        if obs.active_incidents:
            for inc in obs.active_incidents:
                # Determine affected entity and entity type
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

                from agent.recovery_agent import calculate_diagnosis_confidence
                confidence = calculate_diagnosis_confidence(obs)

                # Publish incident alert
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
                        "started_at": inc.get("start_time"),
                    }
                )
                event_bus.publish(inc_evt)

            # TODO: Move confidence bounds thresholds to env configurations instead of hardcoding
            # Trigger the AI agent recovery loop automatically
            toolbox = SimulatorToolbox(self.adapter.simulator)
            fallback = PolicyFallbackAgent(toolbox)

            # Broadcast recovery proposing state
            prop_evt = PaymentEvent(
                event_type="RECOVERY_ACTION_PROPOSED",
                status="PROPOSED",
                metadata={"status": "EVALUATING_ACTIONS"}
            )
            event_bus.publish(prop_evt)

            trace = fallback.run()

            # Broadcast selected action execution
            if trace.selected_action:
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

                exec_evt = PaymentEvent(
                    event_type="RECOVERY_ACTION_EXECUTED",
                    gateway=act["parameters"].get("gateway") or act["parameters"].get("source_gateway"),
                    status="EXECUTED",
                    metadata={
                        "action_type": act["action_type"],
                        "parameters": act["parameters"],
                        "explanation": act["explanation"],
                        "counterfactual_evaluation": cf_eval_data,
                        "prediction_telemetry": trace.prediction_telemetry
                    }
                )
                event_bus.publish(exec_evt)

            if trace.status == "RECOVERY_SUCCESSFUL":
                comp_evt = PaymentEvent(
                    event_type="RECOVERY_COMPLETED",
                    status="SUCCESS",
                    metadata={
                        "before_success_rate": trace.before_metrics["success_rate"],
                        "after_success_rate": trace.after_metrics["success_rate"]
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
def get_traffic_status():
    return {"running": traffic_runner.running, "tps": traffic_runner.tps}

@router.post("/incidents")
def inject_incident(req: IncidentRequest, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    try:
        inc_type = req.incident_type
        target = req.target

        # Construct incident config matching simulator timeline
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

        # Inject into active simulator
        adapter.simulator.incidents_config.append(config)
        adapter.simulator.incident_rngs[config.incident_id] = _rng_for_incident(
            adapter.simulator.incident_seed, config.incident_id
        )

        return {"status": "injected", "incident_id": inc_id, "type": inc_type, "target": target}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reset")
def reset_system(adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    try:
        # Stop traffic runner
        traffic_runner.stop()

        # Clear active and configs
        adapter.simulator.incidents_config.clear()
        adapter.simulator.incident_rngs.clear()
        adapter.simulator.active_actions.clear()
        adapter.simulator.action_history.clear()
        adapter.simulator.last_step_transactions.clear()
        adapter.simulator.prior_step_transactions.clear()

        # Reset timeline
        adapter.simulator.reset()
        adapter.simulator.step()  # Initial baseline window step

        # Clear event bus history
        event_bus.event_history.clear()

        # Publish healthy reset confirmation event
        reset_evt = PaymentEvent(
            event_type="RECOVERY_COMPLETED",
            status="HEALTHY",
            metadata={"message": "System reset to normal baseline successfully"}
        )
        event_bus.publish(reset_evt)

        return {"status": "reset_completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
