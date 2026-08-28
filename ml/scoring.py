"""Deterministic, explainable z-score anomaly scoring."""

from dataclasses import dataclass
from typing import Iterable

from ml.baseline import BaselineModel
from ml.config import DetectionConfig
from ml.features import WindowFeatures


@dataclass(frozen=True, slots=True)
class AnomalyScore:
    feature: WindowFeatures
    score: float
    contributions: dict[str, float]
    zscores: dict[str, float]
    is_anomalous: bool


WEIGHTS = {
    "success_rate_degradation": 0.35,
    "failure_rate_increase": 0.20,
    "latency_increase": 0.25,
    "timeout_spike": 0.12,
    "network_error_spike": 0.05,
    "auth_failed_spike": 0.03,
}


def _positive_z(value: float, baseline_mean: float, baseline_std: float) -> float:
    return max(0.0, (value - baseline_mean) / baseline_std)


def score_features(
    features: Iterable[WindowFeatures], baseline: BaselineModel, config: DetectionConfig
) -> list[AnomalyScore]:
    """Score windows using only aggregated operational data and normal baselines."""
    results: list[AnomalyScore] = []
    for feature in features:
        if feature.transaction_count < config.min_transactions_per_window:
            continue
        metrics = baseline.metrics_for(feature)
        if not metrics:
            continue
        signals = {
            "success_rate_degradation": _positive_z(
                metrics["success_rate"].historical_mean, feature.success_rate, metrics["success_rate"].historical_std
            ),
            "failure_rate_increase": _positive_z(
                feature.failure_rate, metrics["failure_rate"].historical_mean, metrics["failure_rate"].historical_std
            ),
            "latency_increase": _positive_z(
                feature.mean_latency, metrics["mean_latency"].historical_mean, metrics["mean_latency"].historical_std
            ),
            "timeout_spike": _positive_z(
                feature.timeout_rate, metrics["timeout_rate"].historical_mean, metrics["timeout_rate"].historical_std
            ),
            "network_error_spike": _positive_z(
                feature.network_error_rate,
                metrics["network_error_rate"].historical_mean,
                metrics["network_error_rate"].historical_std,
            ),
            "auth_failed_spike": _positive_z(
                feature.auth_failed_rate,
                metrics["auth_failed_rate"].historical_mean,
                metrics["auth_failed_rate"].historical_std,
            ),
        }
        contributions = {
            name: round(weight * min(1.0, zscore / config.zscore_threshold), 4)
            for name, (weight, zscore) in ((name, (WEIGHTS[name], value)) for name, value in signals.items())
        }
        score = round(sum(contributions.values()), 4)
        # A score alone can be exaggerated by a low-rate error in a small segment.
        # Require a material operational change as a second, interpretable guardrail.
        material_degradation = (
            feature.failure_rate >= 0.15
            or feature.mean_latency >= 1_800
            or feature.timeout_rate >= 0.12
            or feature.network_error_rate >= 0.12
            or feature.auth_failed_rate >= 0.12
        )
        results.append(
            AnomalyScore(
                feature=feature,
                score=score,
                contributions=contributions,
                zscores={name: round(value, 4) for name, value in signals.items()},
                is_anomalous=score >= config.anomaly_threshold and material_degradation,
            )
        )
    return results

