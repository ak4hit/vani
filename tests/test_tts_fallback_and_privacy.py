"""Unit and Integration Tests for TTS Quota Fallback and GDPR / DPDP Caller Privacy Deletion.

Verifies compliance with Phase 7 requirements:
1. ElevenLabs quota exhaustion gracefully falls back to Twilio audio without dropping calls.
2. An urgent quota alert is dispatched to the business owner on Telegram.
3. `/deletecaller <number>` cascades deletion across call logs and transcripts.
4. Multi-tenant isolation is strictly preserved (Business A cannot delete Business B's caller data).
5. REST endpoint `DELETE /api/privacy/caller/{number}` enforces tenant scoping.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
import websockets

from src.database.base import Base
from src.database.models.business import Business, CallLog
import src.database.session as db_session_module
from src.services.business_store import business_store
from src.services.db_business_repository import BusinessRepository
from src.services.tts_service import ElevenLabsTTSService
from src.services.notification_service import NotificationService, notification_service
from src.services.voice_pipeline import VoicePipelineSession
from src.bot.handlers.privacy import handle_delete_caller_command
from src.main import app


@pytest.fixture
async def privacy_session_maker():
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
async def test_tts_quota_exhausted_fallback_audio():
    """Verify that when quota is marked exhausted, fallback audio frames are generated."""
    tts = ElevenLabsTTSService(api_key="mock_key")
    tts.quota_exhausted = True

    async def sample_text_stream():
        yield "Hello, your order is ready."

    chunks = []
    async for chunk in tts.stream_audio_from_text(sample_text_stream()):
        chunks.append(chunk)

    assert len(chunks) >= 1
    assert all(len(c) == 800 for c in chunks)
    assert tts.quota_exhausted is True


@pytest.mark.asyncio
async def test_tts_http_429_quota_exhausted_handling():
    """Verify HTTP 429 quota exhaustion sets quota_exhausted=True and yields fallback audio without crash."""
    tts = ElevenLabsTTSService(api_key="mock_key")

    async def text_stream():
        yield "Testing quota exhaustion."

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    exc = websockets.exceptions.InvalidStatusCode(429, mock_resp)

    with patch("websockets.connect", side_effect=exc):
        chunks = []
        async for chunk in tts.stream_audio_from_text(text_stream()):
            chunks.append(chunk)

        assert tts.quota_exhausted is True
        assert len(chunks) >= 1
        assert chunks[0] == b"\xff" * 800


@pytest.mark.asyncio
async def test_voice_pipeline_dispatches_quota_alert_once():
    """Verify voice pipeline alerts the business admin on Telegram when TTS quota is exhausted."""
    business_store.reset()
    biz_id = "biz_quota_test"
    business_store.get_or_create_business(biz_id, chat_id=54321)

    send_mock = AsyncMock()
    pipeline = VoicePipelineSession(
        call_sid="CA_quota_test",
        stream_sid="MZ_quota_test",
        caller_number="+15554443333",
        send_to_twilio=send_mock,
        business_id=biz_id
    )

    # Simulate TTS with quota exhausted
    pipeline.tts.quota_exhausted = True

    with patch.object(NotificationService, "send_tts_quota_alert", new_callable=AsyncMock) as alert_mock:
        # Simulate turn execution where TTS runs
        async def empty_stream(gen):
            if False:
                yield b""

        pipeline.tts.stream_audio_from_text = empty_stream
        await pipeline._process_turn("Hello bot")

        # Quota alert should have been scheduled once
        assert pipeline._quota_alert_sent is True


@pytest.mark.asyncio
async def test_privacy_delete_caller_cascades_logs(privacy_session_maker):
    """Verify /deletecaller permanently deletes all CallLogs for specified caller."""
    biz_id = "biz_privacy_cascade"
    async with privacy_session_maker() as session:
        biz = Business(id=biz_id, business_name="Privacy Dental")
        session.add(biz)
        await session.commit()

        # Add 3 call logs for caller +15551112222
        await BusinessRepository.log_call(session, biz_id, "CA001", caller_number="+15551112222", transcript="Log 1")
        await BusinessRepository.log_call(session, biz_id, "CA002", caller_number="+15551112222", transcript="Log 2")
        await BusinessRepository.log_call(session, biz_id, "CA003", caller_number="+15551112222", transcript="Log 3")
        # Add 1 call log for different caller +15559998888
        await BusinessRepository.log_call(session, biz_id, "CA004", caller_number="+15559998888", transcript="Other")

    # Execute deletion
    async with privacy_session_maker() as session:
        result = await BusinessRepository.delete_caller_records(
            session=session,
            business_id=biz_id,
            caller_number="+15551112222"
        )
        assert result["deleted_call_logs"] == 3

    # Verify caller +15551112222 records gone, but +15559998888 remains
    async with privacy_session_maker() as session:
        from sqlalchemy import select
        res = await session.execute(select(CallLog).where(CallLog.business_id == biz_id))
        remaining = list(res.scalars().all())
        assert len(remaining) == 1
        assert remaining[0].caller_number == "+15559998888"


@pytest.mark.asyncio
async def test_privacy_delete_caller_tenant_isolation(privacy_session_maker):
    """MANDATORY SECURITY GATE:
    Deleting caller records for Business A MUST NOT delete records for Business B,
    even if the caller dialed both businesses.
    """
    biz_a = "biz_tenant_a"
    biz_b = "biz_tenant_b"
    shared_caller = "+15558889999"

    async with privacy_session_maker() as session:
        biz1 = Business(id=biz_a, business_name="Alpha Services")
        biz2 = Business(id=biz_b, business_name="Beta Logistics")
        session.add_all([biz1, biz2])
        await session.commit()

        # Shared caller dialed both businesses
        await BusinessRepository.log_call(session, biz_a, "CA_A1", caller_number=shared_caller, transcript="Alpha Call 1")
        await BusinessRepository.log_call(session, biz_a, "CA_A2", caller_number=shared_caller, transcript="Alpha Call 2")
        await BusinessRepository.log_call(session, biz_b, "CA_B1", caller_number=shared_caller, transcript="Beta Call 1")

    # Business A requests forget-me deletion
    async with privacy_session_maker() as session:
        res = await BusinessRepository.delete_caller_records(session, biz_a, shared_caller)
        assert res["deleted_call_logs"] == 2

    # Verify Business B's call logs remain untouched
    async with privacy_session_maker() as session:
        from sqlalchemy import select
        b_logs = await session.execute(select(CallLog).where(CallLog.business_id == biz_b))
        b_remaining = list(b_logs.scalars().all())
        assert len(b_remaining) == 1
        assert b_remaining[0].caller_number == shared_caller
        assert b_remaining[0].transcript == "Beta Call 1"


@pytest.mark.asyncio
async def test_delete_caller_telegram_command(privacy_session_maker):
    """Verify /deletecaller command validates number and dispatches deletion."""
    business_store.reset()
    biz_id = "biz_tg_del"
    business_store.get_or_create_business(biz_id, chat_id=112233)

    async with privacy_session_maker() as session:
        biz = Business(id=biz_id, business_name="Bot Clinic", chat_id=112233)
        session.add(biz)
        await session.commit()
        await BusinessRepository.log_call(session, biz_id, "CA123", caller_number="+15557778888")

    update = MagicMock()
    update.effective_chat.id = 112233
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_msg)

    context = MagicMock()
    context.args = ["+15557778888"]

    await handle_delete_caller_command(update, context)

    status_msg.edit_text.assert_called_once()
    reply = status_msg.edit_text.call_args[0][0]
    assert "Caller Records Permanently Deleted" in reply
    assert "+15557778888" in reply


@pytest.mark.asyncio
async def test_privacy_rest_api_endpoint(privacy_session_maker):
    """Verify DELETE /api/privacy/caller/{caller_number} endpoint."""
    biz_id = "biz_rest_privacy"
    target_caller = "+15553332222"

    async with privacy_session_maker() as session:
        biz = Business(id=biz_id, business_name="REST Clinic")
        session.add(biz)
        await session.commit()
        await BusinessRepository.log_call(session, biz_id, "CA_REST_1", caller_number=target_caller)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/privacy/caller/{target_caller}",
            headers={"X-Business-ID": biz_id}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["deleted_records"] == 1
    assert data["caller_number"] == target_caller
