import pytest
from decimal import Decimal
from datetime import datetime, timezone
from pydantic import ValidationError
from agent.events import PaymentEvent
from agent.event_bus import event_bus
from simulator.config import GeneratorConfig
from simulator.environment import StatefulSimulator
from simulator.simulator_adapter import SimulatorAdapter
from app.core.simulator_adapter import get_simulator_adapter
from datetime import timedelta

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

def test_event_schema_validation():
    # Valid schema check
    evt = PaymentEvent(
        event_type="PAYMENT_INITIATED",
        transaction_id="tx_123",
        amount=150.00,
        currency="INR",
        payment_method="UPI",
        bank="SBI",
        gateway="gateway_alpha",
        merchant="merchant_retail_001",
        status="INITIATED"
    )
    assert evt.event_type == "PAYMENT_INITIATED"
    assert evt.currency == "INR"

    # Missing mandatory field: event_type should cause a ValidationError
    with pytest.raises(ValidationError):
        PaymentEvent(transaction_id="tx_123")  # type: ignore

def test_event_bus_pub_sub():
    received = []
    def callback(evt):
        received.append(evt)

    event_bus.subscribe(callback)
    
    evt = PaymentEvent(
        event_type="PAYMENT_SUCCESS",
        transaction_id="tx_456",
        amount=100.0,
        status="SUCCESS"
    )
    event_bus.publish(evt)
    
    assert len(received) == 1
    assert received[0].transaction_id == "tx_456"
    assert received[0].event_type == "PAYMENT_SUCCESS"

def test_simulator_adapter_flow(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    sim.step() # baseline window

    adapter = SimulatorAdapter(sim)
    
    # 1. Create Payment
    init_res = adapter.create_payment(
        amount=500.00,
        currency="INR",
        payment_method="UPI",
        bank="HDFC Bank",
        merchant="merchant_retail_001"
    )
    assert init_res["transaction_id"] is not None
    assert init_res["gateway"] in ["gateway_alpha", "gateway_beta", "gateway_gamma"]
    assert init_res["status"] == "PROCESSING"

    # 2. Process Payment
    tx_id = init_res["transaction_id"]
    proc_res = adapter.process_payment(tx_id)
    assert proc_res["transaction_id"] == tx_id
    assert proc_res["status"] == "SUCCESS"
    assert proc_res["latency_ms"] > 0

def test_gateway_degradation_injection(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()

    adapter = SimulatorAdapter(sim)
    
    # Check gateway_gamma status is healthy (no incidents configured)
    assert len(sim.incidents_config) == 0

    # Inject gateway_gamma degradation
    from simulator.incidents import IncidentConfig, IncidentType, Severity
    from simulator.injector import _rng_for_incident
    
    inc = IncidentConfig(
        incident_id="INC_TEST_DEGRAD",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        start_time=sim.simulation_time,
        duration_minutes=20,
        severity=Severity.HIGH,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0
    )
    sim.incidents_config.append(inc)
    sim.incident_rngs[inc.incident_id] = _rng_for_incident(sim.incident_seed, inc.incident_id)

    # Force a transaction through gateway_gamma to assert failure propagation
    # We mock adapter routing to force selection of gateway_gamma
    init_res = adapter.create_payment(
        amount=1000.0,
        currency="INR",
        payment_method="UPI",
        bank="SBI",
        merchant="merchant_retail_001"
    )
    # Reroute to gateway_gamma for testing incident assertion
    from dataclasses import replace
    adapter.pending_payments[init_res["transaction_id"]] = replace(
        adapter.pending_payments[init_res["transaction_id"]],
        gateway="gateway_gamma"
    )

    # In gateway_gamma degradation, transaction should fail stochastically or be processed under incident
    # (intensity is 1.0, failure multiplier is 6.0, excess failure probability is ~17.5%)
    # Let's run a loop of 30 payments routed through gateway_gamma to assert we see non-zero failures
    failures = 0
    for i in range(30):
        pay = adapter.create_payment(100.0, "INR", "UPI", "SBI", "merchant_retail_001")
        adapter.pending_payments[pay["transaction_id"]] = replace(
            adapter.pending_payments[pay["transaction_id"]],
            gateway="gateway_gamma"
        )
        res = adapter.process_payment(pay["transaction_id"])
        if res["status"] == "FAILED":
            failures += 1
            assert res["error_code"] == "NETWORK_ERROR"

    assert failures > 0

def test_deterministic_behavior_and_isolation(default_config, baseline_data):
    sim = StatefulSimulator(default_config, [], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()
    len_first = len(sim.last_step_transactions)

    sim.reset()
    sim.step()
    assert len(sim.last_step_transactions) == len_first
