"""Tests for the ML revenue-at-risk estimation and evaluation engine."""

from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest

from simulator.schema import TransactionRecord
from ml.incident_detection import DetectedIncident
from ml.revenue import calculate_revenue_at_risk, calculate_revenue_for_incidents
from ml.revenue_evaluation import evaluate_revenue_estimate
from ml.ground_truth_adapter import EvaluationGroundTruth
from ml.financial_models import FinancialImpact

# Helper to build simple mock transaction records
def make_txn(tx_id: str, timestamp: datetime, amount: float, status: str, error_code: str | None = None, bank: str = "Axis Bank", payment_method: str = "UPI", gateway: str = "gateway_alpha", merchant_id: str = "merchant_retail_001", location: str = "Bengaluru", incident_id: str | None = None) -> TransactionRecord:
    return TransactionRecord(
        transaction_id=tx_id,
        timestamp=timestamp,
        merchant_id=merchant_id,
        amount=Decimal(str(amount)),
        currency="INR",
        payment_method=payment_method,
        bank=bank,
        gateway=gateway,
        device_type="WEB",
        network_type="4G",
        location=location,
        latency_ms=100 if status == "SUCCESS" else 5000,
        status=status,
        error_code=error_code,
        incident_id=incident_id
    )

@pytest.fixture
def base_time():
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

def test_revenue_calculations_manually_calculable_example(base_time):
    # Incident duration is 10 minutes: 12:10 to 12:20.
    # Baseline period is 12:00 to 12:10.
    # In baseline: 4 successes, 1 failure (20% failure rate)
    # In incident: 2 successes, 3 failures (60% failure rate)
    before_txs = [
        make_txn("t1", base_time + timedelta(minutes=1), 100.0, "SUCCESS"),
        make_txn("t2", base_time + timedelta(minutes=3), 200.0, "SUCCESS"),
        make_txn("t3", base_time + timedelta(minutes=5), 150.0, "FAILED", "TIMEOUT"),
        make_txn("t4", base_time + timedelta(minutes=7), 250.0, "SUCCESS"),
        make_txn("t5", base_time + timedelta(minutes=9), 300.0, "SUCCESS"),
    ]
    during_txs = [
        make_txn("t6", base_time + timedelta(minutes=11), 100.0, "SUCCESS"),
        make_txn("t7", base_time + timedelta(minutes=13), 200.0, "FAILED", "TIMEOUT"),
        make_txn("t8", base_time + timedelta(minutes=15), 150.0, "FAILED", "TIMEOUT"),
        make_txn("t9", base_time + timedelta(minutes=17), 250.0, "FAILED", "TIMEOUT"),
        make_txn("t10", base_time + timedelta(minutes=19), 300.0, "SUCCESS"),
    ]
    records = before_txs + during_txs
    
    # Expected values calculations:
    # Baseline success = 80% (0.80), failure = 20% (0.20)
    # Baseline average amount = (100+200+150+250+300)/5 = 200.00
    # Expected failures = 5 * 0.20 = 1.0
    # Actual failures = 3
    # Incremental failures = 3 - 1.0 = 2.0
    # Failure-based revenue-at-risk = 2.0 * 200.00 = 400.00
    
    # Value-based: 
    # incident_total = 100+200+150+250+300 = 1000.0
    # actual_success = 100+300 = 400.0
    # value-based = incident_total * baseline_success - actual_success = 1000 * 0.8 - 400 = 400.0
    
    mock_incident = DetectedIncident(
        incident_id="DET_0001",
        detected_at=base_time + timedelta(minutes=10),
        start_time=base_time + timedelta(minutes=10),
        end_time=base_time + timedelta(minutes=20),
        severity="HIGH",
        anomaly_score=0.8,
        confidence=0.9,
        status="RESOLVED",
        segment_level="GLOBAL",
        segment_key="ALL",
        transaction_count=5,
        monetary_volume=Decimal("1000.00"),
        scores=()
    )
    
    impact = calculate_revenue_at_risk(records, mock_incident, recoverability_rate=0.60)
    
    assert impact.baseline_transaction_count == 5
    assert impact.incident_transaction_count == 5
    assert impact.baseline_success_rate == 0.80
    assert impact.incident_success_rate == 0.40
    assert impact.baseline_average_transaction_amount == Decimal("200.00")
    assert impact.expected_failures == Decimal("1.0")
    assert impact.actual_failures == 3
    assert impact.incremental_failures == Decimal("2.0")
    assert impact.failure_based_revenue_at_risk == Decimal("400.00")
    assert impact.value_based_revenue_at_risk == Decimal("400.00")
    assert impact.revenue_at_risk == Decimal("400.00")
    assert impact.potentially_recoverable_revenue == Decimal("240.00") # 400 * 0.6
    
    # Verify confidence
    assert impact.confidence.level in {"HIGH", "MEDIUM", "LOW"}
    assert impact.confidence.baseline_sample_size == 5
    
    # Verify dimensional impact contains global attributes sorted
    assert len(impact.dimensional_impact) > 0
    
    # Verify time series contains two 5-minute windows
    assert len(impact.time_series_impact) == 2

def test_zero_transaction_edge_cases(base_time):
    mock_incident = DetectedIncident(
        incident_id="DET_0001",
        detected_at=base_time + timedelta(minutes=10),
        start_time=base_time + timedelta(minutes=10),
        end_time=base_time + timedelta(minutes=20),
        severity="HIGH",
        anomaly_score=0.8,
        confidence=0.9,
        status="RESOLVED",
        segment_level="GLOBAL",
        segment_key="ALL",
        transaction_count=0,
        monetary_volume=Decimal("0.00"),
        scores=()
    )
    
    impact = calculate_revenue_at_risk([], mock_incident)
    assert impact.incident_transaction_count == 0
    assert impact.revenue_at_risk == Decimal("0.00")
    assert impact.potentially_recoverable_revenue == Decimal("0.00")

def test_negative_or_invalid_amount_handling(base_time):
    before = [make_txn("t1", base_time + timedelta(minutes=1), -100.0, "SUCCESS")]
    during = [make_txn("t2", base_time + timedelta(minutes=11), -200.0, "FAILED")]
    
    mock_incident = DetectedIncident(
        incident_id="DET_0001",
        detected_at=base_time + timedelta(minutes=10),
        start_time=base_time + timedelta(minutes=10),
        end_time=base_time + timedelta(minutes=20),
        severity="HIGH",
        anomaly_score=0.8,
        confidence=0.9,
        status="RESOLVED",
        segment_level="GLOBAL",
        segment_key="ALL",
        transaction_count=1,
        monetary_volume=Decimal("-200.00"),
        scores=()
    )
    
    impact = calculate_revenue_at_risk(before + during, mock_incident)
    assert impact.revenue_at_risk >= Decimal("0.00")
    assert impact.potentially_recoverable_revenue >= Decimal("0.00")

def test_no_double_counting_behavior(base_time):
    mock_incident = DetectedIncident(
        incident_id="DET_0001",
        detected_at=base_time + timedelta(minutes=10),
        start_time=base_time + timedelta(minutes=10),
        end_time=base_time + timedelta(minutes=20),
        severity="HIGH",
        anomaly_score=0.8,
        confidence=0.9,
        status="RESOLVED",
        segment_level="GLOBAL",
        segment_key="ALL",
        transaction_count=0,
        monetary_volume=Decimal("0.00"),
        scores=()
    )
    
    impacts = calculate_revenue_for_incidents([], [mock_incident, mock_incident])
    assert len(impacts) == 1

def test_revenue_evaluation_against_ground_truth(base_time):
    baseline = [
        make_txn("t1", base_time + timedelta(minutes=11), 100.0, "SUCCESS")
    ]
    scenario = [
        make_txn("t1", base_time + timedelta(minutes=11), 100.0, "FAILED", "TIMEOUT", incident_id="INC_001")
    ]
    
    mock_impact = FinancialImpact(
        incident_id="INC_001",
        baseline_transaction_count=1,
        incident_transaction_count=1,
        baseline_success_rate=1.0,
        incident_success_rate=0.0,
        baseline_failure_rate=0.0,
        incident_failure_rate=1.0,
        baseline_average_transaction_amount=Decimal("100.00"),
        incident_average_transaction_amount=Decimal("100.00"),
        baseline_failed_amount=Decimal("0.00"),
        incident_failed_amount=Decimal("100.00"),
        expected_failures=Decimal("0.0"),
        actual_failures=1,
        incremental_failures=Decimal("1.0"),
        failure_based_revenue_at_risk=Decimal("120.00"),
        value_based_revenue_at_risk=Decimal("120.00"),
        revenue_at_risk=Decimal("120.00"),
        potentially_recoverable_revenue=Decimal("72.00"),
        recoverability_rate=Decimal("0.6"),
        confidence=None,
        dimensional_impact=(),
        time_series_impact=(),
        top_affected_segment=None
    )
    
    ground_truth = EvaluationGroundTruth(
        incident_id="INC_001",
        incident_type="BANK_UPI_TIMEOUT",
        start_time=base_time + timedelta(minutes=10),
        end_time=base_time + timedelta(minutes=20),
        affected_dimensions={}
    )
    
    evaluation = evaluate_revenue_estimate(baseline, scenario, mock_impact, ground_truth)
    assert evaluation.known_incremental_failed_amount == Decimal("100.00")
    assert evaluation.estimated_revenue_at_risk == Decimal("120.00")
    assert evaluation.estimation_error == Decimal("20.00")
    assert evaluation.absolute_percentage_error == pytest.approx(0.20)
