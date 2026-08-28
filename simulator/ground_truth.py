"""Evaluation-only ground truth generated alongside an injected scenario."""

from dataclasses import asdict, dataclass
from datetime import datetime

from simulator.incidents import IncidentConfig


@dataclass(frozen=True, slots=True)
class IncidentGroundTruth:
    incident_id: str
    incident_type: str
    start_time: datetime
    end_time: datetime
    affected_dimensions: dict[str, str | None]
    expected_root_cause: str
    severity: str
    injection_parameters: dict
    affected_transaction_count: int

    def to_mapping(self) -> dict:
        result = asdict(self)
        result["start_time"] = self.start_time.isoformat()
        result["end_time"] = self.end_time.isoformat()
        return result


def create_ground_truth(config: IncidentConfig, affected_transaction_count: int) -> IncidentGroundTruth:
    return IncidentGroundTruth(
        incident_id=config.incident_id,
        incident_type=config.incident_type.value,
        start_time=config.start_time,
        end_time=config.end_time,
        affected_dimensions={
            "bank": config.affected_bank,
            "payment_method": config.affected_payment_method,
            "gateway": config.affected_gateway,
            "merchant": config.affected_merchant,
            "location": config.affected_location,
            "error_code": config.affected_error_code,
        },
        expected_root_cause=config.expected_root_cause,
        severity=config.severity.value,
        injection_parameters=config.to_parameters(),
        affected_transaction_count=affected_transaction_count,
    )
