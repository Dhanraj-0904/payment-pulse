"""JSON scenario loading, with no external parsing dependency."""

import json
from datetime import datetime
from pathlib import Path

from simulator.incidents import CONFIG_CLASSES, TYPE_DEFAULTS, IncidentConfig, IncidentType, Severity


def incident_config_from_mapping(data: dict) -> IncidentConfig:
    """Create a typed config from one JSON scenario object."""
    try:
        incident_type = IncidentType(data["incident_type"])
        start_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))
        severity = Severity(data.get("severity", "MEDIUM"))
    except (KeyError, ValueError) as exc:
        raise ValueError("scenario incident requires valid incident_type, start_time, and severity") from exc
    default_failure, default_latency = TYPE_DEFAULTS[incident_type]
    affected = {
        "affected_bank": data.get("affected_bank", data.get("bank")),
        "affected_payment_method": data.get("affected_payment_method", data.get("payment_method")),
        "affected_gateway": data.get("affected_gateway", data.get("gateway")),
        "affected_merchant": data.get("affected_merchant", data.get("merchant")),
        "affected_location": data.get("affected_location", data.get("location")),
    }
    return CONFIG_CLASSES[incident_type](
        incident_id=data["incident_id"],
        start_time=start_time,
        duration_minutes=int(data.get("duration_minutes", 30)),
        severity=severity,
        failure_rate_multiplier=float(data.get("failure_rate_multiplier", default_failure)),
        latency_multiplier=float(data.get("latency_multiplier", default_latency)),
        affected_transaction_percentage=float(data.get("affected_transaction_percentage", 1.0)),
        recovery_minutes=int(data.get("recovery_minutes", 10)),
        description=data.get("description"),
        **affected,
    )


def load_scenario(path: str | Path) -> list[IncidentConfig]:
    """Load a JSON object containing an `incidents` list."""
    with Path(path).open(encoding="utf-8") as stream:
        document = json.load(stream)
    incidents = document.get("incidents", document.get("scenarios"))
    if not isinstance(incidents, list) or not incidents:
        raise ValueError("scenario JSON must contain a non-empty incidents array")
    configs = [incident_config_from_mapping(item) for item in incidents]
    identifiers = [config.incident_id for config in configs]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("incident IDs must be unique within a scenario")
    return configs
