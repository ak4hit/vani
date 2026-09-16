"""SQLAlchemy models for Multi-Tenant Businesses, FAQs, and Call Logs."""

from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class Business(Base):
    """Business tenant entity holding voice bot configurations and quota limits."""
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    chat_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True, index=True, nullable=True)
    business_name: Mapped[str] = mapped_column(String(255), default="Vani Reception", nullable=False)
    industry: Mapped[str] = mapped_column(String(100), default="general", nullable=False)
    description: Mapped[str] = mapped_column(
        Text,
        default="AI-powered phone receptionist assisting customers with inquiries.",
        nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(100), default="Vani", nullable=False)
    hours: Mapped[str] = mapped_column(String(255), default="Mon-Fri 9:00 AM - 6:00 PM", nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    escalation_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_chunks: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    faqs: Mapped[List["FAQ"]] = relationship(
        "FAQ",
        back_populates="business",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    call_logs: Mapped[List["CallLog"]] = relationship(
        "CallLog",
        back_populates="business",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    document_chunks: Mapped[List["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="business",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class FAQ(Base):
    """Explicit Question & Answer pair strictly bound to a tenant business."""
    __tablename__ = "faqs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    business: Mapped["Business"] = relationship("Business", back_populates="faqs")


class CallLog(Base):
    """Post-call transcript, summary, and audit log."""
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    call_sid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    caller_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    business: Mapped["Business"] = relationship("Business", back_populates="call_logs")
