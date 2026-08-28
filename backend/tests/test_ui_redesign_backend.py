import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.environment import StatefulSimulator
from agent.events import PaymentEvent
from agent.event_bus import event_bus
from backend.app.api.demo import TrafficRunner

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

def test_incident_detected_event_gateway_degradation(default_config, baseline_data, base_time):
    inc = IncidentConfig(
        incident_id="INC_GW_DEGRADATION",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway="gateway_gamma",
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )

    sim = StatefulSimulator(default_config, [inc], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()  # step so incident starts

    class MockAdapter:
        def __init__(self, simulator):
            self.simulator = simulator

    runner = TrafficRunner()
    runner.adapter = MockAdapter(sim)

    # Capture published events
    published_events = []
    def mock_publish(evt):
        if evt.event_type == "INCIDENT_DETECTED":
            published_events.append(evt)

    orig_publish = event_bus.publish
    event_bus.publish = mock_publish

    try:
        runner._step_simulator()
    finally:
        event_bus.publish = orig_publish

    assert len(published_events) == 1
    evt = published_events[0]
    assert evt.gateway == "gateway_gamma"
    assert evt.bank is None
    assert evt.payment_method is None
    assert evt.location is None
    assert evt.merchant is None
    assert evt.metadata["affected_entity"] == "gateway_gamma"
    assert evt.metadata["affected_entity_type"] == "GATEWAY"
    assert evt.metadata["started_at"] is not None

def test_incident_detected_event_bank_timeout(default_config, baseline_data, base_time):
    inc = IncidentConfig(
        incident_id="INC_BANK_TIMEOUT",
        incident_type=IncidentType.BANK_UPI_TIMEOUT,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        recovery_minutes=0,
        affected_bank="HDFC Bank",
        affected_payment_method="UPI",
        failure_rate_multiplier=8.0,
        latency_multiplier=4.0,
    )

    sim = StatefulSimulator(default_config, [inc], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()

    class MockAdapter:
        def __init__(self, simulator):
            self.simulator = simulator

    runner = TrafficRunner()
    runner.adapter = MockAdapter(sim)

    published_events = []
    def mock_publish(evt):
        if evt.event_type == "INCIDENT_DETECTED":
            published_events.append(evt)

    orig_publish = event_bus.publish
    event_bus.publish = mock_publish

    try:
        runner._step_simulator()
    finally:
        event_bus.publish = orig_publish

    assert len(published_events) == 1
    evt = published_events[0]
    assert evt.bank == "HDFC Bank"
    assert evt.payment_method == "UPI"
    assert evt.gateway is None
    assert evt.location is None
    assert evt.merchant is None
    assert evt.metadata["affected_entity"] == "HDFC Bank"
    assert evt.metadata["affected_entity_type"] == "BANK"

def test_incident_detected_event_regional_network(default_config, baseline_data, base_time):
    inc = IncidentConfig(
        incident_id="INC_NET_DEGRADATION",
        incident_type=IncidentType.REGIONAL_NETWORK_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        recovery_minutes=0,
        affected_location="Pune",
        failure_rate_multiplier=5.0,
        latency_multiplier=3.5,
    )

    sim = StatefulSimulator(default_config, [inc], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()

    class MockAdapter:
        def __init__(self, simulator):
            self.simulator = simulator

    runner = TrafficRunner()
    runner.adapter = MockAdapter(sim)

    published_events = []
    def mock_publish(evt):
        if evt.event_type == "INCIDENT_DETECTED":
            published_events.append(evt)

    orig_publish = event_bus.publish
    event_bus.publish = mock_publish

    try:
        runner._step_simulator()
    finally:
        event_bus.publish = orig_publish

    assert len(published_events) == 1
    evt = published_events[0]
    assert evt.location == "Pune"
    assert evt.gateway is None
    assert evt.bank is None
    assert evt.payment_method is None
    assert evt.merchant is None
    assert evt.metadata["affected_entity"] == "Pune"
    assert evt.metadata["affected_entity_type"] == "LOCATION"

def test_incident_detected_event_merchant_failure(default_config, baseline_data, base_time):
    inc = IncidentConfig(
        incident_id="INC_MERCH_FAILURE",
        incident_type=IncidentType.MERCHANT_SPECIFIC_FAILURE,
        severity=Severity.HIGH,
        start_time=base_time,
        duration_minutes=20,
        recovery_minutes=0,
        affected_merchant="merchant_retail_001",
        failure_rate_multiplier=6.0,
        latency_multiplier=1.5,
    )

    sim = StatefulSimulator(default_config, [inc], baseline_transactions=baseline_data)
    sim.reset()
    sim.step()

    class MockAdapter:
        def __init__(self, simulator):
            self.simulator = simulator

    runner = TrafficRunner()
    runner.adapter = MockAdapter(sim)

    published_events = []
    def mock_publish(evt):
        if evt.event_type == "INCIDENT_DETECTED":
            published_events.append(evt)

    orig_publish = event_bus.publish
    event_bus.publish = mock_publish

    try:
        runner._step_simulator()
    finally:
        event_bus.publish = orig_publish

    assert len(published_events) == 1
    evt = published_events[0]
    assert evt.merchant == "merchant_retail_001"
    assert evt.gateway is None
    assert evt.bank is None
    assert evt.payment_method is None
    assert evt.location is None
    assert evt.metadata["affected_entity"] == "merchant_retail_001"
    assert evt.metadata["affected_entity_type"] == "MERCHANT"
