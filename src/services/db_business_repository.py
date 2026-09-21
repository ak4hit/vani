"""Database Business Repository - Multi-tenant repository enforcing strict business_id scoping."""

from datetime import datetime, date, timezone, time
from typing import List, Optional, Dict, Any
from sqlalchemy import select, update, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.business import Business, FAQ, CallLog
from src.database.models.voice_audit import VoiceConsentAudit
from src.database.models.appointment import Appointment
from src.utils.logger import get_logger

logger = get_logger("vani.db.repository")


class BusinessRepository:
    """Provides tenant-isolated persistence for businesses, FAQs, and call logs."""

    @staticmethod
    async def get_or_create_business(
        session: AsyncSession,
        business_id: str,
        chat_id: Optional[int] = None
    ) -> Business:
        """Fetch existing business or create a new tenant record."""
        stmt = select(Business).where(Business.id == business_id)
        result = await session.execute(stmt)
        biz = result.scalar_one_or_none()

        if not biz:
            biz = Business(id=business_id, chat_id=chat_id)
            session.add(biz)
            await session.commit()
            await session.refresh(biz)
            logger.info(f"Created new business tenant record id='{business_id}'")
        elif chat_id is not None and biz.chat_id != chat_id:
            biz.chat_id = chat_id
            await session.commit()
            await session.refresh(biz)

        return biz

    @staticmethod
    async def get_business(session: AsyncSession, business_id: str) -> Optional[Business]:
        """Fetch business by ID."""
        stmt = select(Business).where(Business.id == business_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_chat_id(session: AsyncSession, chat_id: int) -> Optional[Business]:
        """Fetch business associated with a Telegram chat_id."""
        stmt = select(Business).where(Business.chat_id == chat_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update_profile(
        session: AsyncSession,
        business_id: str,
        **kwargs
    ) -> Optional[Business]:
        """Update business profile fields scoped strictly to business_id."""
        biz = await BusinessRepository.get_business(session, business_id)
        if not biz:
            return None

        for key, val in kwargs.items():
            if hasattr(biz, key):
                setattr(biz, key, val)

        await session.commit()
        await session.refresh(biz)
        return biz

    @staticmethod
    async def set_paused(
        session: AsyncSession,
        business_id: str,
        is_paused: bool
    ) -> bool:
        """Set pause flag for business tenant."""
        biz = await BusinessRepository.get_business(session, business_id)
        if not biz:
            biz = await BusinessRepository.get_or_create_business(session, business_id)

        biz.is_paused = is_paused
        await session.commit()
        return biz.is_paused

    @staticmethod
    async def is_paused(session: AsyncSession, business_id: str) -> bool:
        """Check if business is paused."""
        stmt = select(Business.is_paused).where(Business.id == business_id)
        result = await session.execute(stmt)
        paused = result.scalar_one_or_none()
        return bool(paused) if paused is not None else False

    @staticmethod
    async def add_faq(
        session: AsyncSession,
        business_id: str,
        question: str,
        answer: str
    ) -> FAQ:
        """Add FAQ entry strictly bound to business_id."""
        faq = FAQ(
            business_id=business_id,
            question=question.strip(),
            answer=answer.strip()
        )
        session.add(faq)
        await session.commit()
        await session.refresh(faq)
        return faq

    @staticmethod
    async def list_faqs(session: AsyncSession, business_id: str) -> List[FAQ]:
        """List FAQs belonging strictly to business_id."""
        stmt = select(FAQ).where(FAQ.business_id == business_id).order_by(FAQ.id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def remove_faq(session: AsyncSession, business_id: str, faq_id: int) -> bool:
        """Remove FAQ only if it belongs to the specified business_id (tenant isolated)."""
        stmt = delete(FAQ).where(
            FAQ.id == faq_id,
            FAQ.business_id == business_id
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def log_call(
        session: AsyncSession,
        business_id: str,
        call_sid: str,
        caller_number: Optional[str] = None,
        duration_seconds: int = 0,
        transcript: Optional[str] = None,
        summary: Optional[str] = None
    ) -> CallLog:
        """Record post-call transcript and metadata."""
        log = CallLog(
            business_id=business_id,
            call_sid=call_sid,
            caller_number=caller_number,
            duration_seconds=duration_seconds,
            transcript=transcript,
            summary=summary
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return log

    @staticmethod
    async def record_voice_consent(
        session: AsyncSession,
        business_id: str,
        voice_id: str,
        voice_name: str,
        consent_text: str,
        consent_by_chat_id: Optional[int] = None,
        sample_filename: Optional[str] = None,
        consent_confirmed: bool = True
    ) -> VoiceConsentAudit:
        """Record an immutable legal voice cloning consent audit entry."""
        audit = VoiceConsentAudit(
            business_id=business_id,
            voice_id=voice_id,
            voice_name=voice_name,
            consent_confirmed=consent_confirmed,
            consent_by_chat_id=consent_by_chat_id,
            consent_text=consent_text,
            sample_filename=sample_filename
        )
        session.add(audit)
        await session.commit()
        await session.refresh(audit)
        logger.info(
            f"Voice consent logged for business_id='{business_id}' voice_id='{voice_id}' "
            f"confirmed={consent_confirmed}"
        )
        return audit

    @staticmethod
    async def list_voice_consents(
        session: AsyncSession,
        business_id: str
    ) -> List[VoiceConsentAudit]:
        """Fetch all voice consent audit records for a business tenant."""
        stmt = (
            select(VoiceConsentAudit)
            .where(VoiceConsentAudit.business_id == business_id)
            .order_by(VoiceConsentAudit.id.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_appointment(
        session: AsyncSession,
        business_id: str,
        caller_phone: str,
        slot_time: datetime,
        call_sid: Optional[str] = None,
        customer_name: Optional[str] = None,
        doctor_id: str = "primary",
        duration_minutes: int = 30,
        status: str = "CONFIRMED",
        token_number: Optional[int] = None,
        notes: Optional[str] = None
    ) -> Appointment:
        """Create and persist a scheduled appointment bound to a business tenant."""
        appt = Appointment(
            business_id=business_id,
            caller_phone=caller_phone,
            slot_time=slot_time,
            call_sid=call_sid,
            customer_name=customer_name,
            doctor_id=doctor_id,
            duration_minutes=duration_minutes,
            status=status,
            token_number=token_number,
            notes=notes
        )
        session.add(appt)
        await session.commit()
        await session.refresh(appt)
        logger.info(f"Created appointment id={appt.id} for business='{business_id}', slot={slot_time.isoformat()}")
        return appt

    @staticmethod
    async def get_appointments_by_date(
        session: AsyncSession,
        business_id: str,
        target_date: date,
        doctor_id: Optional[str] = None
    ) -> List[Appointment]:
        """Fetch all active appointments for a specific date and business."""
        start_dt = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)
        stmt = (
            select(Appointment)
            .where(Appointment.business_id == business_id)
            .where(Appointment.slot_time >= start_dt)
            .where(Appointment.slot_time <= end_dt)
            .where(Appointment.status != "CANCELLED")
        )
        if doctor_id:
            stmt = stmt.where(Appointment.doctor_id == doctor_id)
        stmt = stmt.order_by(Appointment.slot_time.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_appointment(
        session: AsyncSession,
        business_id: str,
        appointment_id: int
    ) -> Optional[Appointment]:
        """Fetch a specific appointment by ID strictly scoped to business tenant."""
        stmt = (
            select(Appointment)
            .where(Appointment.business_id == business_id)
            .where(Appointment.id == appointment_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def cancel_appointment(
        session: AsyncSession,
        business_id: str,
        appointment_id: int
    ) -> Optional[Appointment]:
        """Mark an appointment as CANCELLED."""
        appt = await BusinessRepository.get_appointment(session, business_id, appointment_id)
        if appt:
            appt.status = "CANCELLED"
            await session.commit()
            await session.refresh(appt)
            logger.info(f"Cancelled appointment id={appointment_id} for business='{business_id}'")
        return appt

    @staticmethod
    async def delete_caller_records(
        session: AsyncSession,
        business_id: str,
        caller_number: str
    ) -> Dict[str, Any]:
        """Permanently delete all call logs, transcripts, and appointments for a caller number (GDPR / DPDP forget-me flow).

        CRITICAL COMPLIANCE & SECURITY RULE:
        Deletion is strictly tenant-scoped (`WHERE business_id = :business_id`).
        Business A cannot delete any records belonging to Business B.
        """
        raw_number = caller_number.strip()
        digits_only = "".join(c for c in raw_number if c.isdigit())

        conditions = [CallLog.caller_number == raw_number]
        if digits_only and len(digits_only) >= 4:
            conditions.append(CallLog.caller_number.like(f"%{digits_only[-10:]}%"))

        stmt_call = (
            delete(CallLog)
            .where(CallLog.business_id == business_id)
            .where(or_(*conditions))
        )
        result_call = await session.execute(stmt_call)

        appt_conditions = [Appointment.caller_phone == raw_number]
        if digits_only and len(digits_only) >= 4:
            appt_conditions.append(Appointment.caller_phone.like(f"%{digits_only[-10:]}%"))

        stmt_appt = (
            delete(Appointment)
            .where(Appointment.business_id == business_id)
            .where(or_(*appt_conditions))
        )
        result_appt = await session.execute(stmt_appt)

        await session.commit()
        deleted_call_logs = result_call.rowcount if result_call.rowcount is not None else 0
        deleted_appts = result_appt.rowcount if result_appt.rowcount is not None else 0

        logger.info(
            f"GDPR Forget-Me executed: Deleted {deleted_call_logs} call logs and {deleted_appts} appointments "
            f"for caller='{caller_number}' scoped to business_id='{business_id}'"
        )

        return {
            "business_id": business_id,
            "caller_number": caller_number,
            "deleted_call_logs": deleted_call_logs,
            "deleted_appointments": deleted_appts,
            "success": True
        }
