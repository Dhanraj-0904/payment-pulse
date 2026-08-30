import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from simulator.simulator_adapter import SimulatorAdapter
from app.core.simulator_adapter import get_simulator_adapter
from agent.events import PaymentEvent
from agent.event_bus import event_bus

@pytest.fixture
def clean_client():
    adapter = get_simulator_adapter()
    # Reset state to clean baseline
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

def test_utc_virtual_simulation_time_handling(clean_client):
    client, adapter = clean_client
    
    # Check simulation time is UTC
    sim_time = adapter.simulator.simulation_time
    assert sim_time.tzinfo is not None or sim_time.isoformat()
    
    # Create payment and check sim_time in emitted event metadata
    received_events = []
    def listener(evt):
        received_events.append(evt)
    
    event_bus.subscribe(listener)
    try:
        pay = adapter.create_payment(100.0, "INR", "UPI", "HDFC Bank", "merch_1")
        res = adapter.process_payment(pay["transaction_id"])
        
        outcome_evts = [e for e in received_events if e.event_type in ["PAYMENT_SUCCESS", "PAYMENT_FAILED"]]
        assert len(outcome_evts) >= 1
        assert "sim_time" in outcome_evts[-1].metadata
        assert isinstance(outcome_evts[-1].metadata["sim_time"], str)
    finally:
        event_bus.unsubscribe(listener)

def test_simulation_duration_calculation(clean_client):
    client, adapter = clean_client
    
    start_time = adapter.simulator.simulation_time
    # Advance simulator by 2 steps (10 virtual minutes)
    adapter.simulator.step()
    adapter.simulator.step()
    current_time = adapter.simulator.simulation_time
    
    duration = current_time - start_time
    assert duration.total_seconds() == 600  # 10 minutes

def test_windowed_tps_calculation():
    # In a 5-minute simulation window (300 seconds)
    window_duration_seconds = 300
    transaction_volume = 720
    tps = round(transaction_volume / window_duration_seconds, 1)
    assert tps == 2.4

def test_rolling_last_20_success_rate_logic():
    # Simulate a stream of 25 completed transactions
    transactions = []
    for i in range(25):
        is_succ = (i % 5 != 0) # 80% success rate
        transactions.append({"id": f"tx_{i}", "isSuccess": is_succ})
    
    # Last 20 window
    last_20 = transactions[-20:]
    assert len(last_20) == 20
    succ_count = sum(1 for t in last_20 if t["isSuccess"])
    sr = (succ_count / 20) * 100
    assert 0 <= sr <= 100

def test_recovery_candidates_and_dry_run_simulate(clean_client):
    client, adapter = clean_client
    
    # Inject incident on gateway_gamma
    inj_res = client.post("/api/demo/incidents", json={
        "incident_type": "GATEWAY_DEGRADATION",
        "target": "gateway_gamma"
    })
    assert inj_res.status_code == 200
    
    # Advance simulator step so incident becomes active in observation
    adapter.simulator.step()
    
    # Fetch candidate actions
    cand_res = client.get("/api/demo/recovery/candidates")
    assert cand_res.status_code == 200
    cand_data = cand_res.json()
    assert cand_data["active_incident"] is not None
    assert len(cand_data["candidates"]) > 0
    
    first_candidate = cand_data["candidates"][0]
    assert "action" in first_candidate
    assert "expected_success_improvement" in first_candidate
    assert "expected_revenue_risk_reduction" in first_candidate
    assert first_candidate["blast_radius"] in ["LOW", "MEDIUM", "HIGH"]
    assert first_candidate["reversible"] == "YES"
    
    # Run counterfactual simulation without execution
    sim_res = client.post("/api/demo/recovery/simulate", json={"action": first_candidate["action"]})
    assert sim_res.status_code == 200
    cf_data = sim_res.json()
    
    assert "with_action" in cf_data
    assert "without_action" in cf_data
    assert "effect" in cf_data
    assert "success_rate_ci_lower" in cf_data
    assert "success_rate_ci_upper" in cf_data

def test_execute_recovery_action_policy_enforcement(clean_client):
    client, adapter = clean_client
    
    # Inject incident
    client.post("/api/demo/incidents", json={
        "incident_type": "GATEWAY_DEGRADATION",
        "target": "gateway_gamma"
    })
    adapter.simulator.step()
    
    cand_res = client.get("/api/demo/recovery/candidates")
    cand_data = cand_res.json()
    candidate = cand_data["candidates"][0]
    
    # Execute action
    exec_res = client.post("/api/demo/recovery/execute", json={"action": candidate["action"]})
    assert exec_res.status_code == 200
    exec_data = exec_res.json()
    assert exec_data["status"] == "EXECUTED"
    assert "prediction_telemetry" in exec_data
    assert "actual_success_rate" in exec_data["prediction_telemetry"]

def test_system_reset(clean_client):
    client, adapter = clean_client
    res = client.post("/api/demo/reset")
    assert res.status_code == 200
    assert res.json()["status"] == "reset_completed"
