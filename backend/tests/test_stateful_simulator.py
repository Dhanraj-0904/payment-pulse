"""Unit and integration tests for the stateful payment recovery simulator."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.environment import StatefulSimulator
from simulator.schema import TransactionRecord


@pytest.fixture
def base_time():
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def default_config(base_time):
    return GeneratorConfig(
        transaction_count=300,  # 5 minutes of transactions at 1 transaction/second
        random_seed=42,
        start_timestamp=base_time,
        transaction_frequency_seconds=1,
    )


@pytest.fixture
def mock_gateway_incident(base_time):
    return IncidentConfig(
        incident_id="INC_GATEWAY_DEGRADATION",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time + timedelta(minutes=5),
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway="gateway_beta",
        affected_transaction_percentage=1.0,
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )


def test_environment_reset_and_initial_observation(default_config):
    sim = StatefulSimulator(default_config, [])
    obs = sim.reset()
    assert obs is not None
    assert obs.current_time == default_config.start_timestamp.isoformat()
    assert obs.transaction_volume == 0
    assert obs.success_rate == 1.0
    assert obs.anomaly_score == 0.0
    assert obs.revenue_at_risk == Decimal("0")
    assert len(obs.recent_actions) == 0


def test_simulation_step_advances_time(default_config):
    sim = StatefulSimulator(default_config, [])
    sim.reset()
    obs1, outcome1 = sim.step()
    assert obs1.current_time == (default_config.start_timestamp + timedelta(minutes=5)).isoformat()
    assert sim.simulation_time == default_config.start_timestamp + timedelta(minutes=5)

    obs2, outcome2 = sim.step()
    assert obs2.current_time == (default_config.start_timestamp + timedelta(minutes=10)).isoformat()


def test_action_validation_acceptance_and_rejection(default_config):
    sim = StatefulSimulator(default_config, [])
    sim.reset()

    # Valid route action
    res = sim.validate_action({
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 50.0,
        }
    })
    assert res.accepted
    assert not res.rejected

    # Invalid destination same as source
    res2 = sim.validate_action({
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_beta",
            "traffic_percentage": 50.0,
        }
    })
    assert res2.rejected
    assert "cannot be the same" in res2.reason

    # Invalid percentage
    res3 = sim.validate_action({
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 150.0,
        }
    })
    assert res3.rejected
    assert "traffic_percentage" in res3.reason

    # Malformed / unknown type
    res4 = sim.validate_action({
        "action_type": "SOMETHING_ELSE",
        "parameters": {}
    })
    assert res4.rejected
    assert "Unknown action_type" in res4.reason


def test_gateway_degradation_incident_and_traffic_rerouting_recovery(default_config, mock_gateway_incident):
    # Incident starts at minute 5 and ends at minute 25
    sim = StatefulSimulator(default_config, [mock_gateway_incident])
    sim.reset()

    # Step 1: 12:00 -> 12:05. Baseline normal behavior (no incident active yet)
    obs1, outcome1 = sim.step()
    assert obs1.success_rate > 0.92

    # Step 2: 12:05 -> 12:10. Incident is active, but NO recovery action taken.
    # Success rate should degrade.
    obs2, outcome2 = sim.step()
    assert obs2.success_rate < obs1.success_rate
    assert len(obs2.active_incidents) == 1
    assert obs2.active_incidents[0]["incident_id"] == "INC_GATEWAY_DEGRADATION"

    # Step 3: 12:10 -> 12:15. Incident remains active.
    # Apply ROUTE_TRAFFIC action to route 100% traffic from gateway_beta to gateway_alpha.
    action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 100.0,
        }
    }
    obs3, outcome3 = sim.step(action)
    
    # Success rate must improve because gateway_beta traffic is diverted to healthy gateway_alpha
    assert obs3.success_rate > obs2.success_rate
    assert obs3.success_rate >= 0.90
    assert len(sim.active_actions) == 1
    assert sim.action_history[-1]["status"] == "ACCEPTED"


def test_gateway_traffic_reduction_action(default_config):
    sim = StatefulSimulator(default_config, [])
    sim.reset()

    # Reduce gateway_beta traffic by 100% (redirects to other gateways)
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_beta",
            "traffic_percentage": 100.0,
        }
    }
    obs, outcome = sim.step(action)
    assert sim.action_history[-1]["status"] == "ACCEPTED"
    assert len(sim.active_actions) == 1


def test_payment_method_disablement_action(default_config):
    sim = StatefulSimulator(default_config, [])
    sim.reset()

    # Disable CARD method
    action = {
        "action_type": "DISABLE_PAYMENT_METHOD",
        "parameters": {
            "payment_method": "CARD",
            "duration_minutes": 10,
        }
    }
    obs, outcome = sim.step(action)
    # The step execution should fail all CARD transactions instantly,
    # so average success rate decreases.
    assert obs.success_rate < 0.90


def test_merchant_rate_limiting_action(default_config):
    sim = StatefulSimulator(default_config, [])
    sim.reset()

    # Rate limit merchant retail by 100%
    action = {
        "action_type": "RATE_LIMIT_MERCHANT",
        "parameters": {
            "merchant": "merchant_retail_001",
            "traffic_percentage": 100.0,
        }
    }
    obs, outcome = sim.step(action)
    assert sim.action_history[-1]["status"] == "ACCEPTED"


def test_rollback_action_restores_routing_state(default_config, mock_gateway_incident):
    sim = StatefulSimulator(default_config, [mock_gateway_incident])
    sim.reset()

    # Step 1: 12:00 -> 12:05
    sim.step()

    # Step 2: 12:05 -> 12:10 (degraded)
    obs2, _ = sim.step()
    
    # Step 3: 12:10 -> 12:15. Route away.
    action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 100.0,
        }
    }
    obs3, _ = sim.step(action)
    assert obs3.success_rate > obs2.success_rate
    action_id = sim.action_history[-1]["action_id"]

    # Step 4: Rollback the action and run another step (incident active again)
    sim.rollback_action(action_id)
    assert len(sim.active_actions) == 0

    obs4, _ = sim.step()
    # It should degrade back to poor success rate because the bypass was removed.
    assert obs4.success_rate < obs3.success_rate


def test_deterministic_replay_produces_same_outcomes(default_config, mock_gateway_incident):
    sim1 = StatefulSimulator(default_config, [mock_gateway_incident])
    sim1.reset()
    sim1.step()
    obs1_s2, _ = sim1.step()
    action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 80.0,
        }
    }
    obs1_s3, _ = sim1.step(action)

    # Instantiate identical second simulator
    sim2 = StatefulSimulator(default_config, [mock_gateway_incident])
    sim2.reset()
    sim2.step()
    obs2_s2, _ = sim2.step()
    obs2_s3, _ = sim2.step(action)

    assert obs1_s2.success_rate == obs2_s2.success_rate
    assert obs1_s3.success_rate == obs2_s3.success_rate
    assert obs1_s3.latency == obs2_s3.latency


def test_baseline_behavior_is_stable_without_actions(default_config):
    sim = StatefulSimulator(default_config, [])
    sim.reset()
    obs1, _ = sim.step()
    obs2, _ = sim.step()
    assert abs(obs1.success_rate - obs2.success_rate) < 0.05
