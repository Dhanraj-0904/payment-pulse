"""Ground-truth-only post-inference evaluation of incident detection."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from ml.ground_truth_adapter import EvaluationGroundTruth
from ml.incident_detection import DetectedIncident


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    incident_detection_rate: float
    mean_detection_latency_seconds: float | None
    true_positives: int
    false_positives: int
    false_negatives: int
    per_type: dict[str, dict[str, float]]


def _compatible(prediction: DetectedIncident, truth: EvaluationGroundTruth) -> bool:
    if prediction.end_time <= truth.start_time or prediction.start_time >= truth.end_time:
        return False
    dimension_segment = {
        "BANK": truth.affected_dimensions.get("bank"),
        "PAYMENT_METHOD": truth.affected_dimensions.get("payment_method"),
        "GATEWAY": truth.affected_dimensions.get("gateway"),
        "MERCHANT": truth.affected_dimensions.get("merchant"),
        "LOCATION": truth.affected_dimensions.get("location"),
    }
    if prediction.segment_level == "BANK_PAYMENT_METHOD":
        bank = truth.affected_dimensions.get("bank")
        method = truth.affected_dimensions.get("payment_method")
        return bool(bank and method and prediction.segment_key == f"{bank}|{method}")
    expected = dimension_segment.get(prediction.segment_level)
    return prediction.segment_level == "GLOBAL" or expected is None or prediction.segment_key == expected


def evaluate_detection(
    predictions: Iterable[DetectedIncident], ground_truth: Iterable[EvaluationGroundTruth]
) -> EvaluationReport:
    """Match overlapping, compatible detections to ground truth after inference is complete."""
    predictions, truth = list(predictions), list(ground_truth)
    matched_truth: set[int] = set()
    matched_predictions: set[int] = set()
    latencies: list[float] = []
    for prediction_index, prediction in enumerate(predictions):
        candidates = [index for index, item in enumerate(truth) if index not in matched_truth and _compatible(prediction, item)]
        if candidates:
            selected = min(candidates, key=lambda index: truth[index].start_time)
            matched_truth.add(selected)
            matched_predictions.add(prediction_index)
            latencies.append(max(0.0, (prediction.detected_at - truth[selected].start_time).total_seconds()))
    tp, fp, fn = len(matched_predictions), len(predictions) - len(matched_predictions), len(truth) - len(matched_truth)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    types: dict[str, dict[str, float]] = {}
    for incident_type in {item.incident_type for item in truth}:
        total = sum(item.incident_type == incident_type for item in truth)
        found = sum(truth[index].incident_type == incident_type for index in matched_truth)
        types[incident_type] = {"incident_detection_rate": found / total if total else 0.0, "count": float(total)}
    return EvaluationReport(
        precision=precision,
        recall=recall,
        f1=2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        false_positive_rate=fp / len(predictions) if predictions else 0.0,
        incident_detection_rate=recall,
        mean_detection_latency_seconds=sum(latencies) / len(latencies) if latencies else None,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        per_type=types,
    )
