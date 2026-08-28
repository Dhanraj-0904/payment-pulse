"""Bulk database ingestion for validated canonical transaction records."""

from itertools import islice
from typing import Iterable

from simulator.schema import TransactionRecord
from simulator.validation import validate_transactions


def _batches(records: Iterable[TransactionRecord], batch_size: int):
    iterator = iter(records)
    while batch := list(islice(iterator, batch_size)):
        yield batch


def ingest_transactions(records: Iterable[TransactionRecord], engine, batch_size: int = 5_000, create_schema: bool = False) -> int:
    """Validate and insert records in batches, returning the inserted row count."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    from app.db.base import Base
    from app.models.transaction import Transaction

    if create_schema:
        Base.metadata.create_all(bind=engine)
    inserted = 0
    seen_ids: set[str] = set()
    for batch in _batches(records, batch_size):
        validate_transactions(batch, seen_ids=seen_ids)
        with engine.begin() as connection:
            connection.execute(Transaction.__table__.insert(), [record.to_mapping() for record in batch])
        inserted += len(batch)
    return inserted
