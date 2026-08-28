"""High-level inference interface for the statistical detector."""

from typing import Iterable

from ml.baseline import BaselineModel, build_baseline
from ml.config import DetectionConfig
from ml.features import WindowFeatures, aggregate_transactions
from ml.incident_detection import DetectedIncident, detect_incidents
from ml.scoring import AnomalyScore, score_features
from simulator.schema import TransactionRecord


def calculate_anomaly_scores(
    data: Iterable[TransactionRecord], baseline: BaselineModel, config: DetectionConfig
) -> list[AnomalyScore]:
    return score_features(aggregate_transactions(data, config.window_minutes), baseline, config)


def fit_baseline(data: Iterable[TransactionRecord], config: DetectionConfig) -> BaselineModel:
    return build_baseline(aggregate_transactions(data, config.window_minutes), config)


def detect_payment_incidents(
    data: Iterable[TransactionRecord], baseline: BaselineModel, config: DetectionConfig
) -> tuple[list[AnomalyScore], list[DetectedIncident]]:
    scores = calculate_anomaly_scores(data, baseline, config)
    return scores, detect_incidents(scores, config)
