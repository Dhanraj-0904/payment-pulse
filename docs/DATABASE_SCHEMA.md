# Database Schema (Phase 0)

The schema is a small starting point for synthetic or public development data only. It contains no payment credentials, card data, or real payment-processing capability.

## Tables

### `transactions`

Represents a synthetic or public payment event used for future analysis.

| Column | Notes |
| --- | --- |
| `transaction_id` | External-style unique identifier. |
| `timestamp` | Timezone-aware event timestamp. |
| `merchant_id`, `payment_method`, `bank`, `gateway` | Payment dimensions for future segmentation. |
| `amount`, `currency` | Transaction value; Phase 1 generated data uses INR. |
| `device_type`, `network_type`, `location` | Additional operational context. |
| `status`, `latency_ms`, `error_code`, `incident_id` | Core payment-health attributes and future incident reference. |

### `incidents`

Represents a detected or future investigated reliability event.

| Column | Notes |
| --- | --- |
| `incident_id`, `incident_type` | Unique identifier and injected incident category. |
| `status`, `severity` | `ACTIVE`/`RESOLVED` lifecycle and importance. |
| `start_time`, `end_time` | Incident timing. |
| `affected_*` fields | Nullable bank, method, gateway, merchant, location, and error targets. |
| `expected_root_cause`, `injected_parameters` | Evaluation ground truth and replay details. |

### `agent_actions`

Stores proposed or future policy-controlled simulated actions. It has a many-to-one relationship with `incidents` through `incident_id`.

### `incident_outcomes`

Stores outcome measurements for an incident. It has a many-to-one relationship with `incidents` through `incident_id`.

## Relationship diagram

```text
incidents 1 ---- * agent_actions
incidents 1 ---- * incident_outcomes
```

`transactions` is currently independent. An incident-to-transaction relationship should be introduced only when the investigation workflow needs it.

## Evolution approach

Use Alembic migrations when the first deployable environment or schema migration is introduced. Until then, these SQLAlchemy definitions are the Phase 0 source of truth.
