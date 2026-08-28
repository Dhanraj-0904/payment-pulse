import os
import csv
from decimal import Decimal
from datetime import datetime, timezone
from simulator.config import GeneratorConfig
from simulator.environment import StatefulSimulator
from simulator.simulator_adapter import SimulatorAdapter
from simulator.schema import TransactionRecord

_adapter = None

def load_baseline_records(filepath: str) -> list[TransactionRecord]:
    records = []
    with open(filepath, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            err_code = row.get("error_code")
            inc_id = row.get("incident_id")
            records.append(
                TransactionRecord(
                    transaction_id=row["transaction_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
                    amount=Decimal(row["amount"]),
                    currency=row["currency"],
                    payment_method=row["payment_method"],
                    bank=row["bank"],
                    gateway=row["gateway"],
                    merchant_id=row["merchant_id"],
                    status=row["status"],
                    error_code=err_code if err_code and err_code != "" else None,
                    latency_ms=int(row["latency_ms"]),
                    location=row["location"],
                    network_type=row["network_type"],
                    device_type=row["device_type"],
                    incident_id=inc_id if inc_id and inc_id != "" else None,
                )
            )
    return records

def get_simulator_adapter() -> SimulatorAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    config = GeneratorConfig(
        transaction_count=300,
        random_seed=42,
        start_timestamp=base_time,
        transaction_frequency_seconds=1
    )

    # Resolve baseline path from root
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    baseline_path = os.path.join(root_dir, "data", "baseline.csv")
    
    if os.path.exists(baseline_path):
        try:
            baseline_records = load_baseline_records(baseline_path)
        except Exception:
            from simulator.generator import generate_transactions
            baseline_records = list(generate_transactions(config))
    else:
        from simulator.generator import generate_transactions
        baseline_records = list(generate_transactions(config))

    # Instantiate simulator with empty incidents on startup
    simulator = StatefulSimulator(config, [], baseline_transactions=baseline_records)
    simulator.reset()
    simulator.step()  # Initial baseline window step

    _adapter = SimulatorAdapter(simulator)
    return _adapter
