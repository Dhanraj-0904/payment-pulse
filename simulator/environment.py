"""Stateful payment recovery simulation environment for AI agent evaluation."""

import uuid
import random
import hashlib
import copy
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import dataclass, asdict, replace
from typing import Any, Iterable

from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType
from simulator.generator import generate_transactions
from simulator.injector import _apply_record, _rng_for_incident
from simulator.schema import TransactionRecord
from simulator.profiles import BANKS, GATEWAYS, PAYMENT_METHODS, MERCHANTS

from ml.config import DetectionConfig
from ml.anomaly import detect_payment_incidents, fit_baseline
from ml.evidence import generate_investigation_evidence, InvestigationEvidence
from ml.revenue import calculate_revenue_at_risk, calculate_revenue_for_incidents
from ml.incident_detection import DetectedIncident

VALID_BANKS = {b.name for b in BANKS}
VALID_GATEWAYS = {g.name for g in GATEWAYS}
VALID_METHODS = {m[0].name for m in PAYMENT_METHODS}
VALID_MERCHANTS = {m[0].name for m in MERCHANTS}


@dataclass(frozen=True, slots=True)
class SimulationState:
    simulation_time: datetime
    active_incidents: list[str]
    gateway_health: dict[str, float]
    bank_health: dict[str, float]
    payment_method_health: dict[str, float]
    merchant_health: dict[str, float]
    regional_health: dict[str, float]
    transaction_count: int
    success_count: int
    failure_count: int
    success_rate: float
    failure_rate: float
    average_latency: float
    revenue_at_risk: Decimal


@dataclass(frozen=True, slots=True)
class Observation:
    current_time: str
    transaction_volume: int
    success_rate: float
    failure_rate: float
    latency: float
    active_incidents: list[dict[str, Any]]
    top_affected_segments: list[dict[str, Any]]
    anomaly_score: float
    investigation_evidence: list[dict[str, Any]]
    revenue_at_risk: Decimal
    recent_actions: list[dict[str, Any]]
    action_outcomes: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ActionResult:
    accepted: bool
    rejected: bool
    reason: str
    action_id: str


class StatefulSimulator:
    """Deterministic, stateful payment reliability simulation environment."""

    def __init__(
        self,
        generator_config: GeneratorConfig,
        incidents_config: list[IncidentConfig],
        incident_seed: int = 42,
        baseline_transactions: list[TransactionRecord] | None = None,
    ) -> None:
        self.generator_config = generator_config
        self.incidents_config = incidents_config
        self.incident_seed = incident_seed
        self.baseline_transactions = baseline_transactions

        self.simulation_time = generator_config.start_timestamp
        self.active_actions: list[dict[str, Any]] = []
        self.action_history: list[dict[str, Any]] = []
        self.baseline_iterator: Any = None
        self.incident_rngs: dict[str, random.Random] = {}
        self.baseline_model: Any = None
        self.ml_config = DetectionConfig()
        self.last_step_transactions: list[TransactionRecord] = []
        self.prior_step_transactions: list[TransactionRecord] = []

        self._initialize_baseline()

    def _initialize_baseline(self) -> None:
        # Generate baseline transactions if not supplied to fit the ML baseline model
        if self.baseline_transactions is None:
            # Generate a default 1.5-hour baseline dataset prior to or starting at the timestamp
            self.baseline_transactions = list(generate_transactions(self.generator_config))
        self.baseline_model = fit_baseline(self.baseline_transactions, self.ml_config)

    def reset(self) -> Observation:
        """Reset the simulator to the initial state."""
        self.simulation_time = self.generator_config.start_timestamp
        self.active_actions = []
        self.action_history = []
        self.last_step_transactions = []
        self.prior_step_transactions = []
        self.baseline_iterator = generate_transactions(self.generator_config)
        self.incident_rngs = {
            inc.incident_id: _rng_for_incident(self.incident_seed, inc.incident_id)
            for inc in self.incidents_config
        }
        return self.observe([])

    def validate_action(self, action: dict[str, Any]) -> ActionResult:
        """Validate action schemas and parameters."""
        action_id = action.get("action_id", f"ACT_{uuid.uuid4().hex[:8].upper()}")
        action_type = action.get("action_type")

        if not action_type:
            return ActionResult(False, True, "Missing action_type", action_id)

        params = action.get("parameters", {})

        if action_type == "ROUTE_TRAFFIC":
            src = params.get("source_gateway")
            dst = params.get("destination_gateway")
            bank = params.get("affected_bank")
            method = params.get("affected_payment_method")
            pct = params.get("traffic_percentage")

            if src not in VALID_GATEWAYS:
                return ActionResult(False, True, f"Unknown source_gateway: {src}", action_id)
            if dst not in VALID_GATEWAYS:
                return ActionResult(False, True, f"Unknown destination_gateway: {dst}", action_id)
            if src == dst:
                return ActionResult(False, True, "Source and destination gateways cannot be the same", action_id)
            if bank is not None and bank not in VALID_BANKS:
                return ActionResult(False, True, f"Unknown affected_bank: {bank}", action_id)
            if method is not None and method not in VALID_METHODS:
                return ActionResult(False, True, f"Unknown affected_payment_method: {method}", action_id)
            if pct is None or not (0.0 <= float(pct) <= 100.0):
                return ActionResult(False, True, "traffic_percentage must be between 0 and 100", action_id)

        elif action_type == "REDUCE_GATEWAY_TRAFFIC":
            gw = params.get("gateway")
            pct = params.get("traffic_percentage")

            if gw not in VALID_GATEWAYS:
                return ActionResult(False, True, f"Unknown gateway: {gw}", action_id)
            if pct is None or not (0.0 <= float(pct) <= 100.0):
                return ActionResult(False, True, "traffic_percentage must be between 0 and 100", action_id)

        elif action_type == "DISABLE_PAYMENT_METHOD":
            method = params.get("payment_method")
            dur = params.get("duration_minutes")

            if method not in VALID_METHODS:
                return ActionResult(False, True, f"Unknown payment_method: {method}", action_id)
            if dur is None or int(dur) <= 0:
                return ActionResult(False, True, "duration_minutes must be a positive integer", action_id)

        elif action_type == "RATE_LIMIT_MERCHANT":
            merch = params.get("merchant")
            pct = params.get("traffic_percentage")

            if merch not in VALID_MERCHANTS:
                return ActionResult(False, True, f"Unknown merchant: {merch}", action_id)
            if pct is None or not (0.0 <= float(pct) <= 100.0):
                return ActionResult(False, True, "traffic_percentage must be between 0 and 100", action_id)

        else:
            return ActionResult(False, True, f"Unknown action_type: {action_type}", action_id)

        return ActionResult(True, False, "Action accepted", action_id)

    def step(self, action: dict[str, Any] | None = None) -> tuple[Observation, dict[str, Any]]:
        """Advance the simulation state by 5 minutes, applying actions and incidents."""
        action_result = None
        before_state = self._capture_numerical_state([])

        # Handle incoming action
        if action:
            val = self.validate_action(action)
            action_result = {
                "action_id": val.action_id,
                "simulation_time": self.simulation_time.isoformat(),
                "action_type": action["action_type"],
                "parameters": action["parameters"],
                "status": "ACCEPTED" if val.accepted else "REJECTED",
                "reason": val.reason,
                "before_state": before_state,
                "after_state": None,
            }
            if val.accepted:
                # Add action_id to the action dict and store it
                action_to_store = {**action, "action_id": val.action_id, "start_time": self.simulation_time}
                self.active_actions.append(action_to_store)
            self.action_history.append(action_result)

        step_duration = timedelta(minutes=5)
        step_end_time = self.simulation_time + step_duration

        # Determine transaction frequency and counts
        freq = self.generator_config.transaction_frequency_seconds
        tx_count = int(step_duration.total_seconds() / freq)

        # Pull baseline transactions for the step window
        step_baseline: list[TransactionRecord] = []
        for _ in range(tx_count):
            try:
                step_baseline.append(next(self.baseline_iterator))
            except StopIteration:
                break

        # If we ran out of transactions, re-generate dynamically
        if len(step_baseline) < tx_count:
            # Fast forward generator
            temp_config = GeneratorConfig(
                transaction_count=tx_count,
                random_seed=self.generator_config.random_seed + 1000,
                start_timestamp=self.simulation_time,
                transaction_frequency_seconds=freq
            )
            step_baseline = list(generate_transactions(temp_config))[:tx_count]

        # Apply active actions and active incidents to step transactions
        step_simulated = self._simulate_transactions(step_baseline)

        # Update current simulation time
        self.simulation_time = step_end_time

        # Capture state after processing step
        after_state = self._capture_numerical_state(step_simulated)
        if action_result and action_result["status"] == "ACCEPTED":
            action_result["after_state"] = after_state

        # Calculate outcomes
        outcome = self._calculate_outcome(before_state, after_state)

        # Get before revenue at risk
        before_obs = self.observe(self.last_step_transactions)
        before_revenue_at_risk = before_obs.revenue_at_risk

        # Observe the state
        self.prior_step_transactions = self.last_step_transactions
        self.last_step_transactions = step_simulated
        obs = self.observe(step_simulated)

        # Update outcome with actual revenue changes
        rev_reduction = max(Decimal("0.00"), before_revenue_at_risk - obs.revenue_at_risk)
        outcome["revenue_at_risk_reduction"] = str(rev_reduction)
        outcome["estimated_revenue_recovered"] = str(rev_reduction)

        return obs, outcome

    def observe(self, step_transactions: list[TransactionRecord] | None = None) -> Observation:
        """Observe the current payment reliability metrics."""
        if step_transactions is None:
            step_transactions = self.last_step_transactions
            prior_transactions = self.prior_step_transactions
        else:
            if step_transactions is self.last_step_transactions:
                prior_transactions = self.prior_step_transactions
            else:
                prior_transactions = self.last_step_transactions

        combined_records = list(self.baseline_transactions or []) + prior_transactions + list(step_transactions)
        scores, detected = detect_payment_incidents(step_transactions, self.baseline_model, self.ml_config)
        evidence_list = [generate_investigation_evidence(combined_records, inc) for inc in detected]
        revenue_impacts = calculate_revenue_for_incidents(combined_records, detected)

        global_score = max([s.score for s in scores if s.feature.segment_level == "GLOBAL"], default=0.0)
        total_revenue_at_risk = sum((ri.revenue_at_risk for ri in revenue_impacts), Decimal("0"))

        active_incidents_info = []
        for inc in self.incidents_config:
            if inc.start_time <= self.simulation_time < inc.recovery_end_time:
                active_incidents_info.append({
                    "incident_id": inc.incident_id,
                    "incident_type": inc.incident_type.value,
                    "severity": inc.severity.value,
                    "affected_gateway": inc.affected_gateway,
                    "affected_bank": inc.affected_bank,
                    "affected_payment_method": inc.affected_payment_method,
                    "affected_merchant": inc.affected_merchant,
                    "affected_location": inc.affected_location,
                    "start_time": inc.start_time.isoformat(),
                })

        top_affected = []
        for ri in revenue_impacts:
            if ri.top_affected_segment:
                top_affected.append({
                    "incident_id": ri.incident_id,
                    "segment": ri.top_affected_segment,
                    "revenue_at_risk": str(ri.revenue_at_risk),
                })

        recent_actions = self.action_history[-10:]
        action_outcomes = []
        for action in recent_actions:
            if action["status"] == "ACCEPTED":
                # Outcome is calculated dynamically
                action_outcomes.append({
                    "action_id": action["action_id"],
                    "action_type": action["action_type"],
                })

        # Evidence serialized representation
        serialized_evidence = []
        for ev in evidence_list:
            serialized_evidence.append({
                "incident_id": ev.incident_id,
                "top_banks": [asdict(item) for item in ev.top_banks],
                "top_payment_methods": [asdict(item) for item in ev.top_payment_methods],
                "top_gateways": [asdict(item) for item in ev.top_gateways],
                "top_error_codes": [asdict(item) for item in ev.top_error_codes],
                "likely_pattern": ev.likely_pattern,
                "baseline_status": ev.baseline_status,
                "evidence_quality": ev.evidence_quality,
            })

        vol = len(step_transactions)
        successes = sum(t.status == "SUCCESS" for t in step_transactions)
        failures = vol - successes
        success_rate = successes / vol if vol else 1.0
        failure_rate = failures / vol if vol else 0.0
        avg_latency = sum(t.latency_ms for t in step_transactions) / vol if vol else 0.0

        return Observation(
            current_time=self.simulation_time.isoformat(),
            transaction_volume=vol,
            success_rate=success_rate,
            failure_rate=failure_rate,
            latency=avg_latency,
            active_incidents=active_incidents_info,
            top_affected_segments=top_affected,
            anomaly_score=global_score,
            investigation_evidence=serialized_evidence,
            revenue_at_risk=total_revenue_at_risk,
            recent_actions=recent_actions,
            action_outcomes=action_outcomes,
        )

    def rollback_action(self, action_id: str) -> dict[str, Any]:
        """Rollback/deactivate a previous active action."""
        found = False
        for action in self.active_actions:
            if action.get("action_id") == action_id:
                self.active_actions.remove(action)
                found = True
                break

        # Record the rollback in history
        rollback_record = {
            "action_id": f"RLB_{uuid.uuid4().hex[:8].upper()}",
            "simulation_time": self.simulation_time.isoformat(),
            "action_type": "ROLLBACK",
            "parameters": {"target_action_id": action_id},
            "status": "COMPLETED" if found else "FAILED",
            "reason": "Restored baseline routing state" if found else f"Action ID {action_id} not found or inactive",
            "before_state": self._capture_numerical_state([]),
            "after_state": None,
        }
        self.action_history.append(rollback_record)
        return rollback_record

    def _simulate_transactions(self, baseline: list[TransactionRecord]) -> list[TransactionRecord]:
        simulated = []
        rng = random.Random(self.incident_seed + int(self.simulation_time.timestamp()))

        # Filter out expired temporary actions
        self.active_actions = [
            act for act in self.active_actions
            if act["action_type"] != "DISABLE_PAYMENT_METHOD"
            or self.simulation_time < act["start_time"] + timedelta(minutes=int(act["parameters"]["duration_minutes"]))
        ]

        for record in baseline:
            modified_record = record

            # 1. Apply active routing/traffic actions
            for action in self.active_actions:
                act_type = action["action_type"]
                params = action["parameters"]

                if act_type == "ROUTE_TRAFFIC":
                    src = params["source_gateway"]
                    dst = params["destination_gateway"]
                    bank = params.get("affected_bank")
                    method = params.get("affected_payment_method")
                    pct = float(params["traffic_percentage"])

                    if modified_record.gateway == src:
                        if (bank is None or modified_record.bank == bank) and \
                           (method is None or modified_record.payment_method == method):
                            if rng.random() * 100.0 < pct:
                                modified_record = replace(modified_record, gateway=dst)

                elif act_type == "REDUCE_GATEWAY_TRAFFIC":
                    gw = params["gateway"]
                    pct = float(params["traffic_percentage"])

                    if modified_record.gateway == gw:
                        if rng.random() * 100.0 < pct:
                            # Route to any other gateway randomly
                            others = [g for g in VALID_GATEWAYS if g != gw]
                            if others:
                                modified_record = replace(modified_record, gateway=rng.choice(others))

            # 2. Apply active disablement / rate-limiting actions
            for action in self.active_actions:
                act_type = action["action_type"]
                params = action["parameters"]

                if act_type == "DISABLE_PAYMENT_METHOD":
                    method = params["payment_method"]
                    if modified_record.payment_method == method:
                        modified_record = replace(
                            modified_record,
                            status="FAILED",
                            error_code="BANK_DECLINED",
                        )

                elif act_type == "RATE_LIMIT_MERCHANT":
                    merch = params["merchant"]
                    pct = float(params["traffic_percentage"])
                    if modified_record.merchant_id == merch:
                        if rng.random() * 100.0 < pct:
                            modified_record = replace(
                                modified_record,
                                status="FAILED",
                                error_code="BANK_DECLINED",
                            )

            # 3. Apply active incidents
            for inc in self.incidents_config:
                incident_rng = self.incident_rngs.get(inc.incident_id)
                if incident_rng:
                    modified_record = _apply_record(modified_record, inc, incident_rng)

            simulated.append(modified_record)

        return simulated

    def _capture_numerical_state(self, txs: list[TransactionRecord]) -> dict[str, Any]:
        vol = len(txs)
        successes = sum(t.status == "SUCCESS" for t in txs)
        failures = vol - successes
        success_rate = successes / vol if vol else 1.0
        avg_latency = sum(t.latency_ms for t in txs) / vol if vol else 0.0
        return {
            "transaction_count": vol,
            "success_rate": success_rate,
            "failure_rate": 1.0 - success_rate,
            "average_latency": avg_latency,
        }

    def _calculate_outcome(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        # Out-of-the-box basic outcome delta calculation for step verification
        success_improvement = after["success_rate"] - before["success_rate"]
        latency_improvement = before["average_latency"] - after["average_latency"]
        failure_reduction = before["failure_rate"] - after["failure_rate"]

        # Simple outcome mapping for AI evaluation
        return {
            "success_rate_improvement": success_improvement,
            "latency_improvement_ms": latency_improvement,
            "failure_rate_reduction": failure_reduction,
            "revenue_at_risk_reduction": "0.00",
            "estimated_revenue_recovered": "0.00",
        }

    def _get_step_baseline_transactions(self, start_time: datetime, count: int) -> list[TransactionRecord]:
        gen = generate_transactions(self.generator_config)
        elapsed_seconds = (start_time - self.generator_config.start_timestamp).total_seconds()
        elapsed_count = int(elapsed_seconds / self.generator_config.transaction_frequency_seconds)
        for _ in range(elapsed_count):
            try:
                next(gen)
            except StopIteration:
                break
        records = []
        for _ in range(count):
            try:
                records.append(next(gen))
            except StopIteration:
                break
        if len(records) < count:
            temp_config = GeneratorConfig(
                transaction_count=count,
                random_seed=self.generator_config.random_seed + 1000,
                start_timestamp=start_time,
                transaction_frequency_seconds=self.generator_config.transaction_frequency_seconds
            )
            records = list(generate_transactions(temp_config))[:count]
        return records

    def simulate_step_impact(self, action: dict) -> dict[str, Any]:
        """Perform a genuine isolated dry-run simulation of the next step with the candidate action."""
        val = self.validate_action(action)
        if not val.accepted:
            return {
                "is_valid": False,
                "reason": val.reason,
                "projected_success_rate": 0.0,
                "projected_revenue_at_risk": Decimal("0.00"),
                "estimated_recovery": Decimal("0.00"),
            }

        step_duration = timedelta(minutes=5)
        freq = self.generator_config.transaction_frequency_seconds
        tx_count = int(step_duration.total_seconds() / freq)

        temp_baseline = self._get_step_baseline_transactions(self.simulation_time, tx_count)

        saved_rng_states = {}
        for inc_id, rng in self.incident_rngs.items():
            saved_rng_states[inc_id] = rng.getstate()

        temp_active = [copy.deepcopy(act) for act in self.active_actions]
        temp_active.append({**action, "action_id": val.action_id, "start_time": self.simulation_time})

        original_active = self.active_actions
        original_rngs = self.incident_rngs

        self.active_actions = temp_active
        self.incident_rngs = {}
        for inc_id, rng in original_rngs.items():
            copied_rng = random.Random()
            copied_rng.setstate(saved_rng_states[inc_id])
            self.incident_rngs[inc_id] = copied_rng

        try:
            simulated = self._simulate_transactions(temp_baseline)
        finally:
            self.active_actions = original_active
            self.incident_rngs = original_rngs

        scores, detected = detect_payment_incidents(simulated, self.baseline_model, self.ml_config)
        combined_sim = list(self.baseline_transactions or []) + list(self.last_step_transactions) + list(simulated)
        revenue_impacts = calculate_revenue_for_incidents(combined_sim, detected)

        vol = len(simulated)
        successes = sum(t.status == "SUCCESS" for t in simulated)
        failures = vol - successes
        success_rate = successes / vol if vol else 1.0
        failure_rate = failures / vol if vol else 0.0
        avg_latency = sum(t.latency_ms for t in simulated) / vol if vol else 0.0

        total_revenue_at_risk = sum((ri.revenue_at_risk for ri in revenue_impacts), Decimal("0"))

        return {
            "is_valid": True,
            "reason": "Simulation successful",
            "projected_success_rate": success_rate,
            "projected_failure_rate": failure_rate,
            "projected_latency": avg_latency,
            "projected_revenue_at_risk": total_revenue_at_risk,
            "action_id": val.action_id,
        }

    def evaluate_counterfactual(self, action: dict[str, Any], horizon_steps: int = 1, runs: int = 20) -> Any:
        """Evaluate the recovery action using paired counterfactual branches."""
        from simulator.counterfactual import CounterfactualEvaluator
        evaluator = CounterfactualEvaluator(self)
        return evaluator.evaluate_counterfactual(action, horizon_steps, runs)

