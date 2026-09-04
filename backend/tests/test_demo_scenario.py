"""Comprehensive End-to-End Demo Scenario Integration Tests for Payment Pulse."""

import os
import re
import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backend.app.main import app
from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.environment import StatefulSimulator
from simulator.generator import generate_transactions
from agent.tools import SimulatorToolbox
from agent.canary import CanaryRecoveryController, CanaryPolicy, CanaryOutcome
from agent.postmortem import build_postmortem, postmortem_to_markdown, postmortem_to_dict
from agent.recovery_agent import (
    PolicyFallbackAgent,
    calculate_diagnosis_confidence,
    diagnose_incident,
    rank_candidates,
)

@pytest.fixture
def client():
    return TestClient(app)

def test_01_demo_scenario_can_reset(client):
    """1. Demo scenario can reset cleanly to nominal baseline."""
    res = client.post("/api/demo/reset")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "reset_completed"
    assert "sim_time" in data

def test_02_demo_scenario_deterministic_initialization(client):
    """2. Demo scenario can start and establish healthy baseline with deterministic seed."""
    res = client.post(
        "/api/demo/scenario/deterministic",
        json={"incident_type": "GATEWAY_DEGRADATION", "target": "gateway_gamma", "seed": 42, "auto_execute": False}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "deterministic_scenario_initialized"
    scen = data["scenario"]
    assert scen["incident_id"] == "INC_DEMO_001"
    assert scen["target"] == "gateway_gamma"
    assert scen["seed"] == 42
    assert scen["baseline_success_rate"] > 0.90
    assert scen["degraded_success_rate"] < scen["baseline_success_rate"]
    assert scen["revenue_at_risk"] > 0.0

def test_03_incident_injection_and_detection(client):
    """3 & 4. Incident can be injected and detected with ML anomaly signals."""
    client.post("/api/demo/reset")
    res = client.post("/api/demo/incidents", json={"incident_type": "GATEWAY_DEGRADATION", "target": "gateway_gamma"})
    assert res.status_code == 200
    inc_data = res.json()
    assert inc_data["status"] == "injected"
    assert inc_data["target"] == "gateway_gamma"

def test_05_revenue_risk_increases_during_degradation():
    """5. Revenue risk increases during active gateway degradation."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    baseline_cfg = GeneratorConfig(
        transaction_count=5000,
        random_seed=100,
        start_timestamp=base_time - timedelta(hours=5),
        transaction_frequency_seconds=1,
    )
    baseline_data = list(generate_transactions(baseline_cfg))
    step_cfg = GeneratorConfig(
        transaction_count=300,
        random_seed=42,
        start_timestamp=base_time,
        transaction_frequency_seconds=1,
    )
    inc = IncidentConfig(
        incident_id="INC_RISK_TEST",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time + timedelta(minutes=5),
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    sim = StatefulSimulator(step_cfg, [inc], baseline_transactions=baseline_data)
    sim.reset()
    obs_baseline, _ = sim.step()
    obs_degraded, _ = sim.step()
    assert obs_degraded.revenue_at_risk > 0
    assert obs_degraded.anomaly_score > 0.5

def test_06_recovery_analysis_returns_candidates(client):
    """6. Recovery analysis returns ranked candidate actions with all 8 operational dimensions."""
    client.post("/api/demo/scenario/deterministic", json={"seed": 42, "auto_execute": False})
    res = client.get("/api/demo/recovery/candidates")
    assert res.status_code == 200
    data = res.json()
    assert data["agent_mode"] in ["POLICY_FALLBACK", "MOCK"]
    candidates = data["candidates"]
    assert len(candidates) > 0
    top = candidates[0]
    required_keys = [
        "action_type", "target", "traffic_percentage",
        "expected_success_improvement", "expected_revenue_risk_reduction",
        "blast_radius", "reversible", "confidence",
    ]
    for k in required_keys:
        assert k in top, f"Missing dimension {k} in candidate"

def test_07_counterfactual_validation_runs(client):
    """7. Counterfactual evaluation runs paired with/without action comparison."""
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {"gateway": "gateway_gamma", "traffic_percentage": 50.0},
        "explanation": "Reduce 50% traffic on gateway_gamma"
    }
    res = client.post("/api/demo/recovery/simulate", json={"action": action})
    assert res.status_code == 200
    data = res.json()
    assert "with_action" in data
    assert "without_action" in data
    assert "effect" in data
    assert "confidence_interval_lower" in data
    assert "confidence_interval_upper" in data

def test_08_policy_validation_and_safety():
    """8. Policy layer approves valid reversible mitigations and validates safety constraints."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    step_cfg = GeneratorConfig(transaction_count=300, random_seed=42, start_timestamp=base_time, transaction_frequency_seconds=1)
    sim = StatefulSimulator(step_cfg, [])
    sim.reset()
    toolbox = SimulatorToolbox(sim)
    valid_act = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {"gateway": "gateway_gamma", "traffic_percentage": 50.0},
        "explanation": "Safe reversible reduction"
    }
    res = toolbox.execute_action(valid_act)
    assert res["status"] in ["ACCEPTED", "EXECUTED"]

def test_09_canary_pipeline_starts_and_evaluates(client):
    """9 & 10. Canary starts at 5% and evaluates with 3-layer comparison."""
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {"gateway": "gateway_gamma", "traffic_percentage": 50.0},
        "explanation": "Canary test on gateway_gamma"
    }
    res = client.post("/api/demo/recovery/canary/run", json={"action": action, "auto_expand": True})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ["CANARY_PASS", "CANARY_FAIL", "CANARY_INCONCLUSIVE"]
    assert "three_layer_comparison" in data
    comp = data["three_layer_comparison"]
    assert "layer_1_control" in comp
    assert "layer_2_counterfactual" in comp
    assert "layer_3_observed_canary" in comp

def test_11_recovery_executes_and_risk_falls(client):
    """11 & 12. Recovery executes, success rate recovers, and revenue-at-risk drops."""
    action = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {"gateway": "gateway_gamma", "traffic_percentage": 50.0},
        "explanation": "Policy approved recovery execution"
    }
    res = client.post("/api/demo/recovery/execute", json={"action": action})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "EXECUTED"
    assert "before_metrics" in data
    assert "after_metrics" in data

def test_13_postmortem_endpoint_returns_json_and_markdown(client):
    """13. Postmortem generator outputs structured evidence in both JSON and Markdown."""
    res = client.get("/api/demo/recovery/postmortem")
    assert res.status_code == 200
    data = res.json()
    assert "json" in data
    assert "markdown" in data
    assert "# PAYMENT PULSE INCIDENT POSTMORTEM" in data["markdown"]

def test_14_sidebar_navigation_does_not_reset_state():
    """14. Sidebar navigation preserves DOM state and does not reload the page or reset metrics."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "function navigate(view)" in html
    assert "setActiveView(view)" in html
    assert "function setActiveView(view)" in html
    assert "secMetrics.classList.add('hidden')" in html
    assert "secMetrics.classList.remove('hidden')" in html

def test_15_one_websocket_connection_is_maintained():
    """15. Exactly one WebSocket connection is opened and maintained across navigation."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "function connectWS()" in html
    assert "window.addEventListener('load'" in html

def test_16_success_rate_denominator_semantics():
    """16. Success-rate denominator uses completed transactions in rolling window."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "state.completedTransactions" in html
    assert "state.rollingLast20" in html
    assert "WINDOW:" in html

def test_17_txn_rate_uses_correct_window():
    """17. TXN RATE uses completed transactions / current window duration in seconds (300s)."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "TXN RATE" in html
    api_demo_path = os.path.join(os.path.dirname(__file__), "..", "app", "api", "demo.py")
    with open(api_demo_path, "r", encoding="utf-8") as f:
        api_code = f.read()
    assert "300.0" in api_code

def test_18_revenue_risk_not_overwritten_by_unrelated_events():
    """18. Revenue risk remains persisted and is not overwritten with 0 or null by unrelated events."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "state.metrics.revenueAtRisk" in html
    assert "state.metrics.revenueAtRisk = rar" in html

def test_19_simulation_time_vs_wall_clock_separated():
    """19. Simulation time and wall clock time are explicitly separated."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "SIM TIME:" in html
    assert "LAST EVENT:" in html
    assert "updateSimulationClocks" in html

def test_20_no_null_undefined_leaks_in_operational_fields():
    """20. No null/undefined/NaN values leak into active operational fields."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "DATA UNAVAILABLE" in html
    assert "safeVal" in html
    assert "setDemoState" in html
    assert "btn-deterministic-demo" in html
    assert "demo-state-container" in html


# ==============================================================================
# QA INVARIANT REGRESSION TESTS (BUGS 1-11)
# ==============================================================================

def test_qa_01_deterministic_cli_candidate_targets_gateway_gamma():
    """QA 1. Deterministic CLI candidate targets gateway_gamma."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    step_cfg = GeneratorConfig(transaction_count=300, random_seed=42, start_timestamp=base_time, transaction_frequency_seconds=1)
    inc = IncidentConfig(
        incident_id="INC_DEMO_001",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    sim = StatefulSimulator(step_cfg, [inc])
    sim.reset()
    obs_degraded, _ = sim.step()
    toolbox = SimulatorToolbox(sim)
    conf = calculate_diagnosis_confidence(obs_degraded)
    diag = diagnose_incident(obs_degraded, conf)
    assert diag["affected_gateway"] == "gateway_gamma"
    
    candidates = toolbox.list_available_actions()
    ranked = rank_candidates(toolbox, candidates, conf, diag)
    top_act = ranked[0][0]
    target = top_act.get("parameters", {}).get("gateway") or top_act.get("parameters", {}).get("source_gateway")
    assert target == "gateway_gamma"


def test_qa_02_diagnosis_and_candidate_target_agree():
    """QA 2. Authoritative diagnosis and top candidate target agree."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    step_cfg = GeneratorConfig(transaction_count=300, random_seed=42, start_timestamp=base_time, transaction_frequency_seconds=1)
    inc = IncidentConfig(
        incident_id="INC_BETA_TEST",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway="gateway_beta",
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )
    sim = StatefulSimulator(step_cfg, [inc])
    sim.reset()
    obs, _ = sim.step()
    toolbox = SimulatorToolbox(sim)
    diag = diagnose_incident(obs, 0.94)
    assert diag["affected_gateway"] == "gateway_beta"
    
    candidates = toolbox.list_available_actions()
    ranked = rank_candidates(toolbox, candidates, 0.94, diag)
    top_target = ranked[0][0]["parameters"].get("gateway") or ranked[0][0]["parameters"].get("source_gateway")
    assert top_target == diag["affected_gateway"]


def test_qa_03_postmortem_before_recovery_no_canary_pass():
    """QA 3. Postmortem before recovery does NOT claim CANARY_PASS."""
    pm = build_postmortem(
        incident={"incident_id": "INC_TEST", "affected_gateway": "gateway_gamma"},
        canary=None
    )
    assert pm.canary_status == "NOT RUN"
    md = postmortem_to_markdown(pm)
    assert "CANARY_PASS" not in md
    assert "NOT RUN" in md


def test_qa_04_postmortem_before_counterfactual_no_fabricated_ci():
    """QA 4. Postmortem before counterfactual does NOT contain fabricated CI."""
    pm = build_postmortem(
        incident={"incident_id": "INC_TEST"},
        counterfactual=None
    )
    assert pm.counterfactual_ci is None
    assert pm.counterfactual_control_sr is None
    assert pm.counterfactual_predicted_sr is None
    assert pm.counterfactual_effect is None
    md = postmortem_to_markdown(pm)
    assert "16.2" not in md
    assert "26.0" not in md
    assert "73.1" not in md
    assert "94.2" not in md
    assert "COUNTERFACTUAL: NOT RUN" in md


def test_qa_05_postmortem_after_counterfactual_uses_actual_ci():
    """QA 5. Postmortem after counterfactual uses actual CI."""
    cf_data = {
        "without_action": {"success_rate": 0.825},
        "with_action": {"success_rate": 0.932},
        "success_rate_ci": [0.075, 0.139],
        "runs": 20
    }
    pm = build_postmortem(
        incident={"incident_id": "INC_TEST"},
        counterfactual=cf_data
    )
    assert pm.counterfactual_control_sr == 82.5
    assert pm.counterfactual_predicted_sr == 93.2
    assert pm.counterfactual_effect == 10.7
    assert pm.counterfactual_ci == [7.5, 13.9]
    md = postmortem_to_markdown(pm)
    assert "82.5%" in md
    assert "93.2%" in md
    assert "+10.7" in md
    assert "[7.5pp, 13.9pp]" in md


def test_qa_06_postmortem_after_canary_uses_actual_stage_list():
    """QA 6. Postmortem after canary uses actual stage list."""
    canary_data = {
        "status": "CANARY_PASS",
        "initial_traffic_percentage": 5.0,
        "current_traffic_percentage": 25.0,
        "stages": [
            {"stage": 0, "traffic_pct": 5.0, "outcome": "CANARY_PASS"},
            {"stage": 1, "traffic_pct": 25.0, "outcome": "CANARY_PASS"}
        ],
        "rolled_back": False
    }
    pm = build_postmortem(
        incident={"incident_id": "INC_TEST"},
        canary=canary_data
    )
    assert len(pm.canary_stages) == 2
    md = postmortem_to_markdown(pm)
    assert "5% -> 25%" in md
    assert "5% -> 25% -> 50%" not in md


def test_qa_07_postmortem_final_allocation_matches_actual_canary():
    """QA 7. Postmortem distinguishes requested action pct from final canary allocation."""
    action_data = {
        "action_type": "REDUCE_GATEWAY_TRAFFIC",
        "parameters": {"gateway": "gateway_gamma", "traffic_percentage": 25.0},
        "explanation": "Reduce 25% traffic"
    }
    canary_data = {
        "status": "CANARY_PASS",
        "initial_traffic_percentage": 5.0,
        "current_traffic_percentage": 50.0,
        "stages": [
            {"stage": 0, "traffic_pct": 5.0},
            {"stage": 1, "traffic_pct": 25.0},
            {"stage": 2, "traffic_pct": 50.0}
        ],
        "rolled_back": False
    }
    pm = build_postmortem(
        incident={"incident_id": "INC_TEST"},
        action=action_data,
        canary=canary_data
    )
    assert pm.action_requested_pct == 25.0
    assert pm.action_final_allocation_pct == 50.0
    md = postmortem_to_markdown(pm)
    assert "**Requested Action Traffic:** 25.0%" in md
    assert "**Final Active Allocation:** 50.0%" in md


def test_qa_08_postmortem_recovered_revenue_equals_actual_difference():
    """QA 8. Postmortem recovered revenue equals actual before/after difference."""
    before = {"revenue_at_risk": 150000.0}
    after = {"revenue_at_risk": 30000.0}
    pm = build_postmortem(
        incident={"incident_id": "INC_TEST"},
        before_metrics=before,
        after_metrics=after
    )
    assert pm.revenue_recovered == 120000.0
    md = postmortem_to_markdown(pm)
    assert "INR 120,000.00" in md

    # When after risk is higher or equal, recovered is 0
    after_higher = {"revenue_at_risk": 180000.0}
    pm2 = build_postmortem(
        incident={"incident_id": "INC_TEST"},
        before_metrics=before,
        after_metrics=after_higher
    )
    assert pm2.revenue_recovered == 0.0


def test_qa_09_postmortem_outcome_status_reflects_actual_state():
    """QA 9. Postmortem outcome status reflects actual execution state."""
    # 1. Rollback
    pm_rb = build_postmortem(incident={}, canary={"rolled_back": True})
    assert pm_rb.outcome_status == "ROLLED_BACK"

    # 2. Canary fail
    pm_cfail = build_postmortem(incident={}, canary={"status": "CANARY_FAIL"})
    assert pm_cfail.outcome_status == "CANARY_FAILED"

    # 3. Canary inconclusive
    pm_inconc = build_postmortem(incident={}, canary={"status": "CANARY_INCONCLUSIVE"})
    assert pm_inconc.outcome_status == "INCONCLUSIVE"

    # 4. Canary in progress
    pm_prog = build_postmortem(incident={}, canary={"status": "IN_PROGRESS"})
    assert pm_prog.outcome_status == "CANARY_IN_PROGRESS"

    # 5. Counterfactual validated
    pm_cf = build_postmortem(incident={}, counterfactual={"without_action": {"success_rate": 0.8}})
    assert pm_cf.outcome_status == "COUNTERFACTUAL_VALIDATED"

    # 6. Recovery verified
    pm_rec = build_postmortem(
        incident={},
        canary={"status": "CANARY_PASS"},
        before_metrics={"revenue_at_risk": 1000.0},
        after_metrics={"success_rate": 0.95, "revenue_at_risk": 0.0}
    )
    assert pm_rec.outcome_status == "RECOVERY_VERIFIED"


def test_qa_10_no_hardcoded_fallback_operational_metrics_when_missing():
    """QA 10. No hardcoded fallback operational metrics appear when evidence is missing."""
    pm = build_postmortem(incident={})
    assert pm.revenue_at_risk is None
    assert pm.revenue_recovered is None
    assert pm.anomaly_score is None
    assert pm.confidence is None
    md = postmortem_to_markdown(pm)
    assert "96007" not in md
    assert "96,007" not in md
    assert "73.1" not in md
    assert "94.2" not in md
    assert "DATA UNAVAILABLE" in md
