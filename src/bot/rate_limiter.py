"""Rate Limiter - In-memory sliding window rate limiter for Telegram admin commands."""

import time
from functools import wraps
from typing import Dict, List
import threading
from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("vani.bot.ratelimit")


class RateLimiter:
    """Sliding-window rate limiter per chat_id."""

    def __init__(self, max_calls: int = 10, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._history: Dict[int, List[float]] = {}

    def is_allowed(self, chat_id: int, max_calls: int = None, window_seconds: int = None) -> bool:
        """Check if request from chat_id is within the rate limit."""
        limit = max_calls or self.max_calls
        window = window_seconds or self.window_seconds
        now = time.time()

        with self._lock:
            timestamps = self._history.get(chat_id, [])
            # Filter timestamps within current window
            valid_timestamps = [t for t in timestamps if now - t < window]
            if len(valid_timestamps) >= limit:
                self._history[chat_id] = valid_timestamps
                return False

            valid_timestamps.append(now)
            self._history[chat_id] = valid_timestamps
            return True

    def reset(self):
        """Clear rate limit history."""
        with self._lock:
            self._history.clear()


bot_rate_limiter = RateLimiter(
    max_calls=settings.TELEGRAM_RATE_LIMIT_CALLS,
    window_seconds=settings.TELEGRAM_RATE_LIMIT_WINDOW_SEC
)


def rate_limited(max_calls: int = None, window_seconds: int = None):
    """Decorator for Telegram handler callbacks to enforce rate limits."""
    def decorator(handler_func):
        @wraps(handler_func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is not None:
                if not bot_rate_limiter.is_allowed(chat_id, max_calls=max_calls, window_seconds=window_seconds):
                    logger.warning(f"Rate limit exceeded for chat_id={chat_id}")
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            "⚠️ *Rate limit reached.* Please wait a moment before sending more commands.",
                            parse_mode="Markdown"
                        )
                    return None
            return await handler_func(update, context, *args, **kwargs)
        return wrapper
    return decorator
