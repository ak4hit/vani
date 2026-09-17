"""Unit and Integration Tests for Voice Features, Voice Design, and Consent Auditing.

Verifies compliance with voice cloning legal requirements (e.g. Texas SB 140, TCPA):
1. Voice cloning is blocked without explicit affirmative consent.
2. An immutable audit record is created in the database upon confirmed consent.
3. Natural language Voice Design v3 synthesis correctly generates custom personas.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.database.base import Base
from src.database.models.business import Business
import src.database.session as db_session_module
from src.services.business_store import business_store
from src.services.db_business_repository import BusinessRepository
from src.services.voice_service import (
    VoiceService,
    VoiceConsentRequiredError,
    LEGAL_VOICE_CONSENT_STATEMENT,
)
from src.bot.handlers.voice import (
    handle_upload_voice_command,
    handle_voice_consent_callback,
    handle_voice_design_command,
)


@pytest.fixture
async def voice_session_maker():
    """Provides an isolated in-memory SQLite database session maker."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    with patch.object(db_session_module, "get_session_maker", return_value=session_maker):
        yield session_maker

    await engine.dispose()


@pytest.mark.asyncio
async def test_voice_cloning_without_consent_raises_error(voice_session_maker):
    """MANDATORY COMPLIANCE TEST:
    Attempting to clone an audio sample without affirmative consent must be strictly rejected.
    """
    biz_id = "biz_consent_reject"
    business_store.reset()
    business_store.get_or_create_business(biz_id)

    async with voice_session_maker() as session:
        biz = Business(id=biz_id, business_name="Refusal Clinic")
        session.add(biz)
        await session.commit()

    service = VoiceService()
    sample_audio = b"MOCK_AUDIO_DATA_FOR_VOICE_SAMPLE" * 100

    with pytest.raises(VoiceConsentRequiredError) as exc_info:
        await service.clone_voice_from_audio(
            business_id=biz_id,
            audio_bytes=sample_audio,
            filename="unconsented_sample.mp3",
            voice_name="Unconsented Voice",
            consent_confirmed=False
        )

    assert "consent confirmation is legally mandatory" in str(exc_info.value)


@pytest.mark.asyncio
async def test_voice_cloning_with_consent_creates_audit_log(voice_session_maker):
    """MANDATORY COMPLIANCE TEST:
    Cloning with confirmed consent must generate an immutable audit log with timestamp, chat_id, and legal notice.
    """
    biz_id = "biz_consent_approved"
    business_store.reset()
    profile = business_store.get_or_create_business(biz_id, chat_id=123456)

    async with voice_session_maker() as session:
        biz = Business(id=biz_id, business_name="Consented Dental", chat_id=123456)
        session.add(biz)
        await session.commit()

    service = VoiceService()
    sample_audio = b"GENUINE_SAMPLE_AUDIO_BYTES_WITH_CONSENT" * 100

    result = await service.clone_voice_from_audio(
        business_id=biz_id,
        audio_bytes=sample_audio,
        filename="consented_receptionist.mp3",
        voice_name="Sarah Reception",
        consent_confirmed=True,
        chat_id=123456
    )

    assert result["success"] is True
    assert result["voice_id"].startswith("voice_clone_")
    assert result["consent_confirmed"] is True

    # Verify audit log in database
    async with voice_session_maker() as session:
        audits = await BusinessRepository.list_voice_consents(session, biz_id)
        assert len(audits) == 1
        audit = audits[0]
        assert audit.voice_id == result["voice_id"]
        assert audit.consent_confirmed is True
        assert audit.consent_by_chat_id == 123456
        assert audit.sample_filename == "consented_receptionist.mp3"
        assert LEGAL_VOICE_CONSENT_STATEMENT in audit.consent_text

        # Verify business model was updated
        db_biz = await BusinessRepository.get_business(session, biz_id)
        assert db_biz.voice_id == result["voice_id"]

    # Verify memory store profile updated
    profile = business_store.get_business(biz_id)
    assert profile.voice_id == result["voice_id"]


@pytest.mark.asyncio
async def test_voice_design_from_description(voice_session_maker):
    """Verify natural language voice design synthesizes voice ID without requiring audio sample."""
    biz_id = "biz_design_test"
    business_store.reset()
    business_store.get_or_create_business(biz_id)

    async with voice_session_maker() as session:
        biz = Business(id=biz_id, business_name="Apex Law")
        session.add(biz)
        await session.commit()

    service = VoiceService()
    result = await service.design_voice_from_description(
        business_id=biz_id,
        description="Warm, assertive, calm legal assistant with British accent",
        voice_name="British Legal"
    )

    assert result["success"] is True
    assert result["voice_id"].startswith("voice_design_")
    assert "British" in result["description"]

    # Verify updated in DB
    async with voice_session_maker() as session:
        db_biz = await BusinessRepository.get_business(session, biz_id)
        assert db_biz.voice_id == result["voice_id"]


@pytest.mark.asyncio
async def test_upload_voice_command_unregistered():
    """Verify /uploadvoice directs unregistered users to /start."""
    business_store.reset()
    update = MagicMock()
    update.effective_chat.id = 999999
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()

    await handle_upload_voice_command(update, context)
    update.effective_message.reply_text.assert_called_once()
    assert "complete onboarding first" in update.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_upload_voice_command_registered():
    """Verify /uploadvoice presents requirements and legal consent notice."""
    business_store.reset()
    business_store.get_or_create_business("biz_reg", chat_id=888888)

    update = MagicMock()
    update.effective_chat.id = 888888
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()

    await handle_upload_voice_command(update, context)
    reply = update.effective_message.reply_text.call_args[0][0]
    assert "Voice Cloning Setup" in reply
    assert "Legal Notice" in reply


@pytest.mark.asyncio
async def test_voice_consent_callback_cancel():
    """Verify user can cancel voice cloning and discard pending sample."""
    update = MagicMock()
    update.callback_query.data = "consent_voice_cancel"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = MagicMock()
    context.user_data = {
        "pending_voice_bytes": b"sample_bytes",
        "pending_business_id": "biz_cancel"
    }

    await handle_voice_consent_callback(update, context)

    assert "pending_voice_bytes" not in context.user_data
    update.callback_query.edit_message_text.assert_called_once_with(
        "❌ Voice cloning cancelled. The uploaded sample was discarded."
    )


@pytest.mark.asyncio
async def test_voice_design_command():
    """Verify /voicedesign parses prompt and triggers voice service."""
    business_store.reset()
    biz_id = "biz_vd_cmd"
    business_store.get_or_create_business(biz_id, chat_id=777777)

    update = MagicMock()
    update.effective_chat.id = 777777
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_msg)

    context = MagicMock()
    context.args = ["Cheerful,", "optimistic", "florist", "voice"]

    with patch.object(
        VoiceService,
        "design_voice_from_description",
        return_value={"voice_id": "vd_florist_123", "success": True}
    ):
        await handle_voice_design_command(update, context)

    status_msg.edit_text.assert_called_once()
    assert "vd_florist_123" in status_msg.edit_text.call_args[0][0]
