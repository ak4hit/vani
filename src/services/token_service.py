"""Daily Token Queue Engine.

Generates atomic, sequential daily token numbers for clinic appointment intake
(UPGRADE_ADDENDUM.md Section 5). E.g. #01, #02, #14...
Scoped by business_id and date, with 48-hour expiration.
"""

import asyncio
from typing import Dict
from src.utils.logger import get_logger

logger = get_logger("vani.token_service")


class TokenService:
    """Provides atomic daily sequence token generation for clinic queue management."""

    def __init__(self, redis_client=None):
        self.redis = redis_client
        # In-memory store: "business_id:date_str" -> current_counter
        self._memory_counters: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def get_token_key(business_id: str, date_str: str) -> str:
        """Construct a standardized tenant and date scoped key."""
        return f"token:biz_{business_id}:{date_str}"

    async def generate_daily_token(self, business_id: str, date_str: str) -> int:
        """Generates an atomic, incrementing token number for the clinic day.

        Returns integer token number (1, 2, 3...).
        """
        key = self.get_token_key(business_id, date_str)

        if self.redis:
            try:
                token_num = await self.redis.incr(key)
                if token_num == 1:
                    # Expire token counter after 48 hours (172,800 seconds)
                    await self.redis.expire(key, 172800)
                logger.info(f"Generated Redis token #{token_num} for key='{key}'")
                return int(token_num)
            except Exception as e:
                logger.error(f"Redis token generation error: {e}. Falling back to in-memory counter.")

        async with self._lock:
            current = self._memory_counters.get(key, 0) + 1
            self._memory_counters[key] = current
            logger.info(f"Generated in-memory token #{current} for key='{key}'")
            return current

    @staticmethod
    def format_token_string(token_number: int) -> str:
        """Formats integer token into standardized display string (e.g. #01, #14)."""
        if token_number < 10:
            return f"#{token_number:02d}"
        return f"#{token_number}"

    def reset(self) -> None:
        """Clears in-memory counters (used for test isolation)."""
        self._memory_counters.clear()


# Global token service singleton
token_service = TokenService()
