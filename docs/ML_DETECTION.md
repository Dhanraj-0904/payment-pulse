# ML Payment Incident Detection

## Scope

Phase 3 is an interpretable statistical detection layer. It reads transaction-level operational data, aggregates payment-health windows, scores deviations from a normal baseline, groups alerts, and produces investigation evidence. It does not use `incident_id`, incident type, root cause, or any Phase 2 ground truth during inference.

Ground truth is loaded only by the optional evaluation function after detection has completed.

## Feature engineering

Default five-minute windows are produced at these levels: global, bank, payment method, gateway, bank + payment method, merchant, and location. Each window includes transaction, success, failure, amount, failed-amount, mean/median/p95/p99 latency, and error/method/bank/gateway distributions.

Inference inputs are explicitly restricted to numerical operational fields: counts, success/failure rates, latency values, and timeout/network/auth error rates.

## Baseline and scoring

`fit_baseline()` learns historical and trailing-six-window mean and standard deviation per segment from normal data. The detector uses historical z-score deviations for success-rate degradation, failure-rate increase, latency increase, and timeout/network/auth spikes.

Contributions are capped and weighted as follows: success degradation 0.35, failure increase 0.20, latency increase 0.25, timeout spike 0.12, network-error spike 0.05, and auth-failure spike 0.03. Their sum is the deterministic anomaly score.

To reduce noise from small natural error counts, a score must also show a material operational change: at least 15% failure rate, 1,800 ms mean latency, or a 12% timeout/network/auth error rate. The default anomaly threshold is 0.55.

## Incidents, severity, and evidence

Adjacent anomalous windows for the same segment are grouped. A bank + payment-method alert suppresses its duplicate bank/method roll-up. Severity is prototype-only: score >= 0.85 or substantial low-success traffic is `CRITICAL`; >= 0.70 is `HIGH`; >= 0.55 is `MEDIUM`; otherwise `LOW`.

Evidence compares the detected period with an equal prior period and provides top banks, methods, gateways, merchants, locations, and error codes with baseline/current rates, deltas, and percentage changes. Deterministic hints identify patterns such as bank+UPI timeout, gateway degradation, card auth degradation, regional network degradation, and merchant configuration failure. These are investigation hints, not root-cause classifier predictions.

## Run detection and evaluation

Generate a baseline and scenario using the Phase 1/2 commands, then run:

```bash
backend/.venv/Scripts/python.exe -m ml.run_detection \
  --baseline data/baseline.csv \
  --input data/scenario.csv \
  --output data/detection_report.json
```

This performs inference without ground truth. To evaluate separately, provide the Phase 2 output only after the above inference path is complete:

```bash
backend/.venv/Scripts/python.exe -m ml.run_detection \
  --baseline data/baseline.csv \
  --input data/scenario.csv \
  --ground-truth data/ground_truth.json
```

The report includes precision, recall, F1, false-positive rate, incident detection rate, detection latency, and per-type detection rate. `ml.visualize.plot_detection()` optionally creates success-rate, latency, anomaly-score, and shaded detection-region charts when `matplotlib` is available.

## Comparison and limitations

`static_threshold_scores()` implements a simple fixed-threshold comparator. The rolling/historical z-score detector is the primary prototype because it accounts for segment-specific normal variability. Isolation Forest is intentionally deferred: it adds a scikit-learn dependency and has not yet demonstrated a measurable benefit on this synthetic dataset.

Thresholds, profiles, and metrics are development prototypes, not production-calibrated controls. Evaluation should use held-out incident start times, target dimensions, severity levels, and seeds; incident IDs are never features and cannot be memorized.
