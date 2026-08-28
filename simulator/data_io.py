"""Portable CSV and JSON serialization for baseline/scenario preservation."""

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from simulator.ground_truth import IncidentGroundTruth
from simulator.schema import TransactionRecord


def write_transactions_csv(records: Iterable[TransactionRecord], path: str | Path) -> None:
    records = list(records)
    if not records:
        raise ValueError("cannot write an empty transaction dataset")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0].to_mapping()))
        writer.writeheader()
        for record in records:
            row = record.to_mapping()
            row["timestamp"] = record.timestamp.isoformat()
            row["amount"] = str(record.amount)
            writer.writerow(row)


def write_ground_truth(ground_truth: Iterable[IncidentGroundTruth], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        json.dump([record.to_mapping() for record in ground_truth], stream, indent=2)
