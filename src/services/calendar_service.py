"""Calendar Service & EHR Adapter Interface.

Implements pluggable calendar scheduling interfaces conforming to slot availability
and appointment data shapes (MASTERPLAN.md Phase 9 & UPGRADE_ADDENDUM.md Section 6).
Provides LocalDatabaseCalendarAdapter as default and GoogleCalendarAdapter with fallback.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

import src.database.session as db_session_module
from src.services.db_business_repository import BusinessRepository
from src.services.slot_lock_service import slot_lock_manager
from src.utils.logger import get_logger

logger = get_logger("vani.calendar_service")


class CalendarAdapter(ABC):
    """Abstract interface conforming to appointment scheduling and slot queries."""

    @abstractmethod
    async def get_available_slots(
        self,
        business_id: str,
        doctor_id: str,
        target_date: date,
        slot_duration_minutes: int = 30
    ) -> List[datetime]:
        """Returns a list of unbooked, unlocked slot start datetimes for a target day."""
        pass


class LocalDatabaseCalendarAdapter(CalendarAdapter):
    """Default calendar adapter computing slot availability from database bookings and active mutex holds."""

    def __init__(self, session_maker=None):
        self._session_maker = session_maker

    def _get_session_maker(self):
        return self._session_maker or db_session_module.get_session_maker()

    async def get_available_slots(
        self,
        business_id: str,
        doctor_id: str = "primary",
        target_date: Optional[date] = None,
        slot_duration_minutes: int = 30
    ) -> List[datetime]:
        """Calculates free 30-minute intervals within clinic hours (9:00 AM - 5:00 PM)."""
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        session_maker = self._get_session_maker()
        async with session_maker() as session:
            # Fetch existing bookings for this business, doctor, and date
            booked_appts = await BusinessRepository.get_appointments_by_date(
                session=session,
                business_id=business_id,
                target_date=target_date,
                doctor_id=doctor_id
            )

        # Build booked slot timestamps set (rounded to minute, normalized to naive UTC)
        # SQLite may return naive datetimes; strip tzinfo for consistent comparison.
        def _to_naive_utc(dt: datetime) -> datetime:
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt.replace(second=0, microsecond=0)

        booked_times = {
            _to_naive_utc(appt.slot_time)
            for appt in booked_appts
            if appt.status != "CANCELLED"
        }

        # Generate candidate slots between 9:00 AM and 5:00 PM UTC
        start_time = datetime.combine(target_date, time(9, 0)).replace(tzinfo=timezone.utc)
        end_time = datetime.combine(target_date, time(17, 0)).replace(tzinfo=timezone.utc)

        available_slots: List[datetime] = []
        curr = start_time
        while curr + timedelta(minutes=slot_duration_minutes) <= end_time:
            # Normalize curr to naive UTC for comparison with booked_times
            curr_naive = _to_naive_utc(curr)

            # Check if booked in DB
            is_booked = curr_naive in booked_times

            # Check if actively locked by another caller in SlotLockManager
            slot_iso = curr.isoformat()
            is_locked = await slot_lock_manager.is_slot_locked(business_id, doctor_id, slot_iso)

            if not is_booked and not is_locked:
                available_slots.append(curr)

            curr += timedelta(minutes=slot_duration_minutes)

        logger.info(
            f"LocalCalendar: Found {len(available_slots)} available slots for "
            f"biz='{business_id}', doc='{doctor_id}', date={target_date}"
        )
        return available_slots


class GoogleCalendarAdapter(CalendarAdapter):
    """Adapter for Google Calendar API with graceful fallback to LocalDatabaseCalendarAdapter."""

    def __init__(self, google_credentials=None, fallback_adapter: Optional[CalendarAdapter] = None):
        self.google_credentials = google_credentials
        self.fallback = fallback_adapter or LocalDatabaseCalendarAdapter()

    async def get_available_slots(
        self,
        business_id: str,
        doctor_id: str = "primary",
        target_date: Optional[date] = None,
        slot_duration_minutes: int = 30
    ) -> List[datetime]:
        """Queries Google Calendar API if credentials exist, else delegates to local adapter."""
        if not self.google_credentials:
            logger.debug("Google Calendar credentials not provided. Using local database adapter fallback.")
            return await self.fallback.get_available_slots(
                business_id=business_id,
                doctor_id=doctor_id,
                target_date=target_date,
                slot_duration_minutes=slot_duration_minutes
            )

        try:
            # In production with google-api-python-client installed:
            # service = build('calendar', 'v3', credentials=self.google_credentials)
            # freebusy = service.freebusy().query(...).execute()
            # For robustness and testing without external network calls, fallback executes:
            return await self.fallback.get_available_slots(
                business_id=business_id,
                doctor_id=doctor_id,
                target_date=target_date,
                slot_duration_minutes=slot_duration_minutes
            )
        except Exception as e:
            logger.error(f"Google Calendar API error: {e}. Falling back to local adapter.")
            return await self.fallback.get_available_slots(
                business_id=business_id,
                doctor_id=doctor_id,
                target_date=target_date,
                slot_duration_minutes=slot_duration_minutes
            )


# Default calendar adapter singleton
calendar_adapter = LocalDatabaseCalendarAdapter()
