# Payment Pulse Architecture

## Phase 0 scope

Payment Pulse is an AI Payment Reliability & Revenue Protection Agent. Phase 0 establishes a backend and data foundation only. It does not contain anomaly detection, root-cause models, an LLM agent, a UI, recovery orchestration, or any real payment integration.

```text
Future synthetic/public data
          |
          v
  FastAPI backend ----> PostgreSQL
          |
          v
       GET /health
```

## Components

| Component | Responsibility in Phase 0 |
| --- | --- |
| `backend/app/api` | HTTP route modules, starting with health status. |
| `backend/app/core` | Environment-based configuration. |
| `backend/app/db` | SQLAlchemy engine, session lifecycle, and connectivity check. |
| `backend/app/models` | Initial persistence entities. |
| PostgreSQL | Local/containerized durable store. |
| Docker Compose | Starts the database and API together. |

## Design decisions

- Configuration is loaded from environment variables; `.env` is local-only and excluded from Git.
- The backend checks the database with `SELECT 1`; it returns no credential or connection-string data.
- SQLAlchemy models are intentionally lean. Schema migrations and feature-specific constraints belong to later phases.
- Tests use in-memory SQLite to verify the API and metadata without needing a local PostgreSQL server. Docker Compose is the PostgreSQL integration path.

## Future boundaries

Future data ingestion, anomaly detection, incident investigation, policy checks, recovery simulation, and frontend work should use typed interfaces around this API and database layer. No component may process real payments or move real money.
