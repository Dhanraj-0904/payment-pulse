"""CLI for deterministic incident injection from a baseline CSV and JSON scenario."""

import argparse
import json
from dataclasses import asdict
from decimal import Decimal

from simulator.data_io import write_ground_truth, write_transactions_csv
from simulator.injector import inject_scenario
from simulator.loader import load_csv
from simulator.metrics import calculate_incident_metrics
from simulator.scenarios import load_scenario
from simulator.validation import validate_transactions


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject Payment Pulse incidents into a baseline dataset.")
    parser.add_argument("--input", required=True, help="Baseline CSV input path.")
    parser.add_argument("--scenario", required=True, help="JSON scenario configuration path.")
    parser.add_argument("--seed", type=int, required=True, help="Deterministic incident seed.")
    parser.add_argument("--output", required=True, help="Scenario CSV output path.")
    parser.add_argument("--ground-truth", required=True, help="Ground-truth JSON output path.")
    args = parser.parse_args()

    baseline = list(load_csv(args.input))
    validate_transactions(baseline)
    configs = load_scenario(args.scenario)
    scenario_records, ground_truth = inject_scenario(baseline, configs, args.seed)
    validate_transactions(scenario_records)
    write_transactions_csv(scenario_records, args.output)
    write_ground_truth(ground_truth, args.ground_truth)
    metrics = [asdict(calculate_incident_metrics(scenario_records, config)) for config in configs]
    for metric in metrics:
        for key, value in metric.items():
            if isinstance(value, Decimal):
                metric[key] = str(value)
    print(json.dumps({"transactions": len(scenario_records), "incident_metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
