"""Distributed Slot Locking Service.

Implements real-time concurrency control and mutex locking for appointment slots
(UPGRADE_ADDENDUM.md Section 2). Prevents concurrent callers from double-booking
the same appointment slot using temporary holds (180s TTL) with Redis/in-memory support.
"""

import asyncio
import time
from typing import Dict, Optional, Tuple
from src.utils.logger import get_logger

logger = get_logger("vani.slot_lock")


class SlotLockManager:
    """Manages multi-tenant distributed slot locks during live conversational calls."""

    def __init__(self, redis_client=None):
        self.redis = redis_client
        # In-memory store: key -> (call_sid, expires_at_timestamp)
        self._memory_locks: Dict[str, Tuple[str, float]] = {}
        self._async_lock = asyncio.Lock()

    @staticmethod
    def get_lock_key(business_id: str, doctor_id: str, slot_iso: str) -> str:
        """Construct a standardized tenant-scoped lock key."""
        # Normalize slot_iso to remove spaces
        clean_slot = slot_iso.strip().replace(" ", "_")
        return f"lock:biz_{business_id}:doc_{doctor_id}:{clean_slot}"

    async def acquire_temporary_hold(
        self,
        business_id: str,
        doctor_id: str,
        slot_iso: str,
        call_sid: str,
        ttl_seconds: int = 180
    ) -> bool:
        """Attempts to hold a slot during active conversation.

        Returns True if acquired (or if already held by same call_sid),
        False if held by another caller.
        """
        key = self.get_lock_key(business_id, doctor_id, slot_iso)

        if self.redis:
            try:
                # Attempt Redis SET NX EX
                acquired = await self.redis.set(key, call_sid, nx=True, ex=ttl_seconds)
                if acquired:
                    logger.info(f"Acquired Redis slot lock: key='{key}', call_sid='{call_sid}', ttl={ttl_seconds}s")
                    return True
                # Check if current caller already holds this lock
                current_holder = await self.redis.get(key)
                if current_holder and (
                    current_holder == call_sid
                    or (hasattr(current_holder, "decode") and current_holder.decode("utf-8") == call_sid)
                ):
                    return True
                logger.warning(f"Slot lock conflict on '{key}': already held by another caller")
                return False
            except Exception as e:
                logger.error(f"Redis slot lock error: {e}. Falling back to in-memory store.")

        # In-memory fallback
        async with self._async_lock:
            now = time.time()
            if key in self._memory_locks:
                holder, expires_at = self._memory_locks[key]
                if now < expires_at:
                    if holder == call_sid:
                        # Renew hold
                        self._memory_locks[key] = (call_sid, now + ttl_seconds)
                        return True
                    logger.warning(f"Memory slot lock conflict on '{key}': held by '{holder}' until {expires_at}")
                    return False

            # Lock is free or expired
            self._memory_locks[key] = (call_sid, now + ttl_seconds)
            logger.info(f"Acquired in-memory slot lock: key='{key}', call_sid='{call_sid}', ttl={ttl_seconds}s")
            return True

    async def release_hold(
        self,
        business_id: str,
        doctor_id: str,
        slot_iso: str,
        call_sid: str
    ) -> bool:
        """Releases the slot lock if call drops or caller selects a different time."""
        key = self.get_lock_key(business_id, doctor_id, slot_iso)

        if self.redis:
            try:
                current_holder = await self.redis.get(key)
                if current_holder and (
                    current_holder == call_sid
                    or (hasattr(current_holder, "decode") and current_holder.decode("utf-8") == call_sid)
                ):
                    await self.redis.delete(key)
                    logger.info(f"Released Redis slot lock: key='{key}' by call_sid='{call_sid}'")
                    return True
            except Exception as e:
                logger.error(f"Redis slot release error: {e}")

        async with self._async_lock:
            if key in self._memory_locks:
                holder, _ = self._memory_locks[key]
                if holder == call_sid:
                    del self._memory_locks[key]
                    logger.info(f"Released in-memory slot lock: key='{key}' by call_sid='{call_sid}'")
                    return True
            return False

    async def confirm_booking(
        self,
        business_id: str,
        doctor_id: str,
        slot_iso: str,
        call_sid: str
    ) -> bool:
        """Called when appointment is permanently booked in the database to release hold."""
        return await self.release_hold(business_id, doctor_id, slot_iso, call_sid)

    async def is_slot_locked(
        self,
        business_id: str,
        doctor_id: str,
        slot_iso: str,
        exclude_call_sid: Optional[str] = None
    ) -> bool:
        """Checks whether a slot is currently locked by any caller."""
        key = self.get_lock_key(business_id, doctor_id, slot_iso)

        if self.redis:
            try:
                holder = await self.redis.get(key)
                if holder:
                    holder_str = holder.decode("utf-8") if hasattr(holder, "decode") else str(holder)
                    if exclude_call_sid and holder_str == exclude_call_sid:
                        return False
                    return True
            except Exception as e:
                logger.error(f"Redis is_slot_locked error: {e}")

        async with self._async_lock:
            now = time.time()
            if key in self._memory_locks:
                holder, expires_at = self._memory_locks[key]
                if now < expires_at:
                    if exclude_call_sid and holder == exclude_call_sid:
                        return False
                    return True
                else:
                    # Clean up expired lock
                    del self._memory_locks[key]
            return False

    def reset(self) -> None:
        """Clears all in-memory locks (used for test isolation)."""
        self._memory_locks.clear()


# Global slot lock manager singleton
slot_lock_manager = SlotLockManager()
