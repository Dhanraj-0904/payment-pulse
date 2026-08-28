"""Deterministic Gateway Recovery Demo for the AI Payment Recovery Agent."""

import os
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from simulator.config import GeneratorConfig
from simulator.incidents import IncidentConfig, IncidentType, Severity
from simulator.environment import StatefulSimulator
from simulator.generator import generate_transactions
from agent.tools import SimulatorToolbox
from agent.recovery_agent import (
    PolicyFallbackAgent,
    calculate_diagnosis_confidence,
    diagnose_incident,
    rank_candidates,
)


def run_demo() -> None:
    # Ensure default fallback mode
    os.environ["LLM_PROVIDER"] = "mock"
    if "LLM_API_KEY" in os.environ:
        del os.environ["LLM_API_KEY"]

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Generate/load baseline data (5000 transactions)
    baseline_cfg = GeneratorConfig(
        transaction_count=5000,
        random_seed=100,
        start_timestamp=base_time - timedelta(hours=5),
        transaction_frequency_seconds=1,
    )
    baseline_data = list(generate_transactions(baseline_cfg))

    # 2. Configure simulator step
    step_cfg = GeneratorConfig(
        transaction_count=300,
        random_seed=42,
        start_timestamp=base_time,
        transaction_frequency_seconds=1,
    )

    # 3. Configure incident
    incident = IncidentConfig(
        incident_id="INC_GATEWAY_DEGRADATION",
        incident_type=IncidentType.GATEWAY_DEGRADATION,
        severity=Severity.HIGH,
        start_time=base_time + timedelta(minutes=5),
        duration_minutes=20,
        recovery_minutes=0,
        affected_gateway="gateway_gamma",
        affected_transaction_percentage=1.0,
        failure_rate_multiplier=6.0,
        latency_multiplier=3.0,
    )

    # 4. Instantiate StatefulSimulator
    sim = StatefulSimulator(step_cfg, [incident], baseline_transactions=baseline_data)
    sim.reset()

    # Step 1: Baseline normal
    sim.step()

    # Step 2: Inject gateway degradation (Step 2 is the incident window)
    obs_before, _ = sim.step()

    # 5. Initialize SimulatorToolbox and Fallback Agent
    toolbox = SimulatorToolbox(sim)
    fallback = PolicyFallbackAgent(toolbox)

    confidence = calculate_diagnosis_confidence(obs_before)
    diagnosis = diagnose_incident(obs_before, confidence)

    # Get candidates and rank them
    candidates = toolbox.list_available_actions()
    ranked = rank_candidates(toolbox, candidates, confidence, diagnosis)


    # Execute the agent loop
    trace = fallback.run()

    # Generate candidate output logs
    candidate_lines = []
    for c, score in ranked[:3]:
        sim_res = toolbox.simulate_action(c)
        success_improvement = sim_res.get("projected_success_rate", 0.0) - obs_before.success_rate
        rev_reduction = Decimal(str(obs_before.revenue_at_risk)) - Decimal(sim_res.get("projected_revenue_at_risk", "0"))
        
        c_type = c["action_type"]
        params = c["parameters"]
        
        detail_str = f"{c_type}\n"
        if c_type == "ROUTE_TRAFFIC":
            detail_str += f"  Route: {params.get('source_gateway')} -> {params.get('destination_gateway')}\n"
            detail_str += f"  Traffic: {params.get('traffic_percentage')}%\n"
        elif c_type == "REDUCE_GATEWAY_TRAFFIC":
            detail_str += f"  Gateway: {params.get('gateway')}\n"
            detail_str += f"  Traffic reduction: {params.get('traffic_percentage')}%\n"
        
        detail_str += f"  Expected recovery: +{success_improvement*100:.2f}%\n"
        detail_str += f"  Revenue risk reduction: INR {rev_reduction:.2f}\n"
        detail_str += f"  Risk: {'LOW' if c_type in ['ROUTE_TRAFFIC', 'REDUCE_GATEWAY_TRAFFIC'] else 'HIGH'}\n"
        detail_str += f"  Reversible: YES\n"
        detail_str += f"  Score: {score:.4f}"
        candidate_lines.append(detail_str)

    # Output report
    print("==================================================")
    print("PAYMENT PULSE - AI RECOVERY AGENT")
    print("==================================================")
    print()
    print("MODE:")
    print("POLICY_FALLBACK")
    print()
    print("INCIDENT DETECTED:")
    print(diagnosis["root_cause"].replace("_", " ").title())
    print()
    print("AFFECTED GATEWAY:")
    print(diagnosis["affected_gateway"] or "None")
    print()
    print("PAYMENT METHOD:")
    print(diagnosis["affected_payment_method"] or "UPI")
    print()
    print("BANK:")
    print(diagnosis["affected_bank"] or "HDFC")
    print()
    print("--------------------------------------------------")
    print("BEFORE RECOVERY")
    print("--------------------------------------------------")
    print()
    print("Success rate:")
    print(f"{obs_before.success_rate * 100:.2f}%")
    print()
    print("Revenue at risk:")
    print(f"INR {obs_before.revenue_at_risk:.2f}")
    print()
    print("Anomaly score:")
    print(f"{obs_before.anomaly_score:.2f}")
    print()
    print("Diagnosis confidence:")
    print(f"{confidence * 100:.0f}%")
    print()
    print("--------------------------------------------------")
    print("INVESTIGATION")
    print("--------------------------------------------------")
    print()
    print("Root cause:")
    print(diagnosis["root_cause"].replace("_", " ").title())
    print()
    print("Evidence:")
    print(diagnosis["evidence_summary"])
    print()
    print("--------------------------------------------------")
    print("CANDIDATE ACTIONS")
    print("--------------------------------------------------")
    print()
    for line in candidate_lines:
        print(line)
        print()
    print("--------------------------------------------------")
    print("SELECTED ACTION")
    print("--------------------------------------------------")
    print()
    
    sel = trace.selected_action
    if sel:
        sel_type = sel["action_type"]
        sel_params = sel["parameters"]
        print(sel_type)
        print()
        if sel_type == "ROUTE_TRAFFIC":
            print(f"{sel_params.get('source_gateway')} -> {sel_params.get('destination_gateway')}")
            print()
            print("Traffic:")
            print(f"{sel_params.get('traffic_percentage')}%")
        elif sel_type == "REDUCE_GATEWAY_TRAFFIC":
            print(f"Reduce traffic load on {sel_params.get('gateway')}")
            print()
            print("Traffic:")
            print(f"{sel_params.get('traffic_percentage')}%")
        print()
        print("Reason:")
        print(trace.reasoning_summary[0])
    else:
        print("NONE")
    print()
    print("--------------------------------------------------")
    print("AFTER RECOVERY")
    print("--------------------------------------------------")
    print()
    
    after_success = trace.after_metrics["success_rate"] if trace.after_metrics else 0.0
    after_risk = Decimal(trace.after_metrics["revenue_at_risk"]) if trace.after_metrics else Decimal("0.00")
    recovered = Decimal(str(obs_before.revenue_at_risk)) - after_risk
    
    print("Success rate:")
    print(f"{after_success * 100:.2f}%")
    print()
    print("Revenue at risk:")
    print(f"INR {after_risk:.2f}")
    print()
    print("Estimated recovered revenue:")
    print(f"INR {max(Decimal('0.00'), recovered):.2f}")
    print()
    print("--------------------------------------------------")
    print("DECISION")
    print("--------------------------------------------------")
    print()
    print(trace.status)
    print()
    print(trace.decision)
    print()
    print("==================================================")


if __name__ == "__main__":
    run_demo()
