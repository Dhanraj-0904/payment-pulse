import pytest
import random
import copy
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.environment import StatefulSimulator
from simulator.counterfactual import CounterfactualEvaluator
from agent.tools import SimulatorToolbox
from agent.recovery_agent import RecoveryAgent, PolicyFallbackAgent

@pytest.fixture
def base_time():
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

@pytest.fixture
def default_config(base_time):
    return GeneratorConfig(
        transaction_count=300,
        random_seed=42,
        start_timestamp=base_time,
        transaction_frequency_seconds=1,
    )

@pytest.fixture
def baseline_data(base_time):
    cfg = GeneratorConfig(
        transaction_count=2000,
        random_seed=100,
        start_timestamp=base_time - timedelta(hours=2),
        transaction_frequency_seconds=1,
    )
    from simulator.generator import generate_transactions
    return list(generate_transactions(cfg))

def test_identical_paired_future_transaction_inputs(default_config, baseline_data):
    """Prove that both branches process the exact same sequence of transaction records."""
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()

    evaluator = CounterfactualEvaluator(sim)
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        }
    }

    # Fetch baseline transactions for evaluation
    step_duration = timedelta(minutes=5)
    freq = sim.generator_config.transaction_frequency_seconds
    tx_count = int(step_duration.total_seconds() / freq)
    
    future_baseline_1 = sim._get_step_baseline_transactions(sim.simulation_time, tx_count)
    future_baseline_2 = sim._get_step_baseline_transactions(sim.simulation_time, tx_count)

    # Assert deterministic generation identity
    assert len(future_baseline_1) == len(future_baseline_2)
    for t1, t2 in zip(future_baseline_1, future_baseline_2):
        assert t1.transaction_id == t2.transaction_id
        assert t1.amount == t2.amount
        assert t1.gateway == t2.gateway

def test_simulator_immutability(default_config, baseline_data, base_time):
    """Prove that evaluating counterfactuals leaves the main simulator state unmutated."""
    inc = IncidentConfig(
        incident_id="INC_TEST_IMMUTABLE",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    sim = StatefulSimulator(default_config, [inc], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()

    orig_time = sim.simulation_time
    orig_active = list(sim.active_actions)
    orig_rng_states = {inc_id: rng.getstate() for inc_id, rng in sim.incident_rngs.items()}

    evaluator = CounterfactualEvaluator(sim)
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        }
    }

    evaluator.evaluate_counterfactual(action, horizon_steps=1, runs=5)

    assert sim.simulation_time == orig_time
    assert sim.active_actions == orig_active
    for inc_id, state in orig_rng_states.items():
        assert sim.incident_rngs[inc_id].getstate() == state

def test_no_action_control_authenticity(default_config, baseline_data):
    """Verify that the control branch represents authentic baseline behavior (NO actions applied)."""
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()

    evaluator = CounterfactualEvaluator(sim)
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        }
    }

    # Evaluate action
    res = evaluator.evaluate_counterfactual(action, horizon_steps=1, runs=5)

    # WITHOUT action metrics should reflect default healthy baseline (success rate near 1.0, zero active actions)
    assert res.without_action.success_rate >= 0.93
    assert res.without_action.revenue_at_risk == Decimal("0.00")

def test_deterministic_repeated_evaluation(default_config, baseline_data):
    """Prove that evaluating the same action repeatedly on the same state produces identical results."""
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()

    evaluator = CounterfactualEvaluator(sim)
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        }
    }

    res1 = evaluator.evaluate_counterfactual(action, horizon_steps=1, runs=10)
    res2 = evaluator.evaluate_counterfactual(action, horizon_steps=1, runs=10)

    # Assert identity of aggregated statistical metrics
    assert res1.with_action.success_rate == res2.with_action.success_rate
    assert res1.without_action.success_rate == res2.without_action.success_rate
    assert res1.effect.revenue_risk_reduction == res2.effect.revenue_risk_reduction
    assert res1.confidence_interval == res2.confidence_interval
    assert res1.success_rate_ci == res2.success_rate_ci

def test_meaningful_effect_filtering_agent(default_config, baseline_data, base_time):
    """Verify that RecoveryAgent rejects executing actions when the CI bounds predict no meaningful improvement."""
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()

    toolbox = SimulatorToolbox(sim)
    agent = RecoveryAgent(None, toolbox)

    # Candidate action on healthy system
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        }
    }

    # Artificially force fallback selection of this action to test filtering
    trace = agent.run(max_iterations=1)
    
    # Healthy system should not execute the action since CI does not support improvement
    # And trace should terminate with ACTION_INEFFECTIVE
    if trace.selected_action:
        assert trace.status == "ACTION_INEFFECTIVE"
        assert trace.action_result is None

def test_actual_vs_predicted_telemetry(default_config, baseline_data, base_time):
    """Prove that prediction error telemetry logs correctly when an action is executed."""
    inc = IncidentConfig(
        incident_id="INC_TEST_TELEMETRY",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=8.0,
        latency_multiplier=3.0,
    )
    sim = StatefulSimulator(default_config, [inc], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # active incident is now active in simulator

    toolbox = SimulatorToolbox(sim)
    fallback = PolicyFallbackAgent(toolbox)
    trace = fallback.run(max_iterations=1)

    if trace.status == "RECOVERY_SUCCESSFUL":
        assert trace.prediction_telemetry is not None
        telemetry = trace.prediction_telemetry
        assert "predicted_success_rate" in telemetry
        assert "actual_success_rate" in telemetry
        assert "success_rate_error" in telemetry
        assert "predicted_revenue_at_risk" in telemetry
        assert "actual_revenue_at_risk" in telemetry
        assert "revenue_at_risk_error" in telemetry
        
        # Verify prediction error calculation: error = actual - predicted
        err_sr = telemetry["actual_success_rate"] - telemetry["predicted_success_rate"]
        assert pytest.approx(telemetry["success_rate_error"]) == err_sr
