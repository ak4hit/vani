"""Appointment Booking Orchestrator & After-Hours Intake Service.

Integrates distributed slot locking, daily token numbering, calendar availability,
SMS confirmation, and automated morning briefing digests
(MASTERPLAN.md Phase 9 & UPGRADE_ADDENDUM.md Sections 2, 5, 7).

NOTE: notification_service.send_telegram_message is the correct public method.
"""

from datetime import date, datetime, time, timezone
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

import src.database.session as db_session_module
from src.database.models.appointment import Appointment
from src.services.calendar_service import CalendarAdapter, calendar_adapter
from src.services.db_business_repository import BusinessRepository
from src.services.slot_lock_service import slot_lock_manager, SlotLockManager
from src.services.sms_service import sms_service, SMSService
from src.services.token_service import token_service, TokenService
from src.services.notification_service import notification_service
from src.utils.logger import get_logger

logger = get_logger("vani.booking_service")


class BookingService:
    """Orchestrates appointment availability, distributed slot holds, and booking lifecycle."""

    def __init__(
        self,
        cal_adapter: Optional[CalendarAdapter] = None,
        lock_mgr: Optional[SlotLockManager] = None,
        token_mgr: Optional[TokenService] = None,
        sms_client: Optional[SMSService] = None,
        session_maker=None
    ):
        self.calendar = cal_adapter or calendar_adapter
        self.locks = lock_mgr or slot_lock_manager
        self.tokens = token_mgr or token_service
        self.sms = sms_client or sms_service
        self._session_maker = session_maker

    def _get_session_maker(self):
        return self._session_maker or db_session_module.get_session_maker()

    async def get_available_slots(
        self,
        business_id: str,
        doctor_id: str = "primary",
        target_date: Optional[date] = None
    ) -> List[datetime]:
        """Queries free, unlocked slots for a specific date."""
        return await self.calendar.get_available_slots(
            business_id=business_id,
            doctor_id=doctor_id,
            target_date=target_date
        )

    async def hold_slot(
        self,
        business_id: str,
        doctor_id: str,
        slot_time: datetime,
        call_sid: str,
        ttl_seconds: int = 180
    ) -> bool:
        """Acquires a temporary distributed lock on a slot during a call."""
        slot_iso = slot_time.isoformat()
        return await self.locks.acquire_temporary_hold(
            business_id=business_id,
            doctor_id=doctor_id,
            slot_iso=slot_iso,
            call_sid=call_sid,
            ttl_seconds=ttl_seconds
        )

    async def release_slot(
        self,
        business_id: str,
        doctor_id: str,
        slot_time: datetime,
        call_sid: str
    ) -> bool:
        """Releases a temporary slot hold."""
        slot_iso = slot_time.isoformat()
        return await self.locks.release_hold(
            business_id=business_id,
            doctor_id=doctor_id,
            slot_iso=slot_iso,
            call_sid=call_sid
        )

    @staticmethod
    def is_after_hours(booking_time: Optional[datetime] = None) -> bool:
        """Evaluates whether booking occurred during after-hours (8:00 PM – 8:00 AM)."""
        now = booking_time or datetime.now(timezone.utc)
        hour = now.hour
        return hour >= 20 or hour < 8

    async def book_appointment(
        self,
        business_id: str,
        caller_phone: str,
        slot_time: datetime,
        call_sid: Optional[str] = None,
        customer_name: Optional[str] = None,
        doctor_id: str = "primary",
        duration_minutes: int = 30,
        notes: Optional[str] = None,
        force_after_hours: Optional[bool] = None
    ) -> Appointment:
        """Finalizes an appointment booking with token generation, SMS, and status lifecycle."""
        # 1. Determine status: after-hours review vs confirmed
        if force_after_hours is not None:
            after_hours = force_after_hours
        else:
            after_hours = self.is_after_hours()

        status = "AFTER_HOURS_PENDING_REVIEW" if after_hours else "CONFIRMED"

        # 2. Generate daily sequential token number
        date_str = slot_time.strftime("%Y-%m-%d")
        token_num = await self.tokens.generate_daily_token(business_id, date_str)

        # 3. Persist appointment in database
        session_maker = self._get_session_maker()
        async with session_maker() as session:
            # Fetch business details for SMS and notification
            biz = await BusinessRepository.get_business(session, business_id)
            biz_name = biz.business_name if biz else "Clinic Reception"
            biz_phone = biz.phone_number if biz else None
            chat_id = biz.chat_id if biz else None

            appt = await BusinessRepository.create_appointment(
                session=session,
                business_id=business_id,
                caller_phone=caller_phone,
                slot_time=slot_time,
                call_sid=call_sid,
                customer_name=customer_name,
                doctor_id=doctor_id,
                duration_minutes=duration_minutes,
                status=status,
                token_number=token_num,
                notes=notes
            )

        # 4. Release slot hold now that appointment is saved
        slot_iso = slot_time.isoformat()
        if call_sid:
            await self.locks.confirm_booking(business_id, doctor_id, slot_iso, call_sid)

        # 5. Dispatch confirmation SMS
        await self.sms.send_appointment_confirmation(
            to_phone=caller_phone,
            business_name=biz_name,
            provider_name=doctor_id,
            slot_time=slot_time,
            token_number=token_num,
            clinic_phone=biz_phone
        )

        # 6. Send instant notification to business Telegram chat if configured
        if chat_id:
            token_str = TokenService.format_token_string(token_num)
            badge = "🌙 [AFTER-HOURS PENDING REVIEW]" if after_hours else "✅ [CONFIRMED]"
            msg = (
                f"{badge} *New Appointment Booked*\n\n"
                f"👤 *Patient:* `{customer_name or 'Caller'}` ({caller_phone})\n"
                f"🩺 *Provider:* `{doctor_id}`\n"
                f"📅 *Time:* `{slot_time.strftime('%b %d, %Y at %I:%M %p')}`\n"
                f"🔢 *Token:* `{token_str}`\n"
                f"📝 *Notes:* {notes or 'None'}\n"
            )
            await notification_service.send_telegram_message(chat_id, msg)

        logger.info(
            f"Booked appointment #{appt.id} for biz='{business_id}', caller='{caller_phone}', "
            f"token=#{token_num}, status='{status}'"
        )
        return appt


class MorningDigestService:
    """Generates the 8:00 AM Morning Clinic Briefing rollup digest for staff."""

    def __init__(self, session_maker=None):
        self._session_maker = session_maker

    def _get_session_maker(self):
        return self._session_maker or db_session_module.get_session_maker()

    async def generate_digest(
        self,
        business_id: str,
        target_date: Optional[date] = None
    ) -> str:
        """Compiles overnight bookings and activity into a structured markdown report."""
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        session_maker = self._get_session_maker()
        async with session_maker() as session:
            biz = await BusinessRepository.get_business(session, business_id)
            biz_name = biz.business_name if biz else "Clinic"

            # Fetch today's appointments
            today_appts = await BusinessRepository.get_appointments_by_date(
                session=session,
                business_id=business_id,
                target_date=target_date
            )

        overnight_count = sum(
            1 for a in today_appts if a.status == "AFTER_HOURS_PENDING_REVIEW"
        )
        total_booked = len(today_appts)
        date_str = target_date.strftime("%A, %b %d, %Y")

        lines = [
            f"🌅 *Morning Clinic Briefing — {date_str}*",
            f"🏥 *{biz_name}*",
            f"Overnight Intake: {overnight_count} Bookings Pending Review | Total Today: {total_booked}\n",
            "📅 *Schedule for Today:*"
        ]

        if not today_appts:
            lines.append("• No appointments scheduled for today.")
        else:
            for idx, appt in enumerate(today_appts, 1):
                t_str = appt.slot_time.strftime("%I:%M %p")
                tok_str = TokenService.format_token_string(appt.token_number) if appt.token_number else "N/A"
                caller_info = appt.customer_name or appt.caller_phone
                status_tag = "⚠️ [Pending Review]" if appt.status == "AFTER_HOURS_PENDING_REVIEW" else "✅ [Confirmed]"
                notes_part = f" • _{appt.notes}_" if appt.notes else ""
                lines.append(f"{idx}. `{t_str}` — {caller_info} ({appt.doctor_id}) • Token {tok_str} {status_tag}{notes_part}")

        lines.append("\n⚠️ *Action Items:*")
        if overnight_count > 0:
            lines.append(f"• Please review and confirm {overnight_count} overnight intake booking(s).")
        else:
            lines.append("• All morning bookings confirmed. Zero collision conflicts.")

        return "\n".join(lines)

    async def send_morning_digest(
        self,
        business_id: str,
        chat_id: int,
        target_date: Optional[date] = None
    ) -> bool:
        """Dispatches the generated morning briefing to the business's Telegram chat."""
        digest_text = await self.generate_digest(business_id, target_date)
        return await notification_service.send_telegram_message(chat_id, digest_text)


# Global singletons
booking_service = BookingService()
morning_digest_service = MorningDigestService()
