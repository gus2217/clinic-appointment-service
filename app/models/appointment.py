import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STATUSES = ("pending", "booked", "cancelled", "completed", "no_show", "rejected", "expired")

class Appointment(Base):
    __tablename__ = "appointment"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="booked")
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("status IN ('pending','booked','cancelled','completed','no_show','rejected','expired')", name="ck_status"),
        CheckConstraint("ends_at = starts_at + interval '30 minutes'", name="ck_duration"),
        CheckConstraint("(status = 'cancelled') = (cancel_reason IS NOT NULL)", name="ck_cancel_consistent"),
        CheckConstraint("(status = 'pending') = (hold_expires_at IS NOT NULL)", name="ck_hold"),
        Index("ix_appt_doctor_day", "doctor_id", "starts_at", postgresql_where=text("status IN ('pending','booked')")),
        Index("ix_appt_patient", "patient_id", "starts_at", postgresql_where=text("status IN ('pending','booked')")),
    )
