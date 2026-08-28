"""Simulator tools interface providing the strict agent/tool boundary."""

from typing import Any, Mapping
from simulator.environment import StatefulSimulator
from agent.policy import check_policy


class SimulatorToolbox:
    """Operations interface for agent tool interaction with StatefulSimulator."""

    def __init__(self, simulator: StatefulSimulator) -> None:
        self._simulator = simulator

    def inspect_incident(self) -> dict[str, Any]:
        """Retrieve details of currently active incidents."""
        obs = self._simulator.observe()
        return {
            "active_incidents": obs.active_incidents,
            "top_affected_segments": obs.top_affected_segments,
        }

    def calculate_revenue_impact(self) -> dict[str, Any]:
        """Retrieve current success rate, latency, and estimated revenue-at-risk."""
        obs = self._simulator.observe()
        return {
            "success_rate": obs.success_rate,
            "failure_rate": obs.failure_rate,
            "latency": obs.latency,
            "revenue_at_risk": str(obs.revenue_at_risk),
            "transaction_volume": obs.transaction_volume,
        }

    def list_available_actions(self) -> list[dict[str, Any]]:
        """List candidate recovery actions with different parameters."""
        candidates = []
        # ROUTE_TRAFFIC options (25% and 50% to respect policy)
        gateways = ["gateway_alpha", "gateway_beta", "gateway_gamma"]
        for src in gateways:
            for dst in gateways:
                if src != dst:
                    for pct in [25.0, 50.0]:
                        candidates.append({
                            "action_type": "ROUTE_TRAFFIC",
                            "parameters": {
                                "source_gateway": src,
                                "destination_gateway": dst,
                                "traffic_percentage": pct,
                            },
                            "explanation": f"Route {pct}% traffic from {src} to {dst} to bypass degradation.",
                        })

        # REDUCE_GATEWAY_TRAFFIC options
        for gw in gateways:
            for pct in [25.0, 50.0]:
                candidates.append({
                    "action_type": "REDUCE_GATEWAY_TRAFFIC",
                    "parameters": {
                        "gateway": gw,
                        "traffic_percentage": pct,
                    },
                    "explanation": f"Reduce {pct}% traffic load on {gw}.",
                })

        # DISABLE_PAYMENT_METHOD options
        methods = ["UPI", "CARD", "NETBANKING", "WALLET"]
        for method in methods:
            candidates.append({
                "action_type": "DISABLE_PAYMENT_METHOD",
                "parameters": {
                    "payment_method": method,
                    "duration_minutes": 15,
                },
                "explanation": f"Disable degraded {method} payment method.",
            })

        # RATE_LIMIT_MERCHANT options
        merchants = ["merchant_retail_001", "merchant_marketplace_002", "merchant_subscription_003"]
        for merch in merchants:
            for pct in [25.0, 50.0]:
                candidates.append({
                    "action_type": "RATE_LIMIT_MERCHANT",
                    "parameters": {
                        "merchant": merch,
                        "traffic_percentage": pct,
                    },
                    "explanation": f"Apply {pct}% rate limit to {merch}.",
                })

        return candidates

    def simulate_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """Perform a genuine isolated simulation of the action on a cloned state."""
        if not isinstance(action, dict):
            return {
                "is_valid": False,
                "accepted": False,
                "reason": "Action must be a dictionary.",
                "action_id": None,
                "projected_success_rate": 0.0,
                "projected_revenue_at_risk": "0.00",
            }
        
        obs = self._simulator.observe()
        passed, reason = check_policy(action, obs)
        if not passed:
            return {
                "is_valid": False,
                "accepted": False,
                "reason": f"Policy rejection: {reason}",
                "action_id": None,
                "projected_success_rate": 0.0,
                "projected_revenue_at_risk": "0.00",
            }

        try:
            res = self._simulator.simulate_step_impact(action)
            return {
                "is_valid": res.get("is_valid", False),
                "accepted": res.get("is_valid", False),
                "reason": res.get("reason", "Simulation successful"),
                "action_id": res.get("action_id"),
                "projected_success_rate": res.get("projected_success_rate", 0.0),
                "projected_failure_rate": res.get("projected_failure_rate", 0.0),
                "projected_latency": res.get("projected_latency", 0.0),
                "projected_revenue_at_risk": str(res.get("projected_revenue_at_risk", "0.00")),
            }
        except Exception as e:
            return {
                "is_valid": False,
                "accepted": False,
                "reason": f"Simulation execution failure: {str(e)}",
                "action_id": None,
                "projected_success_rate": 0.0,
                "projected_revenue_at_risk": "0.00",
            }

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute action, advance the real simulator, and return observation."""
        if not isinstance(action, dict):
            return {
                "status": "REJECTED",
                "accepted": False,
                "reason": "Action must be a dictionary.",
                "action_id": None,
                "observation": self.observe_result(),
            }

        obs_before = self._simulator.observe()
        passed, reason = check_policy(action, obs_before)
        if not passed:
            return {
                "status": "REJECTED",
                "accepted": False,
                "reason": f"Policy rejection: {reason}",
                "action_id": None,
                "observation": self.observe_result(),
            }

        try:
            obs_after, outcome = self._simulator.step(action)
            action_id = outcome.get("action_id") if outcome else None
            return {
                "status": "ACCEPTED",
                "accepted": True,
                "reason": "Executed successfully",
                "action_id": action_id,
                "observation": {
                    "current_time": obs_after.current_time,
                    "transaction_volume": obs_after.transaction_volume,
                    "success_rate": obs_after.success_rate,
                    "failure_rate": obs_after.failure_rate,
                    "latency": obs_after.latency,
                    "revenue_at_risk": str(obs_after.revenue_at_risk),
                    "active_incidents": obs_after.active_incidents,
                    "top_affected_segments": obs_after.top_affected_segments,
                    "investigation_evidence": obs_after.investigation_evidence,
                },
                "outcome": outcome,
            }
        except Exception as e:
            return {
                "status": "REJECTED",
                "accepted": False,
                "reason": f"Execution failed: {str(e)}",
                "action_id": None,
                "observation": self.observe_result(),
            }

    def observe_result(self) -> dict[str, Any]:
        """Retrieve current observation metrics from the environment."""
        obs = self._simulator.observe()
        return {
            "current_time": obs.current_time,
            "transaction_volume": obs.transaction_volume,
            "success_rate": obs.success_rate,
            "failure_rate": obs.failure_rate,
            "latency": obs.latency,
            "revenue_at_risk": str(obs.revenue_at_risk),
            "active_incidents": obs.active_incidents,
            "top_affected_segments": obs.top_affected_segments,
            "investigation_evidence": obs.investigation_evidence,
            "anomaly_score": obs.anomaly_score,
        }

    def rollback_action(self, action_id: str) -> dict[str, Any]:
        """Rollback an active action, advance environment by 1 step, and observe."""
        if not action_id or not isinstance(action_id, str):
            return {
                "status": "REJECTED",
                "accepted": False,
                "reason": "action_id must be a valid non-empty string",
                "action_id": None,
                "observation": self.observe_result(),
            }
        try:
            self._simulator.rollback_action(action_id)
            obs_after, outcome = self._simulator.step()
            return {
                "status": "COMPLETED",
                "accepted": True,
                "observation": {
                    "current_time": obs_after.current_time,
                    "transaction_volume": obs_after.transaction_volume,
                    "success_rate": obs_after.success_rate,
                    "failure_rate": obs_after.failure_rate,
                    "latency": obs_after.latency,
                    "revenue_at_risk": str(obs_after.revenue_at_risk),
                    "active_incidents": obs_after.active_incidents,
                    "top_affected_segments": obs_after.top_affected_segments,
                    "investigation_evidence": obs_after.investigation_evidence,
                    "anomaly_score": obs_after.anomaly_score,
                },
                "outcome": outcome,
            }
        except Exception as e:
            return {
                "status": "FAILED",
                "accepted": False,
                "reason": f"Rollback error: {str(e)}",
                "observation": self.observe_result(),
            }

    def evaluate_counterfactual(self, action: dict[str, Any], horizon_steps: int = 1, runs: int = 20) -> Any:
        """Run a paired counterfactual experiment for the given action."""
        return self._simulator.evaluate_counterfactual(action, horizon_steps, runs)

