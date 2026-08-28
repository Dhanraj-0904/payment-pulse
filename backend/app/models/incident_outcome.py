"""Outcome measurements for a resolved incident."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class IncidentOutcome(Base):
    __tablename__ = "incident_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_db_id: Mapped[int] = mapped_column("incident_id", ForeignKey("incidents.id"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    success_rate_before: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    success_rate_after: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    estimated_revenue_protected: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    incident: Mapped["Incident"] = relationship(back_populates="outcomes")
