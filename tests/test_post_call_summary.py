"""Unit tests for post-call summarization, transcript formatting, and CallLog persistence."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from src.database.base import Base
from src.database.models.business import Business, CallLog
import src.database.session as db_session_module
from src.services.notification_service import NotificationService
from src.services.voice_pipeline import VoicePipelineSession


@pytest.fixture
async def summary_db():
    """Provides a fresh isolated in-memory SQLite database for post-call testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    # Patch get_session_maker so notification_service uses this test DB
    with patch.object(db_session_module, "get_session_maker", return_value=session_maker):
        yield session_maker

    await engine.dispose()


def test_format_duration():
    """Verify duration formatting into human-readable strings."""
    assert NotificationService.format_duration(45) == "45s"
    assert NotificationService.format_duration(60) == "1m 00s"
    assert NotificationService.format_duration(84) == "1m 24s"
    assert NotificationService.format_duration(3600) == "60m 00s"


def test_format_summary_card():
    """Verify markdown format of the Telegram post-call summary card."""
    card = NotificationService.format_summary_card(
        business_name="Green Valley Dental",
        caller_number="+15550192834",
        duration_seconds=135,
        summary="Caller asked for weekend appointment availability. Confirmed Saturday at 10 AM.",
        transcript="User: Are you open Saturday? Assistant: Yes, 10 AM is available."
    )
    assert "New Call Completed" in card
    assert "Green Valley Dental" in card
    assert "+15550192834" in card
    assert "2m 15s" in card
    assert "weekend appointment availability" in card
    assert "Transcript Excerpt" in card


@pytest.mark.asyncio
async def test_summarize_transcript():
    """Verify summary generation over chat history."""
    service = NotificationService()
    history = [
        {"role": "user", "content": "Hello, I want to book a teeth cleaning session."},
        {"role": "assistant", "content": "I can help with that. We have openings this Thursday."},
        {"role": "user", "content": "Thursday works great for me."}
    ]
    summary = await service.summarize_transcript(history)
    assert summary is not None
    assert len(summary) > 0


@pytest.mark.asyncio
async def test_process_post_call_persists_to_db(summary_db):
    """Verify that process_post_call creates a persistent CallLog record in the database."""
    # Seed a business tenant
    async with summary_db() as session:
        biz = Business(id="biz_summary_test", business_name="Dental Clinic", chat_id=12345)
        session.add(biz)
        await session.commit()

    service = NotificationService()
    chat_history = [
        {"role": "user", "content": "Do you accept Delta Dental insurance?"},
        {"role": "assistant", "content": "Yes, we accept Delta Dental and all major PPO plans."}
    ]

    # Process post call
    call_log = await service.process_post_call(
        business_id="biz_summary_test",
        call_sid="CA_SUMMARY_001",
        caller_number="+15558889999",
        duration_seconds=42,
        chat_history=chat_history,
        chat_id=12345,
        business_name="Dental Clinic",
        session_maker_override=summary_db
    )

    assert call_log is not None
    assert call_log.call_sid == "CA_SUMMARY_001"
    assert call_log.duration_seconds == 42
    assert "Delta Dental" in call_log.transcript

    # Query DB directly to verify persistence
    async with summary_db() as session:
        stmt = select(CallLog).where(CallLog.call_sid == "CA_SUMMARY_001")
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        assert record is not None
        assert record.business_id == "biz_summary_test"
        assert record.duration_seconds == 42


@pytest.mark.asyncio
async def test_send_telegram_mock():
    """Verify mock Telegram notification dispatch succeeds."""
    service = NotificationService(bot_token=None)
    success = await service.send_telegram_message(chat_id=99999, text="Test summary")
    assert success is True
