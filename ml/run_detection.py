"""CLI: fit normal baseline, detect scenario anomalies, then optionally evaluate separately."""

import argparse
import json
from dataclasses import asdict
from decimal import Decimal

from ml.anomaly import detect_payment_incidents, fit_baseline
from ml.config import DetectionConfig
from ml.evaluation import evaluate_detection
from ml.evidence import generate_investigation_evidence
from ml.ground_truth_adapter import load_ground_truth
from simulator.loader import load_csv


def _serialise(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect payment anomalies without ground truth inference inputs.")
    parser.add_argument("--baseline", required=True, help="Normal baseline CSV.")
    parser.add_argument("--input", required=True, help="Scenario or candidate CSV.")
    parser.add_argument("--ground-truth", help="Optional evaluation-only JSON from Phase 2.")
    parser.add_argument("--output", help="Optional JSON report output path.")
    parser.add_argument("--window-minutes", type=int, default=5)
    args = parser.parse_args()
    config = DetectionConfig(window_minutes=args.window_minutes)
    baseline = list(load_csv(args.baseline))
    data = list(load_csv(args.input))
    model = fit_baseline(baseline, config)
    scores, incidents = detect_payment_incidents(data, model, config)
    evidence = [generate_investigation_evidence(data, incident) for incident in incidents]
    report = {
        "detected_incidents": [_serialise(asdict(incident)) for incident in incidents],
        "evidence": [_serialise(asdict(item)) for item in evidence],
        "anomalous_window_count": sum(score.is_anomalous for score in scores),
    }
    if args.ground_truth:
        report["evaluation"] = _serialise(asdict(evaluate_detection(incidents, load_ground_truth(args.ground_truth))))
    output = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output)
    print(output)


if __name__ == "__main__":
    main()
