# Payment Data Engine

## Purpose and scope

The Payment Data Engine creates reproducible, synthetic normal-baseline payment data for development. It is independent of machine learning, AI-agent behaviour, incident simulation, recovery actions, frontend code, gateway integrations, and real payment processing.

## Canonical transaction schema

Every record contains the following fields:

| Field | Description |
| --- | --- |
| `transaction_id` | Deterministic unique identifier. |
| `timestamp` | Timezone-aware transaction time. |
| `merchant_id`, `bank`, `gateway` | Payment participant dimensions. |
| `amount`, `currency` | Transaction value; generated currency is `INR`. |
| `payment_method` | `UPI`, `CARD`, `NETBANKING`, or `WALLET`. |
| `device_type`, `network_type`, `location` | Context dimensions. |
| `latency_ms`, `status`, `error_code` | Payment health measures. |
| `incident_id` | Empty for Phase 1 baseline data; reserved for future incident injection. |

The PostgreSQL `transactions` table follows this representation. The existing initial table was extended only to include the required canonical fields.

## Generator design

`simulator.generator.generate_transactions()` yields `TransactionRecord` objects from a seeded `random.Random` instance. It combines baseline profiles for banks, methods, gateways, and merchants instead of choosing independent columns.

- Payment-method mix and amount distributions differ; travel merchants have higher typical values.
- Banks, gateways, methods, and merchants have small stable success-rate and latency variations.
- Successful transactions normally have lower latency.
- `TIMEOUT` records have high latency (at least 5 seconds); network errors are also slower.
- Failure codes are emitted only for failed records. `CARD_DECLINED` is only emitted for card transactions.
- Each record has `incident_id=None`, preserving a clean baseline that a later incident simulator can alter by any dimension.

## Configuration

Values are loaded from environment variables and can be overridden by CLI options.

| Variable | Purpose |
| --- | --- |
| `TRANSACTION_COUNT` | Number of transactions; default configuration is 500,000. |
| `RANDOM_SEED` | Deterministic generator seed. |
| `START_TIMESTAMP` | ISO-8601, offset-aware start time. |
| `TRANSACTION_FREQUENCY_SECONDS` | Seconds between generated transaction timestamps. |
| `DATABASE_URL` | SQLAlchemy PostgreSQL connection string used only with `--ingest`. |

## Generate and validate

From the repository root, use the installed backend environment:

```bash
backend/.venv/Scripts/python.exe -m simulator.generate --count 500000 --seed 42
```

The command generates and validates the data in memory, then prints method and status summaries. A seed, start time, and frequency produce the same sequence every run.

## Load a supplied dataset

To load a public or synthetic CSV, pass `--input-csv`. The loader accepts canonical headers and common aliases such as `id`, `occurred_at`, `merchant`, `transaction_amount`, `method`, `issuer_bank`, and `payment_gateway`; it normalizes them before the normal validation step.

```bash
backend/.venv/Scripts/python.exe -m simulator.generate --input-csv data/source.csv
```

Add `--ingest` to validate and insert the normalized source using the same batched ingestion path.

## Bulk ingestion

After PostgreSQL is running and `DATABASE_URL` is configured, add `--ingest`:

```bash
backend/.venv/Scripts/python.exe -m simulator.generate --count 500000 --seed 42 --ingest
```

The ingestion module validates each batch before using SQLAlchemy Core executemany inserts. The default batch size is 5,000 and is configurable with `--batch-size`. This avoids row-by-row insertion while retaining clear validation failures.

## Validation

`validate_transactions()` checks required/non-empty fields, uniqueness of transaction IDs, timezone-aware timestamps, valid enums, non-negative amounts and latency, status/error consistency, and payment-method/error compatibility. It raises `DataValidationError` with the row number and reason at the first invalid row.

## Assumptions

- Data is fully synthetic and denominated in INR.
- Baseline profiles are intentionally stable, not intended to model a live processor exactly.
- Incident behaviour is intentionally absent. The schema and profile dimensions are designed for a later injection phase.
