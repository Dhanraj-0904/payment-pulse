# Incident Simulator

## Scope

The Phase 2 Incident Simulator deterministically transforms a preserved synthetic baseline dataset into a separate scenario dataset. It produces evaluation-only ground truth and descriptive metrics. It contains no ML, anomaly detection, root-cause classifier, LLM agent, recovery action, frontend, gateway integration, or real payment logic.

## Architecture

```text
baseline CSV + JSON scenario + incident seed
                 |
                 v
 scenario loader -> probabilistic injector -> scenario CSV
                 |                              |
                 +--> ground truth JSON          +--> metrics / chart utility
```

The original baseline records are immutable `TransactionRecord` values. Injection returns new records, so the baseline can be retained for later comparisons.

## Supported incident types

| Type | Target and induced behaviour | Ground-truth root cause |
| --- | --- | --- |
| `BANK_UPI_TIMEOUT` | Selected bank + UPI; higher latency and timeout failures | `BANK_TIMEOUT` |
| `GATEWAY_DEGRADATION` | Selected gateway across methods; network errors and latency | `GATEWAY_DEGRADATION` |
| `CARD_AUTH_FAILURE` | CARD, optionally narrowed by bank/gateway; auth failures | `CARD_AUTH_DEGRADATION` |
| `REGIONAL_NETWORK_DEGRADATION` | Selected location; network errors and latency | `REGIONAL_NETWORK_DEGRADATION` |
| `MERCHANT_SPECIFIC_FAILURE` | Selected merchant; higher failure probability | `MERCHANT_CONFIGURATION_FAILURE` |

Each configuration has start time, duration, severity, target dimensions, failure-rate multiplier, latency multiplier, affected transaction percentage, and recovery duration. During recovery, effects decay linearly instead of stopping abruptly.

## Scenario format

Use JSON to avoid a non-standard parsing dependency. `bank`, `payment_method`, `gateway`, `merchant`, and `location` are convenient aliases for the corresponding `affected_*` fields.

```json
{
  "incidents": [
    {
      "incident_id": "INC_001",
      "incident_type": "BANK_UPI_TIMEOUT",
      "start_time": "2026-01-01T00:15:00+00:00",
      "duration_minutes": 10,
      "recovery_minutes": 5,
      "severity": "HIGH",
      "bank": "HDFC Bank",
      "payment_method": "UPI",
      "failure_rate_multiplier": 8,
      "latency_multiplier": 4,
      "affected_transaction_percentage": 1.0
    }
  ]
}
```

An example is available at `simulator/scenarios/bank_upi_timeout.json`.

## Replay workflow

First write a baseline CSV:

```bash
backend/.venv/Scripts/python.exe -m simulator.generate --count 5000 --seed 42 --output-csv data/baseline.csv
```

Then inject a scenario without changing the baseline:

```bash
backend/.venv/Scripts/python.exe -m simulator.inject \
  --input data/baseline.csv \
  --scenario simulator/scenarios/bank_upi_timeout.json \
  --seed 42 \
  --output data/scenario.csv \
  --ground-truth data/ground_truth.json
```

The dataset seed (baseline generation), incident seed, and unchanged JSON scenario reproduce the same output. Multiple non-overlapping incidents may be listed in one scenario; injection is sequential so overlapping support can be extended later.

## Ground truth

Each record in the JSON ground-truth file contains the incident ID/type, start/end time, target dimensions, expected root cause, severity, complete injected parameters, and transaction count marked by that incident. This data is for later evaluation only and must not be passed to a future inference system.

## Metrics and visual inspection

The injection CLI prints descriptive matched-population metrics: before/during success and failure rates, latency averages and medians, affected count, failed amount, and an incremental failed-amount estimate. This estimate is descriptive only; it is not a revenue-at-risk model.

To create success-rate, failure-rate, and latency charts:

```bash
backend/.venv/Scripts/python.exe -m simulator.analyze_incidents --input data/scenario.csv --output data/incident_metrics.png
```

The chart utility requires `matplotlib`, listed in backend requirements.
