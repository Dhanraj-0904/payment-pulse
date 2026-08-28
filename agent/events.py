import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

class PaymentEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"EVT_{uuid.uuid4().hex[:8].upper()}")
    event_type: str  # e.g., PAYMENT_INITIATED, PAYMENT_SUCCESS, INCIDENT_DETECTED, etc.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    transaction_id: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "INR"
    payment_method: Optional[str] = None
    bank: Optional[str] = None
    gateway: Optional[str] = None
    merchant: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
