# Payment Pulse: SRE Payment Reliability Sandbox

Payment Pulse is a payment reliability and revenue protection agent sandbox designed to detect infrastructure degradations, estimate financial impact, and execute automated recoveries under policy control.

---

## System Architecture & Design Decisions

### Why Rolling Z-Score Over Isolation Forest
When building the anomaly detection engine (`ml/`), we evaluated unsupervised clustering models like Isolation Forests but ultimately rejected them in favor of a rolling dimensional Z-score detector. In high-frequency payment networks, failures manifest as sudden step-changes in dimensional failure rates (e.g., a specific gateway returning 5xx codes). Isolation Forests are highly capable for offline batch clustering in high-dimensional spaces, but they introduce non-trivial computational overhead, lack temporal responsiveness, and function as black boxes. By using rolling Z-scores calculated over sliding time windows, we gain instant detection latency, low compute cost, and direct mathematical interpretability. SRE operators can instantly trace an anomaly score back to a specific standard deviation threshold violation.

### Process Separation: Checkout Portal vs. SRE Console
The sandbox split into two separate applications—a Customer Portal (`payment_portal/` on port 8010) and an SRE Operations Console (`ops_dashboard/` on port 8000)—mirrors real-world decoupled microservices. Running checkout processing and monitoring inside a single monolith would obscure network latencies, database connection pool contentions, and resource starvation issues. Decoupling the applications forces the Operations Console to consume payment updates asynchronously over a WebSocket event bus. This ensures the dashboard operates under realistic network conditions and handles real-time streams without blocking client checkout flows.

### Decoupling Simulations (Decisions Replaced)
During the design of the SRE recovery loop, we originally tried executing dry-run evaluations directly inside the primary simulator loop. This approach was rejected because running forward projections corrupted the active transaction states and messed up the rolling transaction histories. We resolved this by building a dedicated counterfactual engine (`simulator/counterfactual.py`) that clones the active RNG and transaction-generator states at the exact decision point, performing isolated replications in separate threads to evaluate recovery outcomes without bleeding into live production traffic.

---

## Sandbox Run Instructions

### 1. Backend Setup
Create a virtual environment and install the required dependencies:
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Database Configuration
Copy `.env.example` to `.env` at the repository root and replace the example PostgreSQL password with your local credentials. For a local database, set `POSTGRES_HOST=localhost`.

### 3. Launch Services
Start the backend FastAPI server:
```bash
uvicorn app.main:app --reload
```
You can verify the running instance by calling `http://localhost:8000/health`.

For Docker Compose environments, configure your `.env` credentials and execute:
```bash
docker compose up --build
```

### 4. Running the Test Suite
From the `backend` directory with the virtual environment active, run:
```bash
pytest
```
The test suite utilizes an in-memory SQLite database for rapid, isolated testing.

---

## Data Pipeline & Simulation Commands

### Generate Deterministic Baseline
To write a baseline payment stream for training or testing, execute:
```bash
backend/.venv/Scripts/python.exe -m simulator.generate --count 500000 --seed 42
```
Add the `--ingest` flag to write directly to your configured PostgreSQL database.

### Replay and Inject Incidents
To inject a simulated scenario into a baseline transaction file:
```bash
backend/.venv/Scripts/python.exe -m simulator.inject --input data/baseline.csv --scenario simulator/scenarios/bank_upi_timeout.json --seed 42 --output data/scenario.csv --ground-truth data/ground_truth.json
```

### Detect Anomalies
To execute the anomaly detector on the replayed stream:
```bash
backend/.venv/Scripts/python.exe -m ml.run_detection --baseline data/baseline.csv --input data/scenario.csv --ground-truth data/ground_truth.json
```

---

## Known Limitations & Future Scope

### Causal Segment Assumption
*The Issue*: The recovery agent currently assumes that the segment flagged as degraded by the ML detector is the true root cause. For instance, if HDFC Bank UPI fails, the agent immediately initiates UPI routing mitigations.
*Future Scope*: In production, association does not equal causation. A spike in HDFC failures might stem from a regional gateway issue rather than the bank itself. We want to implement an active causal validation step that runs quick, randomized segment routing trials (A/B testing) to confirm the root cause before committing to heavy recovery actions.

### Static Routing Bounds
*The Issue*: The gateway router uses statically defined, hardcoded boundary distributions (33% split) rather than fluid routing boundaries.
*Future Scope*: We want to implement dynamic PID-controlled routing pools that continuously balance traffic using performance feedback loops and gateway transaction economics.

---

## Project Directory Map

```text
payment-pulse/
├── backend/                 # FastAPI app, database engine, and pytest suites
│   ├── app/
│   │   ├── api/             # Router endpoints
│   │   ├── core/            # Configuration variables
│   │   ├── db/              # SQLAlchemy engine sessions
│   │   ├── models/          # SQLAlchemy model classes
│   │   └── main.py          # Main entrypoint
│   └── tests/               # Integration & unit test files
├── ml/                      # Z-score statistical anomaly detection
├── simulator/               # Synthetic transaction generator, injector, and counterfactuals
├── agent/                   # SRE fallback agent & policy runner
├── ops_dashboard/           # SRE console layout, event feeds, and chart timeline
├── customer_portal/         # Checkout simulator
├── docs/                    # Architecture schemas and specs
├── docker-compose.yml
├── .env.example
└── README.md
```
