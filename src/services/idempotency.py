"""Webhook idempotency guard using an in-memory store (Redis-compatible interface).

Ensures each Twilio CallSid is only processed once, preventing duplicate
pipeline sessions on retried webhooks or network flaps.

In production this will use Redis with:
  SET call:{sid}:processed "1" NX EX 3600
For Phase 2, an in-process TTL cache is used as a Redis-free fallback
(suitable for single-instance deployments).
"""

import time
from typing import Dict, Tuple
from src.utils.logger import get_logger

logger = get_logger("vani.idempotency")

# TTL for idempotency records (1 hour, matching the MASTERPLAN spec)
IDEMPOTENCY_TTL_SECONDS = 3600


class IdempotencyStore:
    """In-process TTL-based idempotency store.

    Thread-safe for asyncio single-threaded usage.
    Designed to be swapped for Redis in Phase 8.
    """

    def __init__(self) -> None:
        # Maps call_sid -> timestamp when it was first registered
        self._store: Dict[str, float] = {}

    def _evict_expired(self) -> None:
        """Remove records past their TTL."""
        now = time.monotonic()
        expired = [sid for sid, ts in self._store.items() if (now - ts) > IDEMPOTENCY_TTL_SECONDS]
        for sid in expired:
            del self._store[sid]

    def check_and_set(self, call_sid: str) -> bool:
        """Atomically check-and-set the call_sid.

        Returns:
            True  — call_sid was NOT previously seen; processing may proceed.
            False — call_sid was already processed; this is a duplicate; reject.
        """
        self._evict_expired()
        if call_sid in self._store:
            logger.warning(f"Duplicate webhook received for CallSid={call_sid}. Rejecting.")
            return False
        self._store[call_sid] = time.monotonic()
        logger.info(f"Idempotency key set for CallSid={call_sid}")
        return True

    def release(self, call_sid: str) -> None:
        """Remove a call_sid from the store (called on call end for memory hygiene)."""
        self._store.pop(call_sid, None)

    def clear(self) -> None:
        """Clear all records from the store (useful for testing)."""
        self._store.clear()


# Module-level singleton shared across all webhook invocations
idempotency_store = IdempotencyStore()
