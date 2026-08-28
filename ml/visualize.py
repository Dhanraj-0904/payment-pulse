"""Optional matplotlib visualization of health, score, and detected incident regions."""

from collections import defaultdict
from pathlib import Path

from ml.features import aggregate_transactions
from ml.incident_detection import DetectedIncident
from ml.scoring import AnomalyScore
from simulator.schema import TransactionRecord


def plot_detection(
    records: list[TransactionRecord], scores: list[AnomalyScore], incidents: list[DetectedIncident], output: str | Path, window_minutes: int = 5
) -> None:
    """Render success, latency, anomaly-score, and shaded detection intervals."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Install matplotlib to render Phase 3 charts") from exc
    global_features = [item for item in aggregate_transactions(records, window_minutes) if item.segment_level == "GLOBAL"]
    global_scores = {item.feature.window_start: item.score for item in scores if item.feature.segment_level == "GLOBAL"}
    times = [item.window_start for item in global_features]
    figure, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    axes[0].plot(times, [item.success_rate for item in global_features], color="green"); axes[0].set_ylabel("Success rate")
    axes[1].plot(times, [item.mean_latency for item in global_features], color="darkorange"); axes[1].set_ylabel("Mean latency (ms)")
    axes[2].plot(times, [global_scores.get(time, 0.0) for time in times], color="crimson"); axes[2].set_ylabel("Anomaly score")
    axes[2].set_xlabel("Time")
    for axis in axes:
        for incident in incidents:
            axis.axvspan(incident.start_time, incident.end_time, color="red", alpha=0.12)
    figure.suptitle("Payment Pulse: normal -> anomaly -> detection -> recovery")
    figure.tight_layout()
    figure.savefig(output, dpi=160)
