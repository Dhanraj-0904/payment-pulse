"""Detected payment-reliability incident model."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_external_id: Mapped[str] = mapped_column("incident_id", String(64), unique=True, index=True)
    incident_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    severity: Mapped[str] = mapped_column(String(32), default="MEDIUM")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    affected_bank: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    affected_payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    affected_gateway: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    affected_merchant: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    affected_location: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    affected_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_root_cause: Mapped[str] = mapped_column(String(64))
    injected_parameters: Mapped[dict] = mapped_column(JSON)
    estimated_revenue_at_risk: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    actions: Mapped[list["AgentAction"]] = relationship(back_populates="incident")
    outcomes: Mapped[list["IncidentOutcome"]] = relationship(back_populates="incident")
