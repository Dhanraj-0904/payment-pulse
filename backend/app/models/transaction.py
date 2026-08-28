"""Transaction persistence model for synthetic or public development data."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    merchant_id: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    payment_method: Mapped[str] = mapped_column(String(32), index=True)
    bank: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    network_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    incident_external_id: Mapped[str | None] = mapped_column("incident_id", String(64), nullable=True, index=True)
