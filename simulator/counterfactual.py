import uuid
import copy
import random
import math
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from simulator.environment import StatefulSimulator
from simulator.schema import TransactionRecord
from simulator.injector import _rng_for_incident
from ml.anomaly import detect_payment_incidents
from ml.revenue import calculate_revenue_for_incidents
from agent.models import (
    CounterfactualEvaluation,
    BranchMetrics,
    CounterfactualEffect
)

# 95% Two-Tailed t-distribution critical values lookup
T_VALUES = {
    4: 2.776,   # runs = 5, df = 4
    9: 2.262,   # runs = 10, df = 9
    19: 2.093,  # runs = 20, df = 19
    29: 2.045,  # runs = 30, df = 29
    49: 2.010,  # runs = 50, df = 49
}

class CounterfactualEvaluator:
    """Evaluates the counterfactual recovery impact of candidate actions."""

    def __init__(self, simulator: StatefulSimulator):
        self.simulator = simulator

    def evaluate_counterfactual(
        self,
        action: dict[str, Any],
        horizon_steps: int = 1,
        runs: int = 20
    ) -> CounterfactualEvaluation:
        """Run paired counterfactual simulation (WITH vs WITHOUT action) under identical randomness."""
        evaluation_id = f"EVAL_{uuid.uuid4().hex[:8].upper()}"
        action_id = action.get("action_id", f"ACT_{uuid.uuid4().hex[:8].upper()}")

        # Save original simulator variables to guarantee immutability
        orig_time = self.simulator.simulation_time
        orig_active = [copy.deepcopy(act) for act in self.simulator.active_actions]
        orig_rng_states = {inc_id: rng.getstate() for inc_id, rng in self.simulator.incident_rngs.items() if rng is not None}
        orig_seed = self.simulator.incident_seed
        orig_last_step = list(self.simulator.last_step_transactions)
        orig_prior_step = list(self.simulator.prior_step_transactions)

        # Pre-fetch baseline transactions for the horizon once to ensure identity
        step_duration = timedelta(minutes=5)
        freq = self.simulator.generator_config.transaction_frequency_seconds
        tx_count = int(step_duration.total_seconds() / freq)

        future_baseline_transactions = []
        for step_idx in range(horizon_steps):
            step_start_time = orig_time + step_idx * step_duration
            step_baseline = self.simulator._get_step_baseline_transactions(step_start_time, tx_count)
            future_baseline_transactions.append(step_baseline)

        with_runs: list[BranchMetrics] = []
        without_runs: list[BranchMetrics] = []
        revenue_risk_differences: list[Decimal] = []
        success_rate_differences: list[float] = []

        # Run replications
        for r in range(runs):
            run_seed = orig_seed + 1000 + r

            # --- Branch A: WITH ACTION ---
            with_metrics = self._run_branch(
                action=action,
                horizon_steps=horizon_steps,
                run_seed=run_seed,
                future_baseline_transactions=future_baseline_transactions,
                orig_active_actions=orig_active
            )
            with_runs.append(with_metrics)

            # --- Branch B: WITHOUT ACTION (Control) ---
            without_metrics = self._run_branch(
                action=None,
                horizon_steps=horizon_steps,
                run_seed=run_seed,
                future_baseline_transactions=future_baseline_transactions,
                orig_active_actions=orig_active
            )
            without_runs.append(without_metrics)

            # Calculate paired difference for this run
            diff = without_metrics.revenue_at_risk - with_metrics.revenue_at_risk
            revenue_risk_differences.append(diff)
            
            sr_diff = with_metrics.success_rate - without_metrics.success_rate
            success_rate_differences.append(sr_diff)

        # Restore original simulator state fully
        self.simulator.simulation_time = orig_time
        self.simulator.active_actions = orig_active
        self.simulator.incident_seed = orig_seed
        self.simulator.last_step_transactions = orig_last_step
        self.simulator.prior_step_transactions = orig_prior_step
        self.simulator.incident_rngs = {}
        for inc_id, state in orig_rng_states.items():
            rng = random.Random()
            rng.setstate(state)
            self.simulator.incident_rngs[inc_id] = rng

        # Calculate aggregations
        mean_with = self._aggregate_metrics(with_runs)
        mean_without = self._aggregate_metrics(without_runs)

        # Statistical calculations for Revenue Risk Reduction
        mean_diff = sum(revenue_risk_differences, Decimal("0.00")) / Decimal(str(runs))
        if runs > 1:
            variance = sum(((d - mean_diff) ** 2 for d in revenue_risk_differences), Decimal("0.00")) / Decimal(str(runs - 1))
            std_dev = Decimal(str(math.sqrt(float(variance))))
        else:
            std_dev = Decimal("0.00")

        # Standard error and confidence interval
        std_error = std_dev / Decimal(str(math.sqrt(runs)))
        df = runs - 1
        t_crit = Decimal(str(T_VALUES.get(df, 1.96)))
        margin_of_error = t_crit * std_error

        ci_lower = mean_diff - margin_of_error
        ci_upper = mean_diff + margin_of_error

        # Statistical calculations for Success Rate Improvement
        mean_sr_diff = sum(success_rate_differences) / runs
        if runs > 1:
            variance_sr = sum(((d - mean_sr_diff) ** 2 for d in success_rate_differences)) / (runs - 1)
            std_dev_sr = math.sqrt(variance_sr)
        else:
            std_dev_sr = 0.0

        std_error_sr = std_dev_sr / math.sqrt(runs)
        margin_of_error_sr = float(t_crit) * std_error_sr
        ci_lower_sr = mean_sr_diff - margin_of_error_sr
        ci_upper_sr = mean_sr_diff + margin_of_error_sr

        effect = CounterfactualEffect(
            success_rate_improvement=mean_with.success_rate - mean_without.success_rate,
            failure_rate_reduction=mean_without.failure_rate - mean_with.failure_rate,
            revenue_risk_reduction=mean_diff,
            estimated_revenue_recovered=mean_diff
        )

        return CounterfactualEvaluation(
            evaluation_id=evaluation_id,
            action_id=action_id,
            horizon_steps=horizon_steps,
            runs=runs,
            with_action=mean_with,
            without_action=mean_without,
            effect=effect,
            confidence_interval=(ci_lower, ci_upper),
            success_rate_ci=(ci_lower_sr, ci_upper_sr),
            confidence_level=0.95
        )

    def _run_branch(
        self,
        action: Optional[dict[str, Any]],
        horizon_steps: int,
        run_seed: int,
        future_baseline_transactions: list[list[TransactionRecord]],
        orig_active_actions: list[dict[str, Any]]
    ) -> BranchMetrics:
        """Helper to run a simulation branch under temporary seed and actions configuration."""
        # 1. Setup branch parameters
        self.simulator.incident_seed = run_seed
        self.simulator.incident_rngs = {
            inc.incident_id: _rng_for_incident(run_seed, inc.incident_id)
            for inc in self.simulator.incidents_config
        }
        self.simulator.active_actions = [copy.deepcopy(act) for act in orig_active_actions]

        if action:
            val = self.simulator.validate_action(action)
            if val.accepted:
                self.simulator.active_actions.append({
                    **action,
                    "action_id": val.action_id,
                    "start_time": self.simulator.simulation_time
                })

        all_simulated = []

        # 2. Advance branch steps
        for step_idx in range(horizon_steps):
            step_time = self.simulator.simulation_time + step_idx * timedelta(minutes=5)
            # Override simulation_time so incident filtering and transaction rng calculation match the step
            self.simulator.simulation_time = step_time

            step_baseline = [copy.deepcopy(t) for t in future_baseline_transactions[step_idx]]
            step_simulated = self.simulator._simulate_transactions(step_baseline)
            all_simulated.extend(step_simulated)

            # Update simulator's rolling window references
            self.simulator.prior_step_transactions = self.simulator.last_step_transactions
            self.simulator.last_step_transactions = step_simulated

        # 3. Calculate branch metrics
        scores, detected = detect_payment_incidents(all_simulated, self.simulator.baseline_model, self.simulator.ml_config)
        combined_sim = list(self.simulator.baseline_transactions or []) + list(self.simulator.last_step_transactions) + list(all_simulated)
        revenue_impacts = calculate_revenue_for_incidents(combined_sim, detected)
        total_revenue_at_risk = sum((ri.revenue_at_risk for ri in revenue_impacts), Decimal("0"))

        vol = len(all_simulated)
        successes = sum(t.status == "SUCCESS" for t in all_simulated)
        failures = vol - successes
        success_rate = successes / vol if vol else 1.0
        failure_rate = failures / vol if vol else 0.0
        avg_latency = sum(t.latency_ms for t in all_simulated) / vol if vol else 0.0
        failed_amount = sum((t.amount for t in all_simulated if t.status == "FAILED"), Decimal("0"))

        return BranchMetrics(
            transaction_count=vol,
            success_rate=success_rate,
            failure_rate=failure_rate,
            average_latency=avg_latency,
            revenue_at_risk=total_revenue_at_risk,
            failed_amount=failed_amount
        )

    def _aggregate_metrics(self, runs_metrics: list[BranchMetrics]) -> BranchMetrics:
        """Aggregate metrics over all runs."""
        N = len(runs_metrics)
        avg_count = sum(m.transaction_count for m in runs_metrics) / N
        avg_sr = sum(m.success_rate for m in runs_metrics) / N
        avg_fr = sum(m.failure_rate for m in runs_metrics) / N
        avg_lat = sum(m.average_latency for m in runs_metrics) / N
        avg_rar = sum(m.revenue_at_risk for m in runs_metrics) / Decimal(str(N))
        avg_failed = sum(m.failed_amount for m in runs_metrics) / Decimal(str(N))

        return BranchMetrics(
            transaction_count=int(round(avg_count)),
            success_rate=avg_sr,
            failure_rate=avg_fr,
            average_latency=avg_lat,
            revenue_at_risk=Decimal(str(round(avg_rar, 2))),
            failed_amount=Decimal(str(round(avg_failed, 2)))
        )
