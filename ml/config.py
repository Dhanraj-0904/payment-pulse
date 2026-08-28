"""Configuration for feature aggregation and prototype detection thresholds."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    window_minutes: int = 5
    min_transactions_per_window: int = 20
    anomaly_threshold: float = 0.55
    zscore_threshold: float = 2.5
    min_baseline_std_success_rate: float = 0.01
    min_baseline_std_latency_ms: float = 75.0
    min_baseline_std_error_rate: float = 0.005
    min_baseline_samples: int = 5
    low_volume_threshold: int = 50
    min_consecutive_windows_low_volume: int = 2

    def __post_init__(self) -> None:
        if self.window_minutes < 1 or self.min_transactions_per_window < 1:
            raise ValueError("window_minutes and min_transactions_per_window must be positive")
