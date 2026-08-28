"""Policy guardrails and safety constraint validators for recovery actions."""

from typing import Any
from dataclasses import asdict
from simulator.environment import (
    VALID_BANKS,
    VALID_GATEWAYS,
    VALID_METHODS,
    VALID_MERCHANTS,
)

MAX_SINGLE_ACTION_TRAFFIC = 50.0


def check_policy(action: dict[str, Any], current_observation: Any) -> tuple[bool, str]:
    """Validate action parameters against safety constraints.

    Returns:
        tuple[bool, str]: (accepted, reason)
    """
    if not isinstance(action, dict):
        return False, "Action must be a dictionary."

    action_type = action.get("action_type")
    params = action.get("parameters")
    explanation = action.get("explanation")

    if not action_type or not isinstance(action_type, str):
        return False, "Action type must be a valid non-empty string."

    if not isinstance(params, dict):
        return False, "Parameters must be a dictionary."

    if not explanation or not isinstance(explanation, str) or len(explanation.strip()) < 5:
        return False, "Action rejected: explanation must be a valid string explaining the rationale."

    # Identify unhealthy gateways from observation
    unhealthy_gateways = set()
    try:
        obs_dict = current_observation if isinstance(current_observation, dict) else asdict(current_observation)
    except Exception:
        obs_dict = {}
    
    # 1. Look for gateway incidents
    for inc in obs_dict.get("active_incidents", []):
        for segment in obs_dict.get("top_affected_segments", []):
            if "gateway" in segment.get("segment", ""):
                unhealthy_gateways.add(segment["segment"].split("|")[-1])

    # 2. Look for gateway failure metrics > 10% in investigation evidence
    for ev in obs_dict.get("investigation_evidence", []):
        for gw in ev.get("top_gateways", []):
            if gw.get("incident_metric", 0.0) > 0.10:
                unhealthy_gateways.add(gw["value"])

    if action_type == "ROUTE_TRAFFIC":
        src = params.get("source_gateway")
        dst = params.get("destination_gateway")
        bank = params.get("affected_bank")
        method = params.get("affected_payment_method")
        pct_raw = params.get("traffic_percentage")

        if src is None or dst is None:
            return False, "source_gateway and destination_gateway are required"
        if src not in VALID_GATEWAYS:
            return False, f"Unknown source_gateway: {src}"
        if dst not in VALID_GATEWAYS:
            return False, f"Unknown destination_gateway: {dst}"
        if src == dst:
            return False, "Source and destination gateways cannot be the same"
        if dst in unhealthy_gateways:
            return False, f"Action rejected: destination gateway {dst} is currently unhealthy/degraded"
        if bank is not None and bank not in VALID_BANKS:
            return False, f"Unknown affected_bank: {bank}"
        if method is not None and method not in VALID_METHODS:
            return False, f"Unknown affected_payment_method: {method}"

        if pct_raw is None:
            return False, "traffic_percentage is required"
        try:
            pct = float(pct_raw)
        except (ValueError, TypeError):
            return False, "traffic_percentage must be numeric"

        if pct > MAX_SINGLE_ACTION_TRAFFIC:
            return False, f"Action rejected: traffic_percentage {pct} exceeds policy limit of {MAX_SINGLE_ACTION_TRAFFIC}%"
        if pct <= 0.0:
            return False, "traffic_percentage must be positive"

    elif action_type == "REDUCE_GATEWAY_TRAFFIC":
        gw = params.get("gateway")
        pct_raw = params.get("traffic_percentage")

        if gw is None:
            return False, "gateway is required"
        if gw not in VALID_GATEWAYS:
            return False, f"Unknown gateway: {gw}"

        if pct_raw is None:
            return False, "traffic_percentage is required"
        try:
            pct = float(pct_raw)
        except (ValueError, TypeError):
            return False, "traffic_percentage must be numeric"

        if pct > MAX_SINGLE_ACTION_TRAFFIC:
            return False, f"Action rejected: traffic_percentage {pct} exceeds policy limit of {MAX_SINGLE_ACTION_TRAFFIC}%"
        if pct <= 0.0:
            return False, "traffic_percentage must be positive"

    elif action_type == "DISABLE_PAYMENT_METHOD":
        method = params.get("payment_method")
        dur_raw = params.get("duration_minutes")

        if method is None:
            return False, "payment_method is required"
        if method not in VALID_METHODS:
            return False, f"Unknown payment_method: {method}"

        if dur_raw is None:
            return False, "duration_minutes is required"
        try:
            dur = int(dur_raw)
        except (ValueError, TypeError):
            return False, "duration_minutes must be numeric"
        if dur <= 0:
            return False, "duration_minutes must be positive"

    elif action_type == "RATE_LIMIT_MERCHANT":
        merch = params.get("merchant")
        pct_raw = params.get("traffic_percentage")

        if merch is None:
            return False, "merchant is required"
        if merch not in VALID_MERCHANTS:
            return False, f"Unknown merchant: {merch}"

        if pct_raw is None:
            return False, "traffic_percentage is required"
        try:
            pct = float(pct_raw)
        except (ValueError, TypeError):
            return False, "traffic_percentage must be numeric"

        if pct > MAX_SINGLE_ACTION_TRAFFIC:
            return False, f"Action rejected: traffic_percentage {pct} exceeds policy limit of {MAX_SINGLE_ACTION_TRAFFIC}%"
        if pct <= 0.0:
            return False, "traffic_percentage must be positive"

    else:
        return False, f"Action rejected: unknown or irreversible action type: {action_type}"

    return True, "Action passes policy constraints"
