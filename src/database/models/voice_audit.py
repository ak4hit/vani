"""SQLAlchemy model for Voice Cloning Consent Audit Logging.

Complies with voice cloning legal requirements (e.g., Texas SB 140, TCPA):
Guarantees a permanent, immutable audit log of explicit consent before any cloned voice is activated.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.business import Business


class VoiceConsentAudit(Base):
    """Permanent audit log capturing explicit consent timestamp for voice cloning."""
    __tablename__ = "voice_consent_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    voice_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    voice_name: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    consent_by_chat_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    sample_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    business: Mapped["Business"] = relationship("Business", back_populates="voice_audits")
