"""Group consecutive anomalous windows into interpretable detected incidents."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from ml.scoring import AnomalyScore


@dataclass(frozen=True, slots=True)
class DetectedIncident:
    incident_id: str
    detected_at: datetime
    start_time: datetime
    end_time: datetime
    severity: str
    anomaly_score: float
    confidence: float
    status: str
    segment_level: str
    segment_key: str
    transaction_count: int
    monetary_volume: Decimal
    scores: tuple[AnomalyScore, ...]


def assign_severity(score: float, success_rate: float, transaction_count: int, amount: Decimal) -> str:
    """Prototype thresholds, intentionally not production calibrated."""
    if score >= 0.85 or (success_rate < 0.70 and transaction_count >= 50):
        return "CRITICAL"
    if score >= 0.70 or (success_rate < 0.82 and transaction_count >= 30):
        return "HIGH"
    if score >= 0.55:
        return "MEDIUM"
    return "LOW"


def group_anomalies(scores: Iterable[AnomalyScore], config=None) -> list[DetectedIncident]:
    """Group adjacent anomalous windows of the same segment, avoiding alert-per-window output."""
    if config is None:
        from ml.config import DetectionConfig
        config = DetectionConfig()
    anomalous = sorted(
        (score for score in scores if score.is_anomalous),
        key=lambda score: (score.feature.segment_level, score.feature.segment_key, score.feature.window_start),
    )
    groups: list[list[AnomalyScore]] = []
    for score in anomalous:
        if groups:
            previous = groups[-1][-1]
            if (
                previous.feature.segment_level == score.feature.segment_level
                and previous.feature.segment_key == score.feature.segment_key
                # Recovery can briefly dip below the score threshold; allow one
                # missing window so a single degradation does not split in two.
                and score.feature.window_start <= previous.feature.window_end
                + (previous.feature.window_end - previous.feature.window_start)
            ):
                groups[-1].append(score)
                continue
        groups.append([score])
    candidates: list[tuple[list[AnomalyScore], AnomalyScore, int, Decimal]] = []
    for group in groups:
        first, last = group[0], group[-1]
        peak = max(group, key=lambda score: score.score)
        
        # Check low-volume constraint:
        # If the average transactions per window for this segment in this group is low,
        # require consecutive anomalous windows.
        avg_transactions = sum(item.feature.transaction_count for item in group) / len(group)
        if avg_transactions < config.low_volume_threshold:
            if len(group) < config.min_consecutive_windows_low_volume:
                continue # Suppress noise
                
        transaction_count = sum(item.feature.transaction_count for item in group)
        volume = sum((item.feature.total_amount for item in group), Decimal("0"))
        candidates.append((group, peak, transaction_count, volume))
    # Prefer a specific bank+method alert over its duplicate bank or method roll-up.
    retained = []
    for candidate in candidates:
        group, peak, _, _ = candidate
        feature = group[0].feature
        duplicate_rollup = any(
            other[0][0].feature.segment_level == "BANK_PAYMENT_METHOD"
            and not (other[0][-1].feature.window_end <= feature.window_start or other[0][0].feature.window_start >= feature.window_end)
            and (
                (feature.segment_level == "BANK" and other[0][0].feature.segment_key.split("|")[0] == feature.segment_key)
                or (feature.segment_level == "PAYMENT_METHOD" and other[0][0].feature.segment_key.split("|")[1] == feature.segment_key)
            )
            for other in candidates
        )
        if not duplicate_rollup:
            retained.append(candidate)
    incidents: list[DetectedIncident] = []
    for number, (group, peak, transaction_count, volume) in enumerate(retained, start=1):
        first, last = group[0], group[-1]
        incidents.append(
            DetectedIncident(
                incident_id=f"DET_{number:04d}",
                detected_at=first.feature.window_start,
                start_time=first.feature.window_start,
                end_time=last.feature.window_end,
                severity=assign_severity(peak.score, min(item.feature.success_rate for item in group), transaction_count, volume),
                anomaly_score=peak.score,
                confidence=min(0.99, round(0.45 + peak.score * 0.55, 2)),
                status="RESOLVED",
                segment_level=first.feature.segment_level,
                segment_key=first.feature.segment_key,
                transaction_count=transaction_count,
                monetary_volume=volume,
                scores=tuple(group),
            )
        )
    return incidents


def detect_incidents(scores: Iterable[AnomalyScore], config=None) -> list[DetectedIncident]:
    return group_anomalies(scores, config)
