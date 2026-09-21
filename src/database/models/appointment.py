"""SQLAlchemy model for Scheduled Appointments and Token Queues."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class Appointment(Base):
    """Represents a booked or pending appointment bound to a business tenant."""
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    call_sid: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    caller_phone: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    customer_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    doctor_id: Mapped[str] = mapped_column(String(64), default="primary", nullable=False)
    slot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        default="CONFIRMED",
        nullable=False
    )  # CONFIRMED, AFTER_HOURS_PENDING_REVIEW, CANCELLED
    token_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    business: Mapped["Business"] = relationship("Business", back_populates="appointments")
