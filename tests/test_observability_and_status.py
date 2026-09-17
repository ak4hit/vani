"""Unit and Integration Tests for Health Monitoring, Error Ring Buffer, and /status Dashboard.

Verifies Phase 8 Half 1 requirements:
1. Active call tracking registers and unregisters concurrent calls.
2. Error ring buffer records recent errors with timestamps.
3. System diagnostics inspects DB latency, cache status, API keys, and TTS quota.
4. /status command delivers rich real-time observability telemetry via Telegram.
5. /api/status endpoint exposes health diagnostics for external monitoring.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.database.base import Base
import src.database.session as db_session_module
from src.services.business_store import business_store
from src.services.health_monitor import (
    ActiveCallTracker,
    ErrorTracker,
    HealthMonitor,
    health_monitor,
)
from src.bot.handlers.control import status_command
from src.main import app


@pytest.fixture
async def health_session_maker():
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


def test_active_call_tracker():
    """Verify thread-safe active call registration and count management."""
    tracker = ActiveCallTracker()
    assert tracker.get_active_count() == 0

    tracker.register_call("CA_001")
    tracker.register_call("CA_002")
    assert tracker.get_active_count() == 2
    assert set(tracker.get_active_calls()) == {"CA_001", "CA_002"}

    tracker.unregister_call("CA_001")
    assert tracker.get_active_count() == 1
    assert tracker.get_active_calls() == ["CA_002"]

    tracker.clear()
    assert tracker.get_active_count() == 0


def test_error_ring_buffer():
    """Verify circular ring buffer captures errors with timestamps and respects max capacity."""
    tracker = ErrorTracker(max_size=3)
    tracker.clear()

    tracker.record_error("deepgram", "WebSocket timeout", call_sid="CA_101")
    tracker.record_error("gemini", "Rate limit exceeded", call_sid="CA_102")
    tracker.record_error("elevenlabs", "Quota 429", call_sid="CA_103")

    errors = tracker.get_recent_errors(limit=5)
    assert len(errors) == 3
    assert errors[0]["service"] == "elevenlabs"
    assert errors[0]["call_sid"] == "CA_103"
    assert errors[2]["service"] == "deepgram"

    # Add 4th error to overflow buffer (max 3)
    tracker.record_error("twilio", "Media frame dropped", call_sid="CA_104")
    errors_after = tracker.get_recent_errors(limit=5)
    assert len(errors_after) == 3
    assert errors_after[0]["service"] == "twilio"
    # Oldest ('deepgram') was evicted
    assert not any(e["service"] == "deepgram" for e in errors_after)


@pytest.mark.asyncio
async def test_health_monitor_system_health(health_session_maker):
    """Verify health monitor aggregates diagnostics from all subsystems."""
    monitor = HealthMonitor()
    monitor.call_tracker.register_call("CA_HEALTH_1")
    monitor.error_tracker.record_error("test_service", "Sample diagnostic error")

    health = await monitor.get_system_health()

    assert health["status"] in ("healthy", "degraded")
    assert health["active_calls"] == 1
    assert health["database"]["status"] == "connected"
    assert health["database"]["latency_ms"] is not None
    assert health["redis"]["status"] == "active"
    assert "deepgram_stt" in health["api_keys"]
    assert health["tts_quota"]["status"] in ("healthy", "mock_mode")
    assert len(health["recent_errors"]) == 1
    assert health["recent_errors"][0]["service"] == "test_service"


@pytest.mark.asyncio
async def test_health_monitor_telegram_dashboard(health_session_maker):
    """Verify formatted Telegram status dashboard markdown content."""
    business_store.reset()
    biz = business_store.get_or_create_business("biz_obs", chat_id=123987)
    business_store.update_profile("biz_obs", business_name="Apex Care", agent_name="Vani")

    monitor = HealthMonitor()
    monitor.call_tracker.register_call("CA_DASH_1")
    monitor.error_tracker.record_error("tts", "Quota warning")

    card = await monitor.format_telegram_dashboard(biz)

    assert "Vani Real-Time Observability Dashboard" in card
    assert "Apex Care" in card
    assert "Live Calls" in card
    assert "`1` concurrent calls active" in card
    assert "Database" in card
    assert "Last 5 Errors" in card
    assert "Quota warning" in card


@pytest.mark.asyncio
async def test_telegram_status_command_dispatches_dashboard(health_session_maker):
    """Verify /status command replies with the real-time observability card."""
    business_store.reset()
    business_store.get_or_create_business("biz_cmd_status", chat_id=445566)

    update = MagicMock()
    update.effective_chat.id = 445566
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await status_command(update, context)

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Vani Real-Time Observability Dashboard" in reply
    assert "Database:" in reply


@pytest.mark.asyncio
async def test_api_status_endpoint(health_session_maker):
    """Verify GET /api/status HTTP endpoint returns complete diagnostics JSON."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "database" in data
    assert "redis" in data
    assert "active_calls" in data
    assert "api_keys" in data
    assert "recent_errors" in data
