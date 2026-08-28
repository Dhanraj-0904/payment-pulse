"""Policy-controlled (future) agent action audit model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_db_id: Mapped[int] = mapped_column("incident_id", ForeignKey("incidents.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    policy_decision: Mapped[str] = mapped_column(String(32), default="pending")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    incident: Mapped["Incident"] = relationship(back_populates="actions")
