"""Tests for TokenService and CalendarAdapter.

Verifies Phase 9 requirements:
1. Atomic daily token generation returns sequential integers starting from 1.
2. Token formatting produces zero-padded strings (#01, #14, #100).
3. Different business_id/date combinations produce independent counters.
4. LocalDatabaseCalendarAdapter returns available 30-min slots in clinic hours.
5. Booked slots are excluded from available slots.
6. Locked slots (via SlotLockManager) are excluded from available slots.
7. GoogleCalendarAdapter falls back to local adapter when credentials absent.
"""

import pytest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.database.base import Base
import src.database.session as db_session_module
from src.services.token_service import TokenService
from src.services.calendar_service import (
    LocalDatabaseCalendarAdapter,
    GoogleCalendarAdapter,
)
from src.services.slot_lock_service import SlotLockManager
from src.services.db_business_repository import BusinessRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session_maker():
    """Isolated in-memory SQLite session for calendar tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    with patch.object(db_session_module, "get_session_maker", return_value=session_maker):
        yield session_maker
    await engine.dispose()


@pytest.fixture
def token_svc():
    """Fresh in-memory TokenService."""
    svc = TokenService(redis_client=None)
    return svc


@pytest.fixture
def lock_mgr():
    return SlotLockManager(redis_client=None)


# ---------------------------------------------------------------------------
# Token Service Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_sequential_generation(token_svc):
    """Tokens increment atomically for the same business and date."""
    t1 = await token_svc.generate_daily_token("biz_a", "2026-10-01")
    t2 = await token_svc.generate_daily_token("biz_a", "2026-10-01")
    t3 = await token_svc.generate_daily_token("biz_a", "2026-10-01")
    assert t1 == 1
    assert t2 == 2
    assert t3 == 3


@pytest.mark.asyncio
async def test_token_isolated_by_business(token_svc):
    """Different businesses have independent token counters."""
    await token_svc.generate_daily_token("biz_clinic_a", "2026-10-01")
    await token_svc.generate_daily_token("biz_clinic_a", "2026-10-01")
    t_b = await token_svc.generate_daily_token("biz_clinic_b", "2026-10-01")
    assert t_b == 1


@pytest.mark.asyncio
async def test_token_isolated_by_date(token_svc):
    """Different dates have independent token counters per business."""
    await token_svc.generate_daily_token("biz_a", "2026-10-01")
    await token_svc.generate_daily_token("biz_a", "2026-10-01")
    t_day2 = await token_svc.generate_daily_token("biz_a", "2026-10-02")
    assert t_day2 == 1


def test_format_token_single_digit():
    """Single-digit tokens are zero-padded."""
    assert TokenService.format_token_string(1) == "#01"
    assert TokenService.format_token_string(9) == "#09"


def test_format_token_double_digit():
    """Double-digit tokens format without zero-padding."""
    assert TokenService.format_token_string(14) == "#14"
    assert TokenService.format_token_string(100) == "#100"


@pytest.mark.asyncio
async def test_token_reset_clears_counters(token_svc):
    """Reset wipes all in-memory counters (test isolation)."""
    await token_svc.generate_daily_token("biz_a", "2026-10-01")
    token_svc.reset()
    t_after_reset = await token_svc.generate_daily_token("biz_a", "2026-10-01")
    assert t_after_reset == 1


# ---------------------------------------------------------------------------
# Calendar Adapter Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_adapter_returns_slots_in_clinic_hours(db_session_maker):
    """Available slots are generated between 9 AM and 5 PM in 30-min intervals."""
    adapter = LocalDatabaseCalendarAdapter(session_maker=db_session_maker)
    future_date = (datetime.now(timezone.utc) + timedelta(days=7)).date()
    slots = await adapter.get_available_slots("biz_test", "primary", future_date)

    # 9:00 AM to 5:00 PM = 16 half-hour slots
    assert len(slots) == 16
    assert all(isinstance(s, datetime) for s in slots)
    # All slots within clinic hours
    for s in slots:
        assert s.hour >= 9
        assert s.hour < 17


@pytest.mark.asyncio
async def test_local_adapter_excludes_booked_slots(db_session_maker):
    """Slots already booked in DB are excluded from available list."""
    adapter = LocalDatabaseCalendarAdapter(session_maker=db_session_maker)
    target_date = (datetime.now(timezone.utc) + timedelta(days=5)).date()
    slot_time = datetime.combine(target_date, time(9, 0)).replace(tzinfo=timezone.utc)

    # Create a business and book the 9:00 AM slot
    async with db_session_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_cal_book")
        await BusinessRepository.create_appointment(
            session=session,
            business_id="biz_cal_book",
            caller_phone="+1234567890",
            slot_time=slot_time,
            doctor_id="primary",
            status="CONFIRMED"
        )

    slots = await adapter.get_available_slots("biz_cal_book", "primary", target_date)
    slot_times = [s.replace(second=0, microsecond=0) for s in slots]
    booked_slot = slot_time.replace(second=0, microsecond=0)
    assert booked_slot not in slot_times
    assert len(slots) == 15  # 16 total minus 1 booked


@pytest.mark.asyncio
async def test_local_adapter_excludes_locked_slots(db_session_maker, lock_mgr):
    """Slots locked by SlotLockManager are excluded from available list."""
    target_date = (datetime.now(timezone.utc) + timedelta(days=3)).date()
    slot_time = datetime.combine(target_date, time(10, 0)).replace(tzinfo=timezone.utc)
    slot_iso = slot_time.isoformat()

    # Acquire an active lock
    await lock_mgr.acquire_temporary_hold("biz_locked", "primary", slot_iso, "CA_lock_test")

    # Patch slot_lock_manager in the calendar service module
    with patch("src.services.calendar_service.slot_lock_manager", lock_mgr):
        adapter = LocalDatabaseCalendarAdapter(session_maker=db_session_maker)
        slots = await adapter.get_available_slots("biz_locked", "primary", target_date)

    slot_times_utc = [s.astimezone(timezone.utc).replace(second=0, microsecond=0) for s in slots]
    assert slot_time.replace(second=0, microsecond=0) not in slot_times_utc


@pytest.mark.asyncio
async def test_google_adapter_falls_back_to_local(db_session_maker):
    """GoogleCalendarAdapter without credentials delegates to local DB adapter."""
    local = LocalDatabaseCalendarAdapter(session_maker=db_session_maker)
    google_adapter = GoogleCalendarAdapter(google_credentials=None, fallback_adapter=local)
    target_date = (datetime.now(timezone.utc) + timedelta(days=10)).date()
    slots = await google_adapter.get_available_slots("biz_goog", "primary", target_date)
    assert isinstance(slots, list)
    assert len(slots) == 16
