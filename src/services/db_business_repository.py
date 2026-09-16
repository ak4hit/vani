"""Database Business Repository - Multi-tenant repository enforcing strict business_id scoping."""

from typing import List, Optional
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.business import Business, FAQ, CallLog
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
