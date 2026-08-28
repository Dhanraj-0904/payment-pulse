"""Canonical transaction representation and payment-domain constants."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

PAYMENT_METHODS = ("UPI", "CARD", "NETBANKING", "WALLET")
STATUSES = ("SUCCESS", "FAILED")
ERROR_CODES = (
    "TIMEOUT",
    "NETWORK_ERROR",
    "BANK_DECLINED",
    "CARD_DECLINED",
    "AUTH_FAILED",
    "UNKNOWN",
)

METHOD_ERRORS = {
    "UPI": {"TIMEOUT", "NETWORK_ERROR", "BANK_DECLINED", "AUTH_FAILED", "UNKNOWN"},
    "CARD": {"TIMEOUT", "NETWORK_ERROR", "BANK_DECLINED", "CARD_DECLINED", "AUTH_FAILED", "UNKNOWN"},
    "NETBANKING": {"TIMEOUT", "NETWORK_ERROR", "BANK_DECLINED", "AUTH_FAILED", "UNKNOWN"},
    "WALLET": {"TIMEOUT", "NETWORK_ERROR", "AUTH_FAILED", "UNKNOWN"},
}


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    """A normalized transaction; `incident_id` remains empty for baseline data."""

    transaction_id: str
    timestamp: datetime
    merchant_id: str
    amount: Decimal
    currency: str
    payment_method: str
    bank: str
    gateway: str
    device_type: str
    network_type: str
    location: str
    latency_ms: int
    status: str
    error_code: str | None
    incident_id: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)
