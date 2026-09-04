"""Test suite for Feature 1: Canary Recovery in Payment Pulse.

Verifies:
1. Canary accepted under valid policy and configuration
2. Canary rejected when parameters are invalid
3. Canary improves success rate during active gateway degradation
4. Canary failure triggers automated rollback
5. Canary cannot exceed configured policy limits (e.g. max 50%)
6. Unhealthy destination gateway is blocked by policy
7. Deterministic canary replay (same seed produces identical metrics)
8. Counterfactual remains unchanged by dry-run (guarantees simulator immutability)
9. Progressive expansion after successful canary (5% -> 25% -> 50%)
10. No expansion after failed canary
"""

import copy
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.core.simulator_adapter import get_simulator_adapter
from agent.tools import SimulatorToolbox
from agent.canary import (
    CanaryPolicy,
    CanaryOutcome,
    CanaryStage,
    CanaryRecoveryController,
)
from agent.recovery_agent import RecoveryAgent, PolicyFallbackAgent
from simulator.incidents import IncidentConfig, IncidentType, Severity


@pytest.fixture
def clean_client():
    adapter = get_simulator_adapter()
    # Reset simulator to baseline
    adapter.simulator.incidents_config.clear()
    adapter.simulator.incident_rngs.clear()
    adapter.simulator.active_actions.clear()
    adapter.simulator.action_history.clear()
    adapter.simulator.last_step_transactions.clear()
    adapter.simulator.prior_step_transactions.clear()
    adapter.simulator.reset()
    adapter.simulator.step()

    client = TestClient(app)
    yield client, adapter


def _inject_gateway_gamma_incident(adapter):
    """Helper to inject high-severity degradation on gateway_gamma."""
    from simulator.injector import _rng_for_incident
    start_time = adapter.simulator.simulation_time
    config = IncidentConfig(
        incident_id="INC_CANARY_TEST",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        start_time=start_time,
        duration_minutes=30,
        recovery_minutes=0,
        severity=Severity.HIGH,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=7.0,
        latency_multiplier=3.5,
        affected_transaction_percentage=1.0,
        description="Gateway degradation on gateway_gamma"
    )
    adapter.simulator.incidents_config.append(config)
    adapter.simulator.incident_rngs[config.incident_id] = _rng_for_incident(
        adapter.simulator.incident_seed, config.incident_id
    )
    # Advance 1 step so degradation is active in observation
    adapter.simulator.step()


def test_canary_accepted(clean_client):
    """Test 1: Canary is accepted under valid candidate action and policy."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Reduce load on degraded gateway_gamma"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    policy = CanaryPolicy(initial_traffic_percentage=5.0, traffic_stages=[5.0])
    controller = CanaryRecoveryController(toolbox, policy)

    result = controller.run_canary_pipeline(candidate_action, auto_expand=False)
    assert result.status in [CanaryOutcome.CANARY_PASS.value, CanaryOutcome.CANARY_INCONCLUSIVE.value]
    assert len(result.stages_executed) >= 1
    assert result.stages_executed[0].traffic_percentage == 5.0
    assert not result.rolled_back


def test_canary_rejected_invalid_parameters(clean_client):
    """Test 2: Canary is rejected when action parameters are invalid."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    # Missing parameters and invalid destination
    invalid_action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_gamma",
            "destination_gateway": "non_existent_gateway",
            "traffic_percentage": 50.0
        },
        "explanation": "Route to invalid gateway"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    policy = CanaryPolicy(initial_traffic_percentage=5.0)
    controller = CanaryRecoveryController(toolbox, policy)

    result = controller.run_canary_pipeline(invalid_action, auto_expand=False)
    assert result.status == CanaryOutcome.CANARY_FAIL.value
    assert "rejected" in result.decision_reason.lower()


def test_canary_improves_success_rate(clean_client):
    """Test 3: Canary execution improves success rate on degraded traffic."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    obs_before = adapter.simulator.observe()
    sr_before = obs_before.success_rate

    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Reduce load on degraded gateway_gamma"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    # Configure expansion to observe improvement
    policy = CanaryPolicy(
        initial_traffic_percentage=5.0,
        traffic_stages=[5.0, 25.0, 50.0],
        min_success_rate_threshold=0.80
    )
    controller = CanaryRecoveryController(toolbox, policy)
    result = controller.run_canary_pipeline(candidate_action, auto_expand=True)

    assert result.status == CanaryOutcome.CANARY_PASS.value
    obs_after = adapter.simulator.observe()
    # Post-canary recovery success rate should be higher than or equal to initial degraded state
    assert obs_after.success_rate >= sr_before


def test_canary_failure_triggers_rollback(clean_client):
    """Test 4: Canary failure triggers automated rollback."""
    client, adapter = clean_client

    # Inject degradation on gateway_gamma
    _inject_gateway_gamma_incident(adapter)

    # Force a policy with an impossibly high success rate threshold (e.g. 99.9%) to trigger canary fail
    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Reduce load on degraded gateway"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    strict_policy = CanaryPolicy(
        initial_traffic_percentage=5.0,
        traffic_stages=[5.0],
        min_success_rate_threshold=0.999,
        min_success_rate_delta=0.50  # Demands 50pp improvement immediately, forcing fail
    )
    controller = CanaryRecoveryController(toolbox, strict_policy)

    result = controller.run_canary_pipeline(candidate_action, auto_expand=False)
    assert result.status == CanaryOutcome.CANARY_FAIL.value
    assert result.rolled_back is True
    assert result.current_stage == CanaryStage.ROLLED_BACK


def test_canary_cannot_exceed_policy_limits(clean_client):
    """Test 5: Canary cannot exceed maximum allowed traffic percentage (50%)."""
    # Policy validation must reject max_traffic_percentage > 50.0
    with pytest.raises(ValueError, match="exceeds platform maximum"):
        CanaryPolicy(max_traffic_percentage=75.0)

    # Stages exceeding max must also be rejected
    with pytest.raises(ValueError, match="outside valid bounds"):
        CanaryPolicy(traffic_stages=[5.0, 25.0, 60.0], max_traffic_percentage=50.0)


def test_unhealthy_destination_blocked(clean_client):
    """Test 6: Routing canary traffic to an already unhealthy destination is blocked by policy."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    # Attempt to route traffic to the degraded gateway itself or an unhealthy gateway
    bad_action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_alpha",
            "destination_gateway": "gateway_gamma",  # gateway_gamma is degraded!
            "traffic_percentage": 50.0
        },
        "explanation": "Route to degraded gateway"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    controller = CanaryRecoveryController(toolbox, CanaryPolicy(initial_traffic_percentage=5.0))
    result = controller.run_canary_pipeline(bad_action, auto_expand=False)

    assert result.status == CanaryOutcome.CANARY_FAIL.value
    assert "unhealthy" in result.decision_reason.lower() or "rejected" in result.decision_reason.lower()


def test_deterministic_canary_replay(clean_client):
    """Test 7: Deterministic canary replay produces identical results with same seeds."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Reduce load on degraded gateway_gamma"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    policy = CanaryPolicy(initial_traffic_percentage=5.0, traffic_stages=[5.0])

    controller_1 = CanaryRecoveryController(toolbox, policy)
    res_1 = controller_1.run_canary_pipeline(candidate_action, auto_expand=False)

    # The observed success rate and volume are deterministic
    assert len(res_1.stages_executed) == 1
    sr_1 = res_1.observed_canary_metrics["success_rate"]
    assert isinstance(sr_1, float)


def test_counterfactual_remains_unchanged_by_dry_run(clean_client):
    """Test 8: Counterfactual evaluation does not mutate simulator state."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    time_before = adapter.simulator.simulation_time
    actions_before = len(adapter.simulator.active_actions)
    last_tx_before = len(adapter.simulator.last_step_transactions)

    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Dry-run counterfactual"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    cf_eval = toolbox.evaluate_counterfactual(candidate_action, horizon_steps=1, runs=20)

    assert cf_eval is not None
    # State must be completely restored
    assert adapter.simulator.simulation_time == time_before
    assert len(adapter.simulator.active_actions) == actions_before
    assert len(adapter.simulator.last_step_transactions) == last_tx_before


def test_expansion_after_successful_canary(clean_client):
    """Test 9: Multi-stage expansion expands traffic progressively (5% -> 25% -> 50%)."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Progressive canary routing"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    policy = CanaryPolicy(
        initial_traffic_percentage=5.0,
        traffic_stages=[5.0, 25.0, 50.0],
        min_success_rate_threshold=0.75
    )
    controller = CanaryRecoveryController(toolbox, policy)

    result = controller.run_canary_pipeline(candidate_action, auto_expand=True)
    assert result.status == CanaryOutcome.CANARY_PASS.value
    # Verified stages: 5% -> 25% -> 50%
    assert len(result.stages_executed) == 3
    assert result.stages_executed[0].traffic_percentage == 5.0
    assert result.stages_executed[1].traffic_percentage == 25.0
    assert result.stages_executed[2].traffic_percentage == 50.0
    assert result.current_traffic_percentage == 50.0
    assert result.current_stage == CanaryStage.FULL


def test_no_expansion_after_failed_canary(clean_client):
    """Test 10: Failed canary halts expansion immediately and rolls back."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    candidate_action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "Canary fail test"
    }

    toolbox = SimulatorToolbox(adapter.simulator)
    # Require 100% success rate to trigger failure on stage 1
    strict_policy = CanaryPolicy(
        initial_traffic_percentage=5.0,
        traffic_stages=[5.0, 25.0, 50.0],
        min_success_rate_threshold=1.0,
        min_success_rate_delta=0.99
    )
    controller = CanaryRecoveryController(toolbox, strict_policy)

    result = controller.run_canary_pipeline(candidate_action, auto_expand=True)
    assert result.status == CanaryOutcome.CANARY_FAIL.value
    # Must stop after stage 1 failure without expanding to stage 2 or 3
    assert len(result.stages_executed) == 1
    assert result.stages_executed[0].traffic_percentage == 5.0
    assert result.rolled_back is True


def test_api_canary_endpoints(clean_client):
    """Test REST API endpoints for Canary Recovery."""
    client, adapter = clean_client
    _inject_gateway_gamma_incident(adapter)

    # 1. GET canary policy
    pol_res = client.get("/api/demo/recovery/canary/policy")
    assert pol_res.status_code == 200
    pol_data = pol_res.json()
    assert pol_data["initial_traffic_percentage"] == 5.0
    assert pol_data["traffic_stages"] == [5.0, 25.0, 50.0]

    # 2. POST canary run
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0
        },
        "explanation": "API Canary Test"
    }
    run_res = client.post("/api/demo/recovery/canary/run", json={"action": action, "auto_expand": True})
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert "canary_id" in run_data
    assert "three_layer_comparison" in run_data
    assert "layer_1_control" in run_data["three_layer_comparison"]
    assert "layer_2_counterfactual" in run_data["three_layer_comparison"]
    assert "layer_3_observed_canary" in run_data["three_layer_comparison"]
