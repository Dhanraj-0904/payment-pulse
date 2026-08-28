from datetime import datetime, timezone, timedelta

from ml.anomaly import detect_payment_incidents, fit_baseline
from ml.config import DetectionConfig
from ml.evaluation import evaluate_detection
from ml.evidence import generate_investigation_evidence
from ml.features import aggregate_transactions
from ml.ground_truth_adapter import EvaluationGroundTruth
from ml.incident_detection import assign_severity
from simulator.config import GeneratorConfig
from simulator.generator import generate_transactions
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.injector import inject_incident


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def data(count=9_000, seed=55):
    return list(generate_transactions(GeneratorConfig(count, seed, START, 1)))


def strong_incident():
    return IncidentConfig(
        incident_id="INC_ML_001",
        incident_type=IncidentType.BANK_UPI_TIMEOUT,
        start_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        duration_minutes=20,
        severity=Severity.HIGH,
        affected_bank="HDFC Bank",
        affected_payment_method="UPI",
        failure_rate_multiplier=10,
        latency_multiplier=4,
    )


def test_features_are_segmented_and_exclude_ground_truth_fields():
    records = data(600)
    features = aggregate_transactions(records)

    assert {"GLOBAL", "BANK", "PAYMENT_METHOD", "GATEWAY", "BANK_PAYMENT_METHOD", "MERCHANT", "LOCATION"}.issubset(
        {feature.segment_level for feature in features}
    )
    values = features[0].inference_values()
    assert "incident_id" not in values
    assert "expected_root_cause" not in values
    assert "incident_type" not in values


def test_normal_baseline_has_no_strong_detected_incident():
    records = data()
    config = DetectionConfig()
    _, incidents = detect_payment_incidents(records, fit_baseline(records, config), config)

    assert not incidents


def test_strong_incident_is_detected_with_evidence_and_reproducibility():
    baseline = data()
    config = DetectionConfig()
    scenario, truth = inject_incident(baseline, strong_incident(), incident_seed=8)
    scores, detections = detect_payment_incidents(scenario, fit_baseline(baseline, config), config)
    second_scores, second_detections = detect_payment_incidents(scenario, fit_baseline(baseline, config), config)

    assert scores == second_scores and detections == second_detections
    target = next(item for item in detections if item.segment_key == "HDFC Bank|UPI")
    assert target.anomaly_score >= config.anomaly_threshold
    assert target.severity in {"HIGH", "CRITICAL"}
    evidence = generate_investigation_evidence(scenario, target)
    assert evidence.likely_pattern == "BANK_UPI_TIMEOUT"
    report = evaluate_detection(
        detections,
        [EvaluationGroundTruth(truth.incident_id, truth.incident_type, truth.start_time, truth.end_time, truth.affected_dimensions)],
    )
    assert report.recall == 1.0


def test_severity_thresholds_are_explainable():
    assert assign_severity(0.90, 0.90, 100, 0) == "CRITICAL"
    assert assign_severity(0.72, 0.90, 100, 0) == "HIGH"
    assert assign_severity(0.60, 0.95, 20, 0) == "MEDIUM"
    assert assign_severity(0.20, 0.99, 20, 0) == "LOW"


def test_plot_detection_alignment_with_custom_window(tmp_path):
    from ml.visualize import plot_detection
    from ml.scoring import AnomalyScore
    from ml.features import aggregate_transactions
    records = data(100)
    features = aggregate_transactions(records, window_minutes=10)
    scores = [
        AnomalyScore(feature=f, score=0.1, contributions={}, zscores={}, is_anomalous=False)
        for f in features
    ]
    output_file = tmp_path / "test_plot.png"
    plot_detection(records, scores, [], output_file, window_minutes=10)
    assert output_file.exists()


def test_low_volume_incident_suppression_and_strong_incident_retention():
    from ml.incident_detection import detect_incidents
    from ml.scoring import AnomalyScore
    from ml.features import WindowFeatures
    from decimal import Decimal
    
    # 1. Low volume (10 transactions) single anomalous window -> should be suppressed
    low_vol_feature = WindowFeatures(
        window_start=START,
        window_end=START + timedelta(minutes=5),
        segment_level="BANK",
        segment_key="SBI",
        transaction_count=10,
        success_count=8,
        failure_count=2,
        success_rate=0.8,
        failure_rate=0.2,
        mean_latency=100.0,
        median_latency=100.0,
        p95_latency=100.0,
        p99_latency=100.0,
        total_amount=Decimal("100.0"),
        failed_amount=Decimal("20.0"),
        error_code_counts={},
        payment_method_counts={},
        bank_counts={},
        gateway_counts={},
        merchant_counts={},
        location_counts={}
    )
    
    # 2. Low volume (10 transactions) spanning two consecutive anomalous windows -> should trigger incident
    low_vol_feature_w1 = WindowFeatures(
        window_start=START,
        window_end=START + timedelta(minutes=5),
        segment_level="PAYMENT_METHOD",
        segment_key="UPI",
        transaction_count=10,
        success_count=8,
        failure_count=2,
        success_rate=0.8,
        failure_rate=0.2,
        mean_latency=100.0,
        median_latency=100.0,
        p95_latency=100.0,
        p99_latency=100.0,
        total_amount=Decimal("100.0"),
        failed_amount=Decimal("20.0"),
        error_code_counts={},
        payment_method_counts={},
        bank_counts={},
        gateway_counts={},
        merchant_counts={},
        location_counts={}
    )
    low_vol_feature_w2 = WindowFeatures(
        window_start=START + timedelta(minutes=5),
        window_end=START + timedelta(minutes=10),
        segment_level="PAYMENT_METHOD",
        segment_key="UPI",
        transaction_count=10,
        success_count=8,
        failure_count=2,
        success_rate=0.8,
        failure_rate=0.2,
        mean_latency=100.0,
        median_latency=100.0,
        p95_latency=100.0,
        p99_latency=100.0,
        total_amount=Decimal("100.0"),
        failed_amount=Decimal("20.0"),
        error_code_counts={},
        payment_method_counts={},
        bank_counts={},
        gateway_counts={},
        merchant_counts={},
        location_counts={}
    )
    
    scores = [
        AnomalyScore(feature=low_vol_feature, score=0.8, contributions={}, zscores={}, is_anomalous=True),
        AnomalyScore(feature=low_vol_feature_w1, score=0.8, contributions={}, zscores={}, is_anomalous=True),
        AnomalyScore(feature=low_vol_feature_w2, score=0.8, contributions={}, zscores={}, is_anomalous=True)
    ]
    
    config = DetectionConfig(low_volume_threshold=50, min_consecutive_windows_low_volume=2)
    detections = detect_incidents(scores, config)
    
    assert not any(d.segment_key == "SBI" for d in detections)
    assert any(d.segment_key == "UPI" for d in detections)


def test_empty_baseline_evidence_handling():
    from ml.evidence import generate_investigation_evidence
    from ml.incident_detection import DetectedIncident
    from simulator.schema import TransactionRecord
    from decimal import Decimal
    
    # Helper to build simple mock transaction records
    def make_txn(tx_id: str, timestamp: datetime, amount: float, status: str, error_code: str | None = None, bank: str = "Axis Bank", payment_method: str = "UPI") -> TransactionRecord:
        return TransactionRecord(
            transaction_id=tx_id,
            timestamp=timestamp,
            merchant_id="merchant_retail_001",
            amount=Decimal(str(amount)),
            currency="INR",
            payment_method=payment_method,
            bank=bank,
            gateway="gateway_alpha",
            device_type="WEB",
            network_type="4G",
            location="Bengaluru",
            latency_ms=100 if status == "SUCCESS" else 5000,
            status=status,
            error_code=error_code,
            incident_id=None
        )
    
    mock_incident = DetectedIncident(
        incident_id="DET_0001",
        detected_at=START,
        start_time=START,
        end_time=START + timedelta(minutes=5),
        severity="MEDIUM",
        anomaly_score=0.7,
        confidence=0.8,
        status="RESOLVED",
        segment_level="BANK_PAYMENT_METHOD",
        segment_key="HDFC Bank|UPI",
        transaction_count=2,
        monetary_volume=Decimal("300.0"),
        scores=()
    )
    
    records = [
        make_txn("t1", START + timedelta(seconds=10), 100.0, "FAILED", "TIMEOUT", bank="HDFC Bank", payment_method="UPI"),
        make_txn("t2", START + timedelta(seconds=30), 200.0, "FAILED", "TIMEOUT", bank="HDFC Bank", payment_method="UPI")
    ]
    
    evidence = generate_investigation_evidence(records, mock_incident)
    
    assert evidence.baseline_status == "INSUFFICIENT_DATA"
    assert evidence.evidence_quality == "POOR_INSUFFICIENT_BASELINE"
    
    for item in evidence.top_banks:
        assert item.baseline_metric is None
        assert item.delta is None
        assert item.percentage_change is None
    
    assert evidence.likely_pattern == "BANK_UPI_TIMEOUT"
