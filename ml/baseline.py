"""Historical and rolling baseline statistics from normal-only feature windows."""

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Iterable

from ml.config import DetectionConfig
from ml.features import WindowFeatures


@dataclass(frozen=True, slots=True)
class MetricBaseline:
    historical_mean: float
    historical_std: float
    rolling_mean: float
    rolling_std: float


@dataclass(frozen=True, slots=True)
class BaselineModel:
    by_segment: dict[tuple[str, str], dict[str, MetricBaseline]]

    def metrics_for(self, feature: WindowFeatures) -> dict[str, MetricBaseline] | None:
        return self.by_segment.get((feature.segment_level, feature.segment_key))


def build_baseline(features: Iterable[WindowFeatures], config: DetectionConfig) -> BaselineModel:
    """Fit historical and trailing-window statistics using normal feature windows only."""
    grouped: dict[tuple[str, str], list[WindowFeatures]] = defaultdict(list)
    for feature in features:
        if feature.transaction_count >= config.min_transactions_per_window:
            grouped[(feature.segment_level, feature.segment_key)].append(feature)
    result: dict[tuple[str, str], dict[str, MetricBaseline]] = {}
    for key, rows in grouped.items():
        if len(rows) < config.min_baseline_samples:
            continue
        values: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            for metric, value in row.inference_values().items():
                values[metric].append(value)
        metrics: dict[str, MetricBaseline] = {}
        for metric, samples in values.items():
            rolling = samples[-min(6, len(samples)):]
            floor = {
                "success_rate": config.min_baseline_std_success_rate,
                "failure_rate": config.min_baseline_std_success_rate,
                "timeout_rate": config.min_baseline_std_error_rate,
                "network_error_rate": config.min_baseline_std_error_rate,
                "auth_failed_rate": config.min_baseline_std_error_rate,
                "mean_latency": config.min_baseline_std_latency_ms,
            }.get(metric, 1.0)
            metrics[metric] = MetricBaseline(
                historical_mean=mean(samples),
                historical_std=max(stdev(samples), floor),
                rolling_mean=mean(rolling),
                rolling_std=max(stdev(rolling) if len(rolling) > 1 else 0.0, floor),
            )
        result[key] = metrics
    return BaselineModel(result)
