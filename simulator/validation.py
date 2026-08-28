"""Data-quality validation for normalized payment transaction records."""

from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from simulator.schema import ERROR_CODES, METHOD_ERRORS, PAYMENT_METHODS, STATUSES, TransactionRecord

REQUIRED_FIELDS = set(TransactionRecord.__dataclass_fields__)


class DataValidationError(ValueError):
    """Raised when a transaction violates the canonical data contract."""


def _mapping(record: TransactionRecord | Mapping[str, object]) -> Mapping[str, object]:
    return asdict(record) if is_dataclass(record) else record


def validate_transactions(records: Iterable[TransactionRecord | Mapping[str, object]], seen_ids: set[str] | None = None) -> int:
    """Validate records and return their count; raise on the first clear violation."""
    if seen_ids is None:
        seen_ids = set()
    count = 0
    for row_number, raw_record in enumerate(records, start=1):
        record = _mapping(raw_record)
        missing = REQUIRED_FIELDS - set(record)
        if missing:
            raise DataValidationError(f"row {row_number}: missing required fields {sorted(missing)}")
        for field in (
            "merchant_id",
            "currency",
            "bank",
            "gateway",
            "device_type",
            "network_type",
            "location",
        ):
            if not isinstance(record[field], str) or not record[field].strip():
                raise DataValidationError(f"row {row_number}: {field} must be a non-empty string")
        transaction_id = record["transaction_id"]
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise DataValidationError(f"row {row_number}: transaction_id must be a non-empty string")
        if transaction_id in seen_ids:
            raise DataValidationError(f"row {row_number}: duplicate transaction_id {transaction_id}")
        seen_ids.add(transaction_id)
        timestamp = record["timestamp"]
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise DataValidationError(f"row {row_number}: timestamp must be timezone-aware datetime")
        try:
            if Decimal(str(record["amount"])) < 0:
                raise DataValidationError(f"row {row_number}: amount cannot be negative")
        except (InvalidOperation, ValueError) as exc:
            raise DataValidationError(f"row {row_number}: amount must be numeric") from exc
        if record["payment_method"] not in PAYMENT_METHODS:
            raise DataValidationError(f"row {row_number}: invalid payment_method")
        if record["currency"] != "INR":
            raise DataValidationError(f"row {row_number}: invalid currency")
        if record["status"] not in STATUSES:
            raise DataValidationError(f"row {row_number}: invalid status")
        latency = record["latency_ms"]
        if not isinstance(latency, int) or latency < 0:
            raise DataValidationError(f"row {row_number}: latency_ms must be a non-negative integer")
        error_code = record["error_code"]
        if record["status"] == "SUCCESS" and error_code is not None:
            raise DataValidationError(f"row {row_number}: successful transaction cannot have error_code")
        if record["status"] == "FAILED" and error_code not in ERROR_CODES:
            raise DataValidationError(f"row {row_number}: failed transaction requires a valid error_code")
        if error_code is not None and error_code not in METHOD_ERRORS[record["payment_method"]]:
            raise DataValidationError(
                f"row {row_number}: {error_code} is invalid for {record['payment_method']}"
            )
        count += 1
    return count
