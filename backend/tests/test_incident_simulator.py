from datetime import datetime, timezone

import pytest

from simulator.config import GeneratorConfig
from simulator.generator import generate_transactions
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.injector import inject_incident, inject_scenario
from simulator.metrics import calculate_incident_metrics


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def baseline():
    return list(generate_transactions(GeneratorConfig(9_000, 31, START, 1)))


def config(incident_type: IncidentType, **dimensions) -> IncidentConfig:
    return IncidentConfig(
        incident_id=f"INC_{incident_type.value}",
        incident_type=incident_type,
        start_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        duration_minutes=30,
        severity=Severity.HIGH,
        failure_rate_multiplier=8,
        latency_multiplier=4,
        recovery_minutes=10,
        **dimensions,
    )


@pytest.mark.parametrize(
    ("incident_type", "dimensions"),
    [
        (IncidentType.BANK_UPI_TIMEOUT, {"affected_bank": "HDFC Bank", "affected_payment_method": "UPI"}),
        (IncidentType.GATEWAY_DEGRADATION, {"affected_gateway": "gateway_alpha"}),
        (IncidentType.CARD_AUTH_FAILURE, {"affected_payment_method": "CARD", "affected_bank": "HDFC Bank"}),
        (IncidentType.REGIONAL_NETWORK_DEGRADATION, {"affected_location": "Delhi"}),
        (IncidentType.MERCHANT_SPECIFIC_FAILURE, {"affected_merchant": "merchant_retail_001"}),
    ],
)
def test_each_incident_type_changes_its_intended_population(baseline, incident_type, dimensions):
    incident = config(incident_type, **dimensions)
    scenario, truth = inject_incident(baseline, incident, incident_seed=7)

    changed = [record for record in scenario if record.incident_id == incident.incident_id]
    assert changed
    assert truth.affected_transaction_count == len(changed)
    for record in changed:
        for attribute, expected in dimensions.items():
            assert getattr(record, attribute.replace("affected_", "").replace("merchant", "merchant_id")) == expected


def test_bank_upi_timeout_only_affects_intended_bank_and_upi(baseline):
    incident = config(IncidentType.BANK_UPI_TIMEOUT, affected_bank="HDFC Bank", affected_payment_method="UPI")
    scenario, _ = inject_incident(baseline, incident, incident_seed=9)

    for original, changed in zip(baseline, scenario):
        if changed.incident_id == incident.incident_id:
            assert changed.bank == "HDFC Bank"
            assert changed.payment_method == "UPI"
        elif original.timestamp < incident.recovery_end_time:
            assert changed == original


def test_card_auth_failure_does_not_affect_other_methods(baseline):
    incident = config(IncidentType.CARD_AUTH_FAILURE, affected_payment_method="CARD", affected_bank="HDFC Bank")
    scenario, _ = inject_incident(baseline, incident, incident_seed=11)

    assert all(changed == original for original, changed in zip(baseline, scenario) if original.payment_method != "CARD")


def test_merchant_and_regional_incidents_do_not_affect_unrelated_population(baseline):
    merchant = config(IncidentType.MERCHANT_SPECIFIC_FAILURE, affected_merchant="merchant_retail_001")
    merchant_scenario, _ = inject_incident(baseline, merchant, incident_seed=13)
    assert all(
        changed == original
        for original, changed in zip(baseline, merchant_scenario)
        if original.merchant_id != "merchant_retail_001"
    )
    regional = config(IncidentType.REGIONAL_NETWORK_DEGRADATION, affected_location="Delhi")
    regional_scenario, _ = inject_incident(baseline, regional, incident_seed=13)
    assert all(
        changed == original for original, changed in zip(baseline, regional_scenario) if original.location != "Delhi"
    )


def test_gateway_incident_affects_only_target_gateway_and_respects_time_window(baseline):
    incident = config(IncidentType.GATEWAY_DEGRADATION, affected_gateway="gateway_alpha")
    scenario, _ = inject_incident(baseline, incident, incident_seed=17)

    for original, changed in zip(baseline, scenario):
        if changed != original:
            assert changed.gateway == "gateway_alpha"
            assert incident.start_time <= changed.timestamp < incident.recovery_end_time


def test_ground_truth_reproducibility_multiple_incidents_and_metrics(baseline):
    first = config(IncidentType.BANK_UPI_TIMEOUT, affected_bank="HDFC Bank", affected_payment_method="UPI")
    second = config(IncidentType.GATEWAY_DEGRADATION, affected_gateway="gateway_beta")
    scenario_a, truth_a = inject_scenario(baseline, [first, second], incident_seed=23)
    scenario_b, truth_b = inject_scenario(baseline, [first, second], incident_seed=23)
    scenario_c, _ = inject_scenario(baseline, [first, second], incident_seed=24)

    assert scenario_a == scenario_b
    assert truth_a == truth_b
    assert scenario_a != scenario_c
    assert [truth.incident_id for truth in truth_a] == [first.incident_id, second.incident_id]
    assert truth_a[0].expected_root_cause == "BANK_TIMEOUT"
    metrics = calculate_incident_metrics(scenario_a, first)
    assert metrics.affected_transaction_count > 0
    assert metrics.failure_rate_during > metrics.failure_rate_before
    assert metrics.average_latency_during_ms > metrics.average_latency_before_ms
