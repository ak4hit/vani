"""Appointment Admin Handlers - Telegram commands for appointment management.

Provides /appointments, /digest, and /cancelappointment commands for business owners
to view, manage, and digest scheduled appointments from Phase 9.
"""

from datetime import date, datetime, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.rate_limiter import rate_limited
from src.config import settings
from src.services.booking_service import booking_service, morning_digest_service
from src.services.business_store import business_store
from src.services.token_service import TokenService
from src.utils.logger import get_logger

import src.database.session as db_session_module
from src.services.db_business_repository import BusinessRepository

logger = get_logger("vani.bot.appointments")


def _get_business_id(chat_id: int) -> str:
    """Derive business_id from Telegram chat_id."""
    return f"biz_{chat_id}"


@rate_limited()
async def appointments_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/appointments [YYYY-MM-DD] — List all appointments for today or a specified date."""
    chat_id = update.effective_chat.id
    business_id = _get_business_id(chat_id)

    target_date: Optional[date] = None
    if context.args:
        try:
            target_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid date format. Use `/appointments YYYY-MM-DD` or `/appointments` for today.",
                parse_mode="Markdown"
            )
            return

    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    date_display = target_date.strftime("%A, %b %d, %Y")

    session_maker = db_session_module.get_session_maker()
    async with session_maker() as session:
        biz = await BusinessRepository.get_business(session, business_id)
        biz_name = biz.business_name if biz else "Clinic"
        appts = await BusinessRepository.get_appointments_by_date(
            session=session,
            business_id=business_id,
            target_date=target_date
        )

    if not appts:
        await update.message.reply_text(
            f"📅 No appointments found for *{biz_name}* on *{date_display}*.\n\n"
            "Tip: Book appointments by calling your Vani number.",
            parse_mode="Markdown"
        )
        return

    lines = [f"📅 *Appointments — {biz_name}*", f"📆 *{date_display}* ({len(appts)} bookings)\n"]
    for appt in appts:
        t_str = appt.slot_time.strftime("%I:%M %p")
        tok_str = TokenService.format_token_string(appt.token_number) if appt.token_number else "N/A"
        status_icon = "✅" if appt.status == "CONFIRMED" else "🌙" if appt.status == "AFTER_HOURS_PENDING_REVIEW" else "❌"
        patient = appt.customer_name or appt.caller_phone
        line = f"{status_icon} `{t_str}` — *{patient}* | Dr. `{appt.doctor_id}` | Token {tok_str} | ID: `{appt.id}`"
        lines.append(line)

    lines.append("\n_Use /cancelappointment <id> to cancel an appointment._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@rate_limited()
async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/digest — Generate and display the Morning Clinic Briefing digest for today."""
    chat_id = update.effective_chat.id
    business_id = _get_business_id(chat_id)

    await update.message.reply_text("⏳ Generating your morning clinic briefing digest...", parse_mode="Markdown")

    target_date: Optional[date] = None
    if context.args:
        try:
            target_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
        except ValueError:
            pass

    digest_text = await morning_digest_service.generate_digest(business_id, target_date)
    await update.message.reply_text(digest_text, parse_mode="Markdown")


@rate_limited()
async def cancel_appointment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cancelappointment <id> — Cancel a specific appointment by its ID."""
    chat_id = update.effective_chat.id
    business_id = _get_business_id(chat_id)

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/cancelappointment <appointment_id>`\n"
            "Example: `/cancelappointment 12`",
            parse_mode="Markdown"
        )
        return

    try:
        appointment_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Appointment ID must be a number.", parse_mode="Markdown")
        return

    session_maker = db_session_module.get_session_maker()
    async with session_maker() as session:
        appt = await BusinessRepository.cancel_appointment(session, business_id, appointment_id)

    if not appt:
        await update.message.reply_text(
            f"❌ Appointment ID `{appointment_id}` not found for your business.",
            parse_mode="Markdown"
        )
        return

    t_str = appt.slot_time.strftime("%b %d, %Y at %I:%M %p")
    patient = appt.customer_name or appt.caller_phone
    await update.message.reply_text(
        f"✅ *Appointment Cancelled*\n\n"
        f"• ID: `{appt.id}`\n"
        f"• Patient: `{patient}`\n"
        f"• Time: `{t_str}`\n"
        f"• Provider: `{appt.doctor_id}`\n\n"
        "The patient has been removed from the schedule.",
        parse_mode="Markdown"
    )
    logger.info(f"Appointment {appointment_id} cancelled by admin chat_id={chat_id}")
