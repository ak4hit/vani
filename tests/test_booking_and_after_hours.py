"""Tests for BookingService end-to-end flow, After-Hours intake, and Morning Digest.

Verifies Phase 9 requirements:
1. book_appointment creates a DB record with correct status, token, and doctor_id.
2. After-hours bookings receive AFTER_HOURS_PENDING_REVIEW status.
3. Business-hours bookings receive CONFIRMED status.
4. Slot hold is released after confirmed booking.
5. SMS confirmation is dispatched on booking.
6. Telegram notification is dispatched to business chat_id on booking.
7. MorningDigestService generates correct clinic briefing with AFTER_HOURS bookings.
8. Morning digest dispatches to Telegram via send_morning_digest.
"""

import pytest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.database.base import Base
import src.database.session as db_session_module
from src.services.booking_service import BookingService, MorningDigestService
from src.services.calendar_service import LocalDatabaseCalendarAdapter
from src.services.slot_lock_service import SlotLockManager
from src.services.token_service import TokenService
from src.services.sms_service import SMSService
from src.services.db_business_repository import BusinessRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_maker():
    """Isolated in-memory SQLite database for booking tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    with patch.object(db_session_module, "get_session_maker", return_value=sm):
        yield sm
    await engine.dispose()


def _make_booking_service(db_maker):
    """Build a BookingService with mocked externals and in-memory backends."""
    lock_mgr = SlotLockManager(redis_client=None)
    token_mgr = TokenService(redis_client=None)
    sms_client = SMSService(account_sid=None, auth_token=None, from_phone=None)

    local_cal = LocalDatabaseCalendarAdapter(session_maker=db_maker)

    svc = BookingService(
        cal_adapter=local_cal,
        lock_mgr=lock_mgr,
        token_mgr=token_mgr,
        sms_client=sms_client,
        session_maker=db_maker,
    )
    return svc, lock_mgr, token_mgr, sms_client


# ---------------------------------------------------------------------------
# Booking Flow Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_book_appointment_confirmed_status(db_maker):
    """Business-hours booking creates CONFIRMED appointment in DB."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_book_a", chat_id=None)

    svc, _, _, sms_mock = _make_booking_service(db_maker)
    slot_time = datetime.combine(
        (datetime.now(timezone.utc) + timedelta(days=5)).date(), time(10, 0)
    ).replace(tzinfo=timezone.utc)

    with patch.object(svc, "is_after_hours", return_value=False):
        with patch("src.services.booking_service.notification_service") as mock_notif:
            mock_notif.send_telegram_message = AsyncMock(return_value=True)
            appt = await svc.book_appointment(
                business_id="biz_book_a",
                caller_phone="+10001112222",
                slot_time=slot_time,
                call_sid="CA_test_001",
                customer_name="Jane Doe",
                doctor_id="dr_smith",
            )

    assert appt.id is not None
    assert appt.status == "CONFIRMED"
    assert appt.doctor_id == "dr_smith"
    assert appt.caller_phone == "+10001112222"
    assert appt.token_number == 1


@pytest.mark.asyncio
async def test_book_appointment_after_hours_status(db_maker):
    """After-hours booking creates AFTER_HOURS_PENDING_REVIEW appointment."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_book_b", chat_id=None)

    svc, _, _, _ = _make_booking_service(db_maker)
    slot_time = datetime.combine(
        (datetime.now(timezone.utc) + timedelta(days=2)).date(), time(22, 0)
    ).replace(tzinfo=timezone.utc)

    with patch("src.services.booking_service.notification_service") as mock_notif:
        mock_notif.send_telegram_message = AsyncMock(return_value=True)
        appt = await svc.book_appointment(
            business_id="biz_book_b",
            caller_phone="+19998887777",
            slot_time=slot_time,
            force_after_hours=True,
        )

    assert appt.status == "AFTER_HOURS_PENDING_REVIEW"


@pytest.mark.asyncio
async def test_book_appointment_generates_sequential_tokens(db_maker):
    """Multiple bookings for same business on same day get sequential tokens."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_tokens", chat_id=None)

    svc, _, _, _ = _make_booking_service(db_maker)
    target_date = (datetime.now(timezone.utc) + timedelta(days=4)).date()
    base_time = datetime.combine(target_date, time(9, 0)).replace(tzinfo=timezone.utc)

    with patch("src.services.booking_service.notification_service") as mock_notif:
        mock_notif.send_telegram_message = AsyncMock(return_value=True)
        appt1 = await svc.book_appointment("biz_tokens", "+1111", base_time, force_after_hours=False)
        appt2 = await svc.book_appointment("biz_tokens", "+2222", base_time + timedelta(minutes=30), force_after_hours=False)
        appt3 = await svc.book_appointment("biz_tokens", "+3333", base_time + timedelta(minutes=60), force_after_hours=False)

    assert appt1.token_number == 1
    assert appt2.token_number == 2
    assert appt3.token_number == 3


@pytest.mark.asyncio
async def test_book_appointment_releases_slot_lock(db_maker):
    """Slot lock is confirmed (released) after appointment is persisted."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_lock_release", chat_id=None)

    svc, lock_mgr, _, _ = _make_booking_service(db_maker)
    target_date = (datetime.now(timezone.utc) + timedelta(days=6)).date()
    slot_time = datetime.combine(target_date, time(11, 0)).replace(tzinfo=timezone.utc)
    slot_iso = slot_time.isoformat()

    # Pre-acquire slot hold
    await lock_mgr.acquire_temporary_hold("biz_lock_release", "primary", slot_iso, "CA_release_test")
    assert await lock_mgr.is_slot_locked("biz_lock_release", "primary", slot_iso) is True

    with patch("src.services.booking_service.notification_service") as mock_notif:
        mock_notif.send_telegram_message = AsyncMock(return_value=True)
        await svc.book_appointment(
            business_id="biz_lock_release",
            caller_phone="+5554443333",
            slot_time=slot_time,
            call_sid="CA_release_test",
            force_after_hours=False,
        )

    # Lock should now be released
    assert await lock_mgr.is_slot_locked("biz_lock_release", "primary", slot_iso) is False


@pytest.mark.asyncio
async def test_book_appointment_dispatches_sms(db_maker):
    """SMS confirmation is dispatched for every successful booking."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_sms_test", chat_id=None)

    svc, _, _, sms_client = _make_booking_service(db_maker)
    slot_time = datetime.combine(
        (datetime.now(timezone.utc) + timedelta(days=1)).date(), time(14, 0)
    ).replace(tzinfo=timezone.utc)

    with patch("src.services.booking_service.notification_service") as mock_notif:
        mock_notif.send_telegram_message = AsyncMock(return_value=True)
        await svc.book_appointment(
            business_id="biz_sms_test",
            caller_phone="+441234567890",
            slot_time=slot_time,
            force_after_hours=False,
        )

    assert len(sms_client.dispatched_messages) == 1
    assert sms_client.dispatched_messages[0]["to"] == "+441234567890"


@pytest.mark.asyncio
async def test_book_appointment_sends_telegram_when_chat_id_set(db_maker):
    """Telegram notification is sent to business chat_id after a confirmed booking."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_tg_notify", chat_id=99001)

    svc, _, _, _ = _make_booking_service(db_maker)
    slot_time = datetime.combine(
        (datetime.now(timezone.utc) + timedelta(days=2)).date(), time(13, 0)
    ).replace(tzinfo=timezone.utc)

    with patch("src.services.booking_service.notification_service") as mock_notif:
        mock_notif.send_telegram_message = AsyncMock(return_value=True)
        await svc.book_appointment(
            business_id="biz_tg_notify",
            caller_phone="+1555000111",
            slot_time=slot_time,
            force_after_hours=False,
        )
        mock_notif.send_telegram_message.assert_called_once()
        call_args = mock_notif.send_telegram_message.call_args
        assert call_args[0][0] == 99001


# ---------------------------------------------------------------------------
# is_after_hours Tests
# ---------------------------------------------------------------------------

def test_is_after_hours_evening():
    """20:00 UTC is after hours."""
    evening = datetime(2026, 10, 1, 20, 0, 0, tzinfo=timezone.utc)
    assert BookingService.is_after_hours(evening) is True


def test_is_after_hours_early_morning():
    """03:00 UTC is after hours."""
    early = datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc)
    assert BookingService.is_after_hours(early) is True


def test_is_after_hours_midday():
    """12:00 UTC is business hours."""
    midday = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert BookingService.is_after_hours(midday) is False


# ---------------------------------------------------------------------------
# Morning Digest Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_morning_digest_includes_all_statuses(db_maker):
    """Digest includes both CONFIRMED and AFTER_HOURS_PENDING_REVIEW bookings."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_digest", chat_id=55001)
        target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        slot1 = datetime.combine(target_date, time(9, 0)).replace(tzinfo=timezone.utc)
        slot2 = datetime.combine(target_date, time(10, 0)).replace(tzinfo=timezone.utc)

        await BusinessRepository.create_appointment(
            session, "biz_digest", "+1111111111", slot1,
            customer_name="Alice", status="CONFIRMED", token_number=1
        )
        await BusinessRepository.create_appointment(
            session, "biz_digest", "+2222222222", slot2,
            customer_name="Bob", status="AFTER_HOURS_PENDING_REVIEW", token_number=2
        )

    digest_svc = MorningDigestService(session_maker=db_maker)
    digest_text = await digest_svc.generate_digest("biz_digest", target_date)

    assert "Alice" in digest_text or "+1111111111" in digest_text
    assert "Bob" in digest_text or "+2222222222" in digest_text
    assert "Pending Review" in digest_text or "AFTER_HOURS" in digest_text
    assert "#01" in digest_text
    assert "#02" in digest_text


@pytest.mark.asyncio
async def test_morning_digest_empty_day(db_maker):
    """Digest for a day with no appointments states no appointments scheduled."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_empty_day", chat_id=None)

    digest_svc = MorningDigestService(session_maker=db_maker)
    future_date = (datetime.now(timezone.utc) + timedelta(days=30)).date()
    digest_text = await digest_svc.generate_digest("biz_empty_day", future_date)

    assert "No appointments" in digest_text


@pytest.mark.asyncio
async def test_send_morning_digest_calls_telegram(db_maker):
    """send_morning_digest dispatches digest text to Telegram chat."""
    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_digest_send", chat_id=None)

    digest_svc = MorningDigestService(session_maker=db_maker)

    with patch("src.services.booking_service.notification_service") as mock_notif:
        mock_notif.send_telegram_message = AsyncMock(return_value=True)
        result = await digest_svc.send_morning_digest("biz_digest_send", chat_id=55002)
        assert result is True
        mock_notif.send_telegram_message.assert_called_once()
        call_args = mock_notif.send_telegram_message.call_args
        assert call_args[0][0] == 55002
        assert "Morning" in call_args[0][1] or "Briefing" in call_args[0][1]
