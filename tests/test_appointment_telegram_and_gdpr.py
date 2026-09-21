"""Tests for Appointment Telegram Handlers and GDPR Cascade Deletion.

Verifies Phase 9 requirements:
1. /appointments command lists today's appointments in correct Markdown format.
2. /appointments with date arg filters to the specified date.
3. /appointments with no bookings shows empty-state message.
4. /digest generates and sends morning briefing to the chat.
5. /cancelappointment marks an appointment as CANCELLED and replies correctly.
6. /cancelappointment with invalid ID returns error message.
7. /deletecaller cascades deletion across CallLogs AND Appointments.
"""

import pytest
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.database.base import Base
import src.database.session as db_session_module
from src.services.db_business_repository import BusinessRepository
from src.database.models.business import CallLog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_maker():
    """Isolated in-memory SQLite session for handler tests."""
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


def make_update(chat_id: int, args=None, text=""):
    """Build a minimal Telegram Update mock."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    update.message.text = text
    ctx = MagicMock()
    ctx.args = args or []
    return update, ctx


# ---------------------------------------------------------------------------
# /appointments Command Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_appointments_command_empty(db_maker):
    """/appointments with no bookings returns empty state message."""
    from src.bot.handlers.appointments import appointments_command

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_99001")

    update, ctx = make_update(99001)
    with patch("src.bot.handlers.appointments.db_session_module.get_session_maker", return_value=db_maker):
        await appointments_command(update, ctx)

    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "No appointments" in text


@pytest.mark.asyncio
async def test_appointments_command_with_bookings(db_maker):
    """/appointments lists appointments with token numbers and status icons."""
    from src.bot.handlers.appointments import appointments_command

    target_date = (datetime.now(timezone.utc) + timedelta(days=2)).date()
    slot_time = datetime.combine(target_date, time(9, 0)).replace(tzinfo=timezone.utc)

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_99002")
        await BusinessRepository.create_appointment(
            session, "biz_99002", "+1234567890", slot_time,
            customer_name="Sarah Jenkins", status="CONFIRMED", token_number=1
        )

    update, ctx = make_update(99002, args=[target_date.strftime("%Y-%m-%d")])
    with patch("src.bot.handlers.appointments.db_session_module.get_session_maker", return_value=db_maker):
        await appointments_command(update, ctx)

    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Sarah Jenkins" in text or "+1234567890" in text
    assert "#01" in text


@pytest.mark.asyncio
async def test_appointments_command_invalid_date(db_maker):
    """/appointments with malformed date arg returns usage error."""
    from src.bot.handlers.appointments import appointments_command

    update, ctx = make_update(99003, args=["not-a-date"])
    await appointments_command(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    assert "Invalid date" in text or "YYYY-MM-DD" in text


# ---------------------------------------------------------------------------
# /digest Command Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_digest_command_generates_briefing(db_maker):
    """/digest returns morning clinic briefing for today."""
    from src.bot.handlers.appointments import digest_command

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_99010")

    update, ctx = make_update(99010)
    with patch("src.bot.handlers.appointments.morning_digest_service") as mock_digest:
        mock_digest.generate_digest = AsyncMock(return_value="🌅 *Morning Clinic Briefing*\nNo appointments scheduled.")
        await digest_command(update, ctx)

    # Two calls: "Generating..." + actual digest
    assert update.message.reply_text.call_count == 2
    final_text = update.message.reply_text.call_args_list[1][0][0]
    assert "Morning Clinic Briefing" in final_text


# ---------------------------------------------------------------------------
# /cancelappointment Command Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_appointment_success(db_maker):
    """/cancelappointment marks an existing appointment as CANCELLED."""
    from src.bot.handlers.appointments import cancel_appointment_command

    target_date = (datetime.now(timezone.utc) + timedelta(days=3)).date()
    slot_time = datetime.combine(target_date, time(14, 0)).replace(tzinfo=timezone.utc)

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_99020")
        appt = await BusinessRepository.create_appointment(
            session, "biz_99020", "+9998887777", slot_time,
            customer_name="Mark Davis", status="CONFIRMED", token_number=3
        )
        appt_id = appt.id

    update, ctx = make_update(99020, args=[str(appt_id)])
    with patch("src.bot.handlers.appointments.db_session_module.get_session_maker", return_value=db_maker):
        await cancel_appointment_command(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    assert "Cancelled" in text or "cancelled" in text
    assert str(appt_id) in text

    # Verify in DB
    async with db_maker() as session:
        cancelled = await BusinessRepository.get_appointment(session, "biz_99020", appt_id)
    assert cancelled.status == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_appointment_not_found(db_maker):
    """/cancelappointment with a non-existent ID returns not-found error."""
    from src.bot.handlers.appointments import cancel_appointment_command

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_99021")

    update, ctx = make_update(99021, args=["99999"])
    with patch("src.bot.handlers.appointments.db_session_module.get_session_maker", return_value=db_maker):
        await cancel_appointment_command(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    assert "not found" in text.lower() or "99999" in text


@pytest.mark.asyncio
async def test_cancel_appointment_no_args(db_maker):
    """/cancelappointment with no args returns usage help."""
    from src.bot.handlers.appointments import cancel_appointment_command

    update, ctx = make_update(99022, args=[])
    await cancel_appointment_command(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    assert "Usage" in text or "appointment_id" in text


# ---------------------------------------------------------------------------
# GDPR /deletecaller Cascade to Appointments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_caller_cascades_to_appointments(db_maker):
    """delete_caller_records removes both call logs and appointments for the caller."""
    caller_phone = "+44123456789"
    target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    slot_time = datetime.combine(target_date, time(10, 0)).replace(tzinfo=timezone.utc)

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_gdpr_appt")
        # Create appointment for this caller
        await BusinessRepository.create_appointment(
            session, "biz_gdpr_appt", caller_phone, slot_time,
            customer_name="Test Patient", status="CONFIRMED"
        )
        # Create a call log for this caller
        call_log = CallLog(
            business_id="biz_gdpr_appt",
            call_sid="CA_gdpr_001",
            caller_number=caller_phone,
            duration_seconds=120,
            transcript="Test transcript",
            summary="Test summary"
        )
        session.add(call_log)
        await session.commit()

    # Run GDPR deletion
    async with db_maker() as session:
        result = await BusinessRepository.delete_caller_records(session, "biz_gdpr_appt", caller_phone)

    assert result["success"] is True
    assert result["deleted_call_logs"] == 1
    assert result["deleted_appointments"] == 1

    # Verify nothing remains
    async with db_maker() as session:
        remaining_appts = await BusinessRepository.get_appointments_by_date(
            session, "biz_gdpr_appt", target_date
        )
    assert len(remaining_appts) == 0


@pytest.mark.asyncio
async def test_delete_caller_tenant_scoped(db_maker):
    """GDPR deletion for business A cannot delete appointments belonging to business B."""
    caller = "+19001234567"
    target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    slot_time = datetime.combine(target_date, time(11, 0)).replace(tzinfo=timezone.utc)

    async with db_maker() as session:
        await BusinessRepository.get_or_create_business(session, "biz_gdpr_biz_a")
        await BusinessRepository.get_or_create_business(session, "biz_gdpr_biz_b")
        # Both businesses have an appointment for the same caller
        await BusinessRepository.create_appointment(
            session, "biz_gdpr_biz_a", caller, slot_time, status="CONFIRMED"
        )
        await BusinessRepository.create_appointment(
            session, "biz_gdpr_biz_b", caller, slot_time, status="CONFIRMED"
        )

    # Delete only for biz_a
    async with db_maker() as session:
        result = await BusinessRepository.delete_caller_records(session, "biz_gdpr_biz_a", caller)

    assert result["deleted_appointments"] == 1

    # biz_b appointment must still exist
    async with db_maker() as session:
        biz_b_appts = await BusinessRepository.get_appointments_by_date(
            session, "biz_gdpr_biz_b", target_date
        )
    assert len(biz_b_appts) == 1
