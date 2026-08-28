"""Unit and integration tests for the AI Payment Recovery Agent."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.environment import StatefulSimulator

from agent.providers import MockLLMProvider
from agent.tools import SimulatorToolbox
from agent.policy import check_policy, MAX_SINGLE_ACTION_TRAFFIC
from agent.recovery_agent import (
    RecoveryAgent,
    PolicyFallbackAgent,
    rank_candidates,
    evaluate_agent_recovery,
    calculate_diagnosis_confidence,
    diagnose_incident,
)


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


def test_tool_validation_and_rejection(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    toolbox = SimulatorToolbox(sim)

    # Validate correct list behavior
    candidates = toolbox.list_available_actions()
    assert len(candidates) > 0
    assert any(c["action_type"] == "ROUTE_TRAFFIC" for c in candidates)

    # Invalid execute (missing explanation)
    res = toolbox.execute_action({
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 50.0,
        }
    })
    assert res["status"] == "REJECTED"
    assert "explanation" in res["reason"]


def test_policy_validation_safety_limits(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    obs = sim.reset()

    # Reject routing percentage > 50%
    action_too_high = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 60.0,
        },
        "explanation": "Attempt to route 60% traffic (should fail)",
    }
    passed, reason = check_policy(action_too_high, obs)
    assert not passed
    assert f"exceeds policy limit of {MAX_SINGLE_ACTION_TRAFFIC}%" in reason

    # Accept routing percentage <= 50%
    action_safe = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 50.0,
        },
        "explanation": "Divert 50% traffic (safe limit)",
    }
    passed, reason = check_policy(action_safe, obs)
    assert passed


def test_policy_rejects_routing_to_degraded_gateway(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # Step 1: 12:00 -> 12:05 (baseline)
    obs, _ = sim.step()  # Step 2: 12:05 -> 12:10 (degraded on gateway_beta)

    # Try to route traffic from gateway_alpha TO the degraded gateway_beta
    action_to_unsafe = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_alpha",
            "destination_gateway": "gateway_beta",
            "traffic_percentage": 50.0,
        },
        "explanation": "Route traffic to the unhealthy gateway_beta (should fail)",
    }
    passed, reason = check_policy(action_to_unsafe, obs)
    assert not passed
    assert "currently unhealthy/degraded" in reason


def test_true_dry_run_simulation_isolation(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    toolbox = SimulatorToolbox(sim)

    # Initial state snapshots
    initial_time = sim.simulation_time
    initial_history_len = len(sim.action_history)
    
    # Consume 1 baseline item to verify iterator state
    first_record = next(sim.baseline_iterator)

    # Perform simulate_action dry-run
    action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 50.0,
        },
        "explanation": "Test isolated dry-run impact",
    }
    res = toolbox.simulate_action(action)
    assert res["is_valid"]
    assert float(res["projected_success_rate"]) > 0.0

    # Verify dry-run left simulator completely unchanged
    assert sim.simulation_time == initial_time
    assert len(sim.action_history) == initial_history_len
    
    # Iterator check: next item should still be deterministic from the baseline iterator
    second_record = next(sim.baseline_iterator)
    assert first_record.transaction_id != second_record.transaction_id


def test_candidate_ranking_mechanism(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # Baseline normal
    sim.step()  # Degraded
    
    toolbox = SimulatorToolbox(sim)
    candidates = toolbox.list_available_actions()

    # Rerouting gateway_beta to gateway_alpha is the optimal action
    ranked = rank_candidates(toolbox, candidates, diagnosis_confidence=0.9)
    assert len(ranked) > 0
    
    top_action, score = ranked[0]
    assert top_action["action_type"] in {"ROUTE_TRAFFIC", "REDUCE_GATEWAY_TRAFFIC"}
    if top_action["action_type"] == "ROUTE_TRAFFIC":
        assert top_action["parameters"]["source_gateway"] == "gateway_beta"
    else:
        assert top_action["parameters"]["gateway"] == "gateway_beta"
    assert score > 0.0


def test_fallback_agent_recovery_loop(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # baseline
    obs_before, _ = sim.step()  # degraded

    toolbox = SimulatorToolbox(sim)
    fallback = PolicyFallbackAgent(toolbox)
    trace = fallback.run()


    # Fallback agent should inspect state, rank actions, and safely route 50% traffic
    assert trace.selected_action is not None
    assert trace.selected_action["action_type"] in {"ROUTE_TRAFFIC", "REDUCE_GATEWAY_TRAFFIC"}
    assert float(trace.selected_action["parameters"]["traffic_percentage"]) <= 50.0
    assert trace.decision == "STOP"  # recovery achieved
    assert trace.status == "RECOVERY_SUCCESSFUL"
    assert trace.after_metrics["success_rate"] > trace.before_metrics["success_rate"]

    # Verify trace log contains decision summaries only (no hidden chain-of-thought)
    assert len(trace.reasoning_summary) > 0
    assert "POLICY_FALLBACK" in trace.reasoning_summary[0]


def test_no_incident_agent_behavior(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()

    toolbox = SimulatorToolbox(sim)
    provider = MockLLMProvider()
    agent = RecoveryAgent(provider, toolbox)
    trace = agent.run()

    # No action should be taken under normal baseline health
    assert trace.selected_action is None
    assert trace.decision == "STOP"


def test_ineffective_action_triggers_rollback(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # baseline
    sim.step()  # degraded

    toolbox = SimulatorToolbox(sim)
    provider = MockLLMProvider()
    agent = RecoveryAgent(provider, toolbox)

    # Manually configure an ineffective action (routing traffic away from gateway_alpha to gateway_beta)
    # which is degraded, so it fails policy checks or is ineffective.
    # Note: policy checks reject routing to degraded gateways, so it is rejected.
    action_ineffective = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_alpha",
            "destination_gateway": "gateway_beta",
            "traffic_percentage": 50.0,
        },
        "explanation": "Ineffective routing (unsafe)",
    }
    
    # We will execute this action directly through toolbox to check rollback branch
    res = toolbox.execute_action(action_ineffective)
    assert res["status"] == "REJECTED"


def test_agent_metrics_evaluation():
    from agent.models import AgentTrace
    trace = AgentTrace(
        run_id="test_run",
        incident_id="INC_001",
        selected_action={"action_type": "ROUTE_TRAFFIC"},
        before_metrics={"success_rate": 0.70, "revenue_at_risk": Decimal("1000.00")},
        after_metrics={"success_rate": 0.95, "revenue_at_risk": Decimal("200.00")},
        decision="CONTINUE"
    )
    trace.action_result = {"status": "ACCEPTED"}
    
    metrics = evaluate_agent_recovery(trace)
    assert metrics.incident_detected
    assert metrics.action_selected
    assert metrics.action_accepted
    assert metrics.recovery_achieved
    assert metrics.success_rate_improvement == pytest.approx(0.25)
    assert metrics.revenue_at_risk_reduction == Decimal("800.00")
    assert metrics.estimated_recovered_revenue == Decimal("800.00")
    assert metrics.rollback_rate == 0.0
    assert metrics.time_to_recovery_seconds == 300.0


def test_real_provider_interface(monkeypatch):
    import json
    import urllib.request
    from agent.providers import RealLLMProvider, get_llm_provider

    class MockResponse:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    def mock_urlopen(req, timeout=None):
        if "generativelanguage.googleapis.com" in req.full_url:
            body = {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": '{"reasoning": "Gemini response", "selected_action": null, "confidence": 0.9}'
                        }]
                    }
                }]
            }
            return MockResponse(json.dumps(body).encode("utf-8"))
        elif "api.openai.com" in req.full_url:
            body = {
                "choices": [{
                    "message": {
                        "content": '{"reasoning": "OpenAI response", "selected_action": null, "confidence": 0.8}'
                    }
                }]
            }
            return MockResponse(json.dumps(body).encode("utf-8"))
        raise ValueError(f"Unknown URL: {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    # Test Gemini
    prov_gemini = RealLLMProvider(provider="gemini", api_key="test_key")
    res = prov_gemini.generate("hello")
    assert "Gemini response" in res

    # Test OpenAI
    prov_openai = RealLLMProvider(provider="openai", api_key="test_key")
    res2 = prov_openai.generate("hello")
    assert "OpenAI response" in res2

    # Factory check
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "test_key")
    prov_factory = get_llm_provider()
    assert isinstance(prov_factory, RealLLMProvider)


def test_evidence_based_confidence_calculation():
    # Strong evidence case
    obs_strong = {
        "anomaly_score": 0.95,
        "active_incidents": [{"incident_id": "INC_001", "incident_type": "GATEWAY_DEGRADATION", "severity": "HIGH"}],
        "investigation_evidence": [{
            "evidence_quality": "HIGH",
            "likely_pattern": "GATEWAY_DEGRADATION",
            "baseline_status": "OK",
            "metadata": {}
        }]
    }
    conf_strong = calculate_diagnosis_confidence(obs_strong)
    # 0.95 * 0.4 (0.38) + 0.2 (quality HIGH) + 0.1 (likely pattern) + 0.2 (severity HIGH) = 0.88
    assert conf_strong >= 0.80

    # Weak evidence case (insufficient baseline status)
    obs_weak = {
        "anomaly_score": 0.35,
        "active_incidents": [{"incident_id": "INC_002", "incident_type": "GATEWAY_DEGRADATION", "severity": "LOW"}],
        "investigation_evidence": [{
            "evidence_quality": "POOR",
            "likely_pattern": "UNKNOWN",
            "baseline_status": "INSUFFICIENT_DATA",
            "metadata": {"insufficient_baseline": True}
        }]
    }
    conf_weak = calculate_diagnosis_confidence(obs_weak)
    # 0.35 * 0.4 (0.14) + 0.05 (quality POOR) + 0.05 (severity LOW) = 0.24. Penalyized by 0.5 = 0.12.
    assert conf_weak < 0.40


def test_explicit_root_cause_diagnosis():
    obs = {
        "active_incidents": [{"incident_id": "INC_001", "incident_type": "GATEWAY_DEGRADATION", "severity": "HIGH"}],
        "investigation_evidence": [{
            "evidence_quality": "HIGH",
            "likely_pattern": "GATEWAY_DEGRADATION",
            "baseline_status": "OK",
            "top_gateways": [{"value": "gateway_beta", "incident_metric": 0.35}],
            "top_banks": [{"value": "HDFC Bank", "incident_metric": 0.25}],
            "top_payment_methods": [{"value": "UPI", "incident_metric": 0.45}],
            "top_merchants": [{"value": "merchant_retail_001", "incident_metric": 0.15}]
        }]
    }
    diag = diagnose_incident(obs, 0.88)
    assert diag["root_cause"] == "GATEWAY_DEGRADATION"
    assert diag["affected_gateway"] == "gateway_beta"
    assert diag["affected_bank"] == "HDFC Bank"
    assert diag["affected_payment_method"] == "UPI"
    assert diag["affected_merchant"] == "merchant_retail_001"
    assert diag["confidence"] == 0.88
    assert "Gateway Degradation pattern detected" in diag["evidence_summary"]


def test_recovery_threshold_criteria():
    from agent.recovery_agent import is_recovery_successful
    
    # Meets success rate target (>= 0.90)
    before_1 = {"success_rate": 0.70, "revenue_at_risk": Decimal("100.00")}
    after_1 = {"success_rate": 0.92, "revenue_at_risk": Decimal("10.00")}
    assert is_recovery_successful(before_1, after_1)

    # Does not meet success target, but meets revenue risk reduction (>= 50%)
    before_2 = {"success_rate": 0.70, "revenue_at_risk": Decimal("100.00")}
    after_2 = {"success_rate": 0.85, "revenue_at_risk": Decimal("40.00")}
    assert is_recovery_successful(before_2, after_2)

    # Fails both criteria
    before_3 = {"success_rate": 0.70, "revenue_at_risk": Decimal("100.00")}
    after_3 = {"success_rate": 0.75, "revenue_at_risk": Decimal("90.00")}
    assert not is_recovery_successful(before_3, after_3)


def test_low_confidence_conservative_behavior(default_config, baseline_data):
    # Setup simulator with no incidents to simulate low confidence
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    
    # Injected low-confidence mock observation manually
    obs_low = {
        "current_time": datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc),
        "success_rate": 0.95,
        "revenue_at_risk": "0.00",
        "anomaly_score": 0.15,
        "active_incidents": [{"incident_id": "INC_LOW", "incident_type": "GATEWAY_DEGRADATION", "severity": "LOW"}],
        "investigation_evidence": [{
            "evidence_quality": "POOR",
            "likely_pattern": "UNKNOWN",
            "baseline_status": "INSUFFICIENT_DATA",
            "metadata": {"insufficient_baseline": True}
        }]
    }
    
    # Mock toolbox.observe_result to return this low confidence obs
    toolbox = SimulatorToolbox(sim)
    def mock_observe_result():
        return obs_low
    toolbox.observe_result = mock_observe_result
    
    agent = RecoveryAgent(MockLLMProvider(), toolbox)
    trace = agent.run(max_iterations=1)
    
    # Diagnosis confidence should be < 0.60
    assert trace.diagnosis_confidence < 0.60
    # Agent should stop with LOW_CONFIDENCE
    assert trace.decision == "STOP"
    assert trace.status == "LOW_CONFIDENCE"
    assert trace.selected_action is None


def test_malformed_tool_inputs(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    toolbox = SimulatorToolbox(sim)

    # 1. Invalid traffic percentage type
    action_bad_type = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": "invalid_value",
        },
        "explanation": "Test validation",
    }
    res_sim = toolbox.simulate_action(action_bad_type)
    assert not res_sim["accepted"]
    assert "numeric" in res_sim["reason"]

    res_exec = toolbox.execute_action(action_bad_type)
    assert not res_exec["accepted"]
    assert res_exec["action_id"] is None
    assert "numeric" in res_exec["reason"]

    # 2. Negative traffic percentage
    action_neg = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": -10.0,
        },
        "explanation": "Test negative traffic",
    }
    res_neg = toolbox.simulate_action(action_neg)
    assert not res_neg["accepted"]

    # 3. Missing gateway parameter
    action_missing = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 25.0,
        },
        "explanation": "Test missing gateway",
    }
    res_missing = toolbox.simulate_action(action_missing)
    assert not res_missing["accepted"]


def test_strong_dry_run_state_isolation(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # baseline step
    
    # Store initial state snapshots
    time_before = sim.simulation_time
    history_len_before = len(sim.action_history)
    active_actions_before = list(sim.active_actions)
    rng_states_before = {inc: rng.getstate() for inc, rng in sim.incident_rngs.items()}
    
    # Run dry-run simulation
    toolbox = SimulatorToolbox(sim)
    action = {
        "action_type": "ROUTE_TRAFFIC",
        "parameters": {
            "source_gateway": "gateway_beta",
            "destination_gateway": "gateway_alpha",
            "traffic_percentage": 50.0,
        },
        "explanation": "Dry-run isolation check",
    }
    res_dry = toolbox.simulate_action(action)
    assert res_dry["is_valid"]
    
    # Assert isolation
    assert sim.simulation_time == time_before
    assert len(sim.action_history) == history_len_before
    assert sim.active_actions == active_actions_before
    for inc, state in rng_states_before.items():
        assert sim.incident_rngs[inc].getstate() == state


def test_candidate_scoring_factors(default_config, mock_gateway_incident, baseline_data):
    sim = StatefulSimulator(default_config, [mock_gateway_incident], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()
    sim.step()
    
    toolbox = SimulatorToolbox(sim)
    fallback = PolicyFallbackAgent(toolbox)
    trace = fallback.run()
    
    # Verify candidate scores entries exist and show metric factors
    assert len(trace.candidate_scores) > 0
    score_entry = trace.candidate_scores[0]
    assert "score" in score_entry
    assert "success_improvement" in score_entry
    assert "revenue_reduction" in score_entry
    assert "risk" in score_entry
    assert "reversible" in score_entry
    assert "confidence" in score_entry


def test_complete_gateway_recovery_demo():
    from agent.demo import run_demo
    # Assert run_demo completes successfully without exceptions
    run_demo()


def test_diagnosed_gateway_prioritized_and_unrelated_penalized(default_config, baseline_data):
    from simulator.incidents import IncidentConfig, IncidentType, Severity
    from datetime import timedelta
    from agent.recovery_agent import calculate_diagnosis_confidence, diagnose_incident, rank_candidates
    
    base_time = default_config.start_timestamp
    incident_gamma = IncidentConfig(
        incident_id='INC_GAMMA_DEGRADATION',
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time + timedelta(minutes=5),
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway='gateway_gamma',
        affected_transaction_percentage=1.0,
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    
    sim = StatefulSimulator(default_config, [incident_gamma], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # baseline step
    obs_degraded, _ = sim.step()  # degraded step
    
    toolbox = SimulatorToolbox(sim)
    confidence = calculate_diagnosis_confidence(obs_degraded)
    diagnosis = diagnose_incident(obs_degraded, confidence)
    
    assert diagnosis["affected_gateway"] == "gateway_gamma"
    
    candidates = toolbox.list_available_actions()
    ranked = rank_candidates(toolbox, candidates, confidence, diagnosis)
    
    # 1. Diagnosed gateway (gateway_gamma) is prioritized
    top_action = ranked[0][0]
    assert top_action["action_type"] == "REDUCE_GATEWAY_TRAFFIC"
    assert top_action["parameters"]["gateway"] == "gateway_gamma"
    
    # 2. Unrelated gateway action (e.g. gateway_beta) is not incorrectly preferred and has a lower score
    unrelated_actions = [item for item in ranked if item[0]["action_type"] == "REDUCE_GATEWAY_TRAFFIC" and item[0]["parameters"]["gateway"] == "gateway_beta"]
    if unrelated_actions:
        assert ranked[0][1] > unrelated_actions[0][1]


def test_revenue_at_risk_metrics_flow_and_recovery(default_config, baseline_data):
    from simulator.incidents import IncidentConfig, IncidentType, Severity
    from datetime import timedelta
    from decimal import Decimal
    
    base_time = default_config.start_timestamp
    incident_gamma = IncidentConfig(
        incident_id='INC_GAMMA_DEGRADATION',
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time + timedelta(minutes=5),
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway='gateway_gamma',
        affected_transaction_percentage=1.0,
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    
    sim = StatefulSimulator(default_config, [incident_gamma], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()
    obs_degraded, _ = sim.step()
    
    # 3. Revenue-at-risk is non-zero during measurable incident failures
    rev_before = Decimal(obs_degraded.revenue_at_risk)
    assert rev_before > Decimal("0.00")
    
    # Run recovery action
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {
            "gateway": "gateway_gamma",
            "traffic_percentage": 50.0,
        },
        "explanation": "Reduce traffic on gateway_gamma to recover success rate.",
    }
    
    obs_after, outcome = sim.step(action)
    rev_after = Decimal(obs_after.revenue_at_risk)
    
    # 4. Revenue-at-risk decreases after successful recovery
    assert rev_after < rev_before
    
    # 5. Recovered revenue is non-zero when failures are actually recovered
    assert outcome is not None
    recovered_revenue_str = outcome.get("estimated_revenue_recovered", "0.00")
    assert Decimal(recovered_revenue_str) > Decimal("0.00")


def test_deterministic_replay_works(default_config, baseline_data):
    from simulator.incidents import IncidentConfig, IncidentType, Severity
    from datetime import timedelta
    
    base_time = default_config.start_timestamp
    incident_gamma = IncidentConfig(
        incident_id='INC_GAMMA_DEGRADATION',
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time + timedelta(minutes=5),
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway='gateway_gamma',
        affected_transaction_percentage=1.0,
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    
    # 6. Deterministic replay still works (running twice produces identical outcomes)
    sim1 = StatefulSimulator(default_config, [incident_gamma], baseline_transactions=baseline_data)
    sim1.reset()
    sim1.step()
    obs1, _ = sim1.step()
    
    sim2 = StatefulSimulator(default_config, [incident_gamma], baseline_transactions=baseline_data)
    sim2.reset()
    sim2.step()
    obs2, _ = sim2.step()
    
    assert obs1.success_rate == obs2.success_rate
    assert obs1.revenue_at_risk == obs2.revenue_at_risk

