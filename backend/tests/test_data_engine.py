from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import engine
from app.models.transaction import Transaction
from simulator.config import GeneratorConfig
from simulator.generator import generate_transactions
from simulator.ingestion import ingest_transactions
from simulator.loader import normalize_row
from simulator.schema import TransactionRecord
from simulator.validation import DataValidationError, validate_transactions


def config(count: int = 20, seed: int = 42) -> GeneratorConfig:
    return GeneratorConfig(
        transaction_count=count,
        random_seed=seed,
        start_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        transaction_frequency_seconds=1,
    )


def test_generator_produces_requested_number_of_transactions():
    records = list(generate_transactions(config(count=37)))

    assert len(records) == 37


def test_same_seed_produces_reproducible_output():
    first = list(generate_transactions(config(seed=7)))
    second = list(generate_transactions(config(seed=7)))

    assert first == second


def test_different_seed_produces_different_output():
    assert list(generate_transactions(config(seed=7))) != list(generate_transactions(config(seed=8)))


def test_generated_records_have_required_fields_and_pass_validation():
    records = list(generate_transactions(config()))

    assert set(TransactionRecord.__dataclass_fields__).issubset(records[0].to_mapping())
    assert validate_transactions(records) == len(records)


def test_invalid_data_is_rejected():
    record = next(generate_transactions(config(count=1)))

    with pytest.raises(DataValidationError, match="successful transaction cannot have error_code"):
        validate_transactions([replace(record, status="SUCCESS", error_code="TIMEOUT")])


def test_generated_error_status_and_method_relationships_are_valid():
    records = list(generate_transactions(config(count=250)))

    for record in records:
        assert (record.status == "SUCCESS") == (record.error_code is None)
        if record.error_code == "CARD_DECLINED":
            assert record.payment_method == "CARD"
    assert validate_transactions(records) == 250


def test_bulk_ingestion_works_for_small_dataset():
    records = list(generate_transactions(config(count=12)))

    assert ingest_transactions(records, engine, batch_size=5) == 12
    with engine.connect() as connection:
        count = connection.scalar(select(func.count()).select_from(Transaction))
    assert count == 12


def test_loader_normalizes_common_source_aliases():
    record = normalize_row(
        {
            "id": "source-1",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "merchant": "merchant_retail_001",
            "transaction_amount": "99.50",
            "method": "upi",
            "issuer_bank": "HDFC Bank",
            "payment_gateway": "gateway_alpha",
            "device": "android",
            "network": "5g",
            "city": "Delhi",
            "latency": "500",
            "payment_status": "success",
            "error": "",
        },
        row_number=2,
    )

    assert record.transaction_id == "source-1"
    assert record.payment_method == "UPI"
    assert validate_transactions([record]) == 1


def test_cross_batch_duplicate_transaction_ids():
    record1 = next(generate_transactions(config(count=1, seed=10)))
    record2 = next(generate_transactions(config(count=1, seed=11)))
    record2 = replace(record2, transaction_id=record1.transaction_id)

    with pytest.raises(DataValidationError, match="duplicate transaction_id"):
        ingest_transactions([record1, record2], engine, batch_size=1)
