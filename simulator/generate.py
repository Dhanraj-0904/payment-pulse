"""CLI for generating, validating, and optionally bulk-ingesting baseline data."""

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from simulator.config import GeneratorConfig
from simulator.generator import generate_transactions
from simulator.ingestion import ingest_transactions
from simulator.loader import load_csv
from simulator.data_io import write_transactions_csv
from simulator.validation import validate_transactions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Payment Pulse baseline transactions.")
    parser.add_argument("--count", type=int, help="Transaction count; overrides TRANSACTION_COUNT.")
    parser.add_argument("--seed", type=int, help="Random seed; overrides RANDOM_SEED.")
    parser.add_argument("--start-timestamp", help="ISO-8601 timestamp; overrides START_TIMESTAMP.")
    parser.add_argument("--frequency-seconds", type=int, help="Cadence; overrides TRANSACTION_FREQUENCY_SECONDS.")
    parser.add_argument("--ingest", action="store_true", help="Bulk insert into DATABASE_URL after validation.")
    parser.add_argument("--input-csv", help="Load and normalize a supplied public or synthetic CSV instead.")
    parser.add_argument("--output-csv", help="Write generated or normalized records to a CSV file.")
    parser.add_argument("--batch-size", type=int, default=5_000, help="Rows per insert batch.")
    return parser


def main() -> None:
    args = _parser().parse_args()
    base = GeneratorConfig.from_environment()
    start_timestamp = base.start_timestamp
    if args.start_timestamp:
        from datetime import datetime

        start_timestamp = datetime.fromisoformat(args.start_timestamp.replace("Z", "+00:00"))
    config = GeneratorConfig(
        transaction_count=args.count if args.count is not None else base.transaction_count,
        random_seed=args.seed if args.seed is not None else base.random_seed,
        start_timestamp=start_timestamp,
        transaction_frequency_seconds=(
            args.frequency_seconds
            if args.frequency_seconds is not None
            else base.transaction_frequency_seconds
        ),
    )
    records = list(load_csv(args.input_csv)) if args.input_csv else list(generate_transactions(config))
    validate_transactions(records)
    if args.output_csv:
        write_transactions_csv(records, args.output_csv)
    if args.ingest:
        from app.db.session import engine

        inserted = ingest_transactions(records, engine, batch_size=args.batch_size, create_schema=True)
    else:
        inserted = 0
    methods = Counter(record.payment_method for record in records)
    statuses = Counter(record.status for record in records)
    print(
        f"{'Loaded' if args.input_csv else 'Generated'} and validated {len(records)} transactions; "
        f"ingested={inserted}; methods={dict(methods)}; statuses={dict(statuses)}"
    )


if __name__ == "__main__":
    main()
