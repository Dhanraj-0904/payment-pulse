# Payment Pulse Live Payment Network Sandbox Architecture (Phase 5)

This document describes the design, communication protocol, and operations of the decoupled live payment network simulation.

---

## 1. Separate Application Boundaries

Payment Pulse now runs as two completely decoupled application systems:

```
                  PAYMENT PULSE
               AI/RISK OPERATIONS (Port 8000)
                       │
               WebSocket / API
                       │
        ┌──────────────┴──────────────┐
        │                             │
 AGENT DASHBOARD                 EVENT STREAM
 Dashboard UI (/index.html)      Live websocket events
        │                             │
        │                             │
        └──────────────┬──────────────┘
                       │
              DEMO PAYMENT API
              /api/payments/*
                       │
               CUSTOMER PORTAL (Port 8010)
               Checkout Page (/index.html)
                       │
           ┌───────────┼───────────┐
           │           │           │
        Gateway A   Gateway B   Gateway C
```

### Application 1: Customer Payment Portal (Port 8010)
*   **Path**: `payment_portal/`
*   **Purpose**: Simulates a customer-facing watch/electronics e-commerce storefront.
*   **Security Isolation**: Standard zero-import separation. This portal never imports ML, agent, or simulator code. It communicates purely via REST proxy HTTP calls.

### Application 2: Payment Pulse SRE Operations Console (Port 8000)
*   **Path**: `backend/` & `ops_dashboard/`
*   **Purpose**: Serves SRE operations metrics, active incident alerts, live event timelines (WebSocket), and triggers the AI Recovery Agent's loop.

---

## 2. Event Contract (Pydantic Models)

All transaction lifecycle and SRE events conform to a typed contract specified in `agent/events.py`:
- `PAYMENT_INITIATED` / `PAYMENT_PROCESSING`: Triggered when checkout begins.
- `PAYMENT_SUCCESS` / `PAYMENT_FAILED`: Triggered on payment final status callback.
- `INCIDENT_DETECTED`: Triggered when ML anomaly scores exceed adaptive threshold.
- `REVENUE_RISK_UPDATED`: Streams current steps success rate, latencies, and Dynamic Revenue-at-Risk.
- `RECOVERY_ACTION_PROPOSED` / `RECOVERY_ACTION_EXECUTED`: Published when the AI agent evaluates and routes traffic.
- `RECOVERY_COMPLETED`: Signals successful mitigation or system reset.

---

## 3. Kaggle Data Ingestion & Calibration

Simulated transactions are calibrated against PaySim Kaggle transaction datasets to ensure realistic distributions:
- **Pipeline**: Ingests CSV records under `data/external/` via `loader.py`.
- **Normalization**: Translates column schemas into `TransactionRecord` format using `normalizer.py`.
- **Profiling**: Extracts statistical metrics (average amount, type frequency distributions) via `profiler.py` and outputs `data/processed/paysim_profile.json`.
- **Live Stream Integration**: The live transaction generator stochastically generates amounts and payment methods following these profiles.

---

## 4. How to Launch and Run the Sandbox Demo

### Step 1: Ingest and Calibrate Profile Data
If not already done, generate the PaySim-style transaction calibration profile:
```powershell
backend\.venv\Scripts\python.exe -c "from data_ingestion.loader import CSVLoader; from data_ingestion.normalizer import Normalizer; from data_ingestion.profiler import Profiler; loader = CSVLoader('data/external/paysim_sample.csv'); rows = loader.load(); normalized = [Normalizer.normalize_row(r) for r in rows]; prof = Profiler(normalized); prof.save_profile('data/processed/paysim_profile.json')"
```

### Step 2: Start Payment Pulse Operations Backend (Port 8000)
From the root directory:
```powershell
cd backend
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000
```
*   **Dashboard URL**: Open `http://localhost:8000/` in your browser.

### Step 3: Start Customer Payment Portal (Port 8010)
Open a separate terminal window and run:
```powershell
cd payment_portal/backend
..\..\backend\.venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8010
```
*   **Checkout URL**: Open `http://localhost:8010/` in your browser.

---

## 5. Walkthrough: Triggering Recovery Loop

1.  Open the **Operations Console** (`http://localhost:8000/`) and click **Start Live Traffic**.
2.  Observe the live streaming success rate (100%) and transaction feed updating.
3.  Go to the **Customer Portal** (`http://localhost:8010/`) and checkout a laptop using the simulated credit card number (`4111 1111 1111 1111`). Verify that it succeeds and registers instantly in the Operations Console feed.
4.  On the Operations Console, click **Degrade gateway_gamma** to inject an active incident.
5.  Watch the success rate drop, triggering the **Incident Alert** banner.
6.  The **AI Recovery Agent** console will wake up:
    -   *Evaluating Actions*: Reviews candidates via dry-run simulation.
    -   *Executing Recovery*: Selects and executes `REDUCE_GATEWAY_TRAFFIC gateway_gamma 50%`.
7.  Verify that success rates recover and subsequent customer checkouts succeed.
8.  Click **Reset Demo System** to return all gateway metrics to normal baseline health.
