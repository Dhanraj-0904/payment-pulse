"""Incident definitions and configuration objects for deterministic injection."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class IncidentType(StrEnum):
    BANK_UPI_TIMEOUT = "BANK_UPI_TIMEOUT"
    GATEWAY_DEGRADATION = "GATEWAY_DEGRADATION"
    CARD_AUTH_FAILURE = "CARD_AUTH_FAILURE"
    REGIONAL_NETWORK_DEGRADATION = "REGIONAL_NETWORK_DEGRADATION"
    MERCHANT_SPECIFIC_FAILURE = "MERCHANT_SPECIFIC_FAILURE"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


ROOT_CAUSES = {
    IncidentType.BANK_UPI_TIMEOUT: "BANK_TIMEOUT",
    IncidentType.GATEWAY_DEGRADATION: "GATEWAY_DEGRADATION",
    IncidentType.CARD_AUTH_FAILURE: "CARD_AUTH_DEGRADATION",
    IncidentType.REGIONAL_NETWORK_DEGRADATION: "REGIONAL_NETWORK_DEGRADATION",
    IncidentType.MERCHANT_SPECIFIC_FAILURE: "MERCHANT_CONFIGURATION_FAILURE",
}

PRIMARY_ERRORS = {
    IncidentType.BANK_UPI_TIMEOUT: "TIMEOUT",
    IncidentType.GATEWAY_DEGRADATION: "NETWORK_ERROR",
    IncidentType.CARD_AUTH_FAILURE: "AUTH_FAILED",
    IncidentType.REGIONAL_NETWORK_DEGRADATION: "NETWORK_ERROR",
    IncidentType.MERCHANT_SPECIFIC_FAILURE: "UNKNOWN",
}

TYPE_DEFAULTS = {
    IncidentType.BANK_UPI_TIMEOUT: (8.0, 4.0),
    IncidentType.GATEWAY_DEGRADATION: (6.0, 3.0),
    IncidentType.CARD_AUTH_FAILURE: (7.0, 1.8),
    IncidentType.REGIONAL_NETWORK_DEGRADATION: (5.0, 3.5),
    IncidentType.MERCHANT_SPECIFIC_FAILURE: (6.0, 1.5),
}


@dataclass(frozen=True, kw_only=True)
class IncidentConfig:
    """Common externally configurable controls for a temporary degradation event."""

    incident_id: str
    incident_type: IncidentType
    start_time: datetime
    duration_minutes: int
    severity: Severity
    affected_bank: str | None = None
    affected_payment_method: str | None = None
    affected_gateway: str | None = None
    affected_merchant: str | None = None
    affected_location: str | None = None
    failure_rate_multiplier: float = 1.0
    latency_multiplier: float = 1.0
    affected_transaction_percentage: float = 1.0
    recovery_minutes: int = 10
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.incident_id:
            raise ValueError("incident_id is required")
        if self.start_time.tzinfo is None:
            raise ValueError("start_time must include a UTC offset")
        if self.duration_minutes < 1 or self.recovery_minutes < 0:
            raise ValueError("duration_minutes must be positive and recovery_minutes cannot be negative")
        if self.failure_rate_multiplier < 1 or self.latency_multiplier < 1:
            raise ValueError("incident multipliers must be at least 1")
        if not 0 < self.affected_transaction_percentage <= 1:
            raise ValueError("affected_transaction_percentage must be in (0, 1]")
        if self.incident_type is IncidentType.BANK_UPI_TIMEOUT and (
            not self.affected_bank or self.affected_payment_method != "UPI"
        ):
            raise ValueError("BANK_UPI_TIMEOUT requires affected_bank and affected_payment_method=UPI")
        if self.incident_type is IncidentType.GATEWAY_DEGRADATION and not self.affected_gateway:
            raise ValueError("GATEWAY_DEGRADATION requires affected_gateway")
        if self.incident_type is IncidentType.CARD_AUTH_FAILURE and self.affected_payment_method != "CARD":
            raise ValueError("CARD_AUTH_FAILURE requires affected_payment_method=CARD")
        if self.incident_type is IncidentType.REGIONAL_NETWORK_DEGRADATION and not self.affected_location:
            raise ValueError("REGIONAL_NETWORK_DEGRADATION requires affected_location")
        if self.incident_type is IncidentType.MERCHANT_SPECIFIC_FAILURE and not self.affected_merchant:
            raise ValueError("MERCHANT_SPECIFIC_FAILURE requires affected_merchant")

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(minutes=self.duration_minutes)

    @property
    def recovery_end_time(self) -> datetime:
        return self.end_time + timedelta(minutes=self.recovery_minutes)

    @property
    def expected_root_cause(self) -> str:
        return ROOT_CAUSES[self.incident_type]

    @property
    def affected_error_code(self) -> str:
        return PRIMARY_ERRORS[self.incident_type]

    def to_parameters(self) -> dict:
        data = asdict(self)
        data["incident_type"] = self.incident_type.value
        data["severity"] = self.severity.value
        data["start_time"] = self.start_time.isoformat()
        return data


@dataclass(frozen=True, kw_only=True)
class BankUpiTimeoutConfig(IncidentConfig):
    incident_type: IncidentType = IncidentType.BANK_UPI_TIMEOUT


@dataclass(frozen=True, kw_only=True)
class GatewayDegradationConfig(IncidentConfig):
    incident_type: IncidentType = IncidentType.GATEWAY_DEGRADATION


@dataclass(frozen=True, kw_only=True)
class CardAuthFailureConfig(IncidentConfig):
    incident_type: IncidentType = IncidentType.CARD_AUTH_FAILURE


@dataclass(frozen=True, kw_only=True)
class RegionalNetworkDegradationConfig(IncidentConfig):
    incident_type: IncidentType = IncidentType.REGIONAL_NETWORK_DEGRADATION


@dataclass(frozen=True, kw_only=True)
class MerchantSpecificFailureConfig(IncidentConfig):
    incident_type: IncidentType = IncidentType.MERCHANT_SPECIFIC_FAILURE


CONFIG_CLASSES = {
    IncidentType.BANK_UPI_TIMEOUT: BankUpiTimeoutConfig,
    IncidentType.GATEWAY_DEGRADATION: GatewayDegradationConfig,
    IncidentType.CARD_AUTH_FAILURE: CardAuthFailureConfig,
    IncidentType.REGIONAL_NETWORK_DEGRADATION: RegionalNetworkDegradationConfig,
    IncidentType.MERCHANT_SPECIFIC_FAILURE: MerchantSpecificFailureConfig,
}
