"""CSV loading and normalization into the canonical transaction representation."""

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from simulator.schema import TransactionRecord


class DataLoadError(ValueError):
    """Raised when a supplied source row cannot be normalized."""


FIELD_ALIASES = {
    "transaction_id": ("transaction_id", "id"),
    "timestamp": ("timestamp", "occurred_at", "created_at"),
    "merchant_id": ("merchant_id", "merchant"),
    "amount": ("amount", "transaction_amount"),
    "currency": ("currency",),
    "payment_method": ("payment_method", "method"),
    "bank": ("bank", "issuer_bank"),
    "gateway": ("gateway", "payment_gateway"),
    "device_type": ("device_type", "device"),
    "network_type": ("network_type", "network"),
    "location": ("location", "city", "geography"),
    "latency_ms": ("latency_ms", "latency"),
    "status": ("status", "payment_status"),
    "error_code": ("error_code", "error"),
    "incident_id": ("incident_id",),
}


def _value(row: dict[str, str], field: str, default: str = "") -> str:
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] is not None:
            return row[alias].strip()
    return default


def _optional(value: str) -> str | None:
    return None if value.upper() in {"", "NULL", "NONE", "N/A"} else value


def normalize_row(row: dict[str, str], row_number: int) -> TransactionRecord:
    """Normalize one common CSV row shape; validation remains a separate concern."""
    try:
        timestamp = datetime.fromisoformat(_value(row, "timestamp").replace("Z", "+00:00"))
        amount = Decimal(_value(row, "amount"))
        latency_ms = int(_value(row, "latency_ms"))
    except (InvalidOperation, ValueError) as exc:
        raise DataLoadError(f"row {row_number}: invalid timestamp, amount, or latency value") from exc
    return TransactionRecord(
        transaction_id=_value(row, "transaction_id"),
        timestamp=timestamp,
        merchant_id=_value(row, "merchant_id"),
        amount=amount,
        currency=_value(row, "currency", "INR").upper(),
        payment_method=_value(row, "payment_method").upper(),
        bank=_value(row, "bank"),
        gateway=_value(row, "gateway"),
        device_type=_value(row, "device_type").upper(),
        network_type=_value(row, "network_type").upper(),
        location=_value(row, "location"),
        latency_ms=latency_ms,
        status=_value(row, "status").upper(),
        error_code=_optional(_value(row, "error_code")),
        incident_id=_optional(_value(row, "incident_id")),
    )


def load_csv(path: str | Path) -> Iterator[TransactionRecord]:
    """Load a supplied public or synthetic CSV source into canonical records."""
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise DataLoadError("CSV source must contain a header row")
        for row_number, row in enumerate(reader, start=2):
            yield normalize_row(row, row_number)
