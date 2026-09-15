"""Barge-in (interruption) detection and in-flight pipeline cancellation.

When the caller speaks while the bot is responding, all active LLM
streaming and TTS synthesis tasks are immediately cancelled, and the
Twilio audio stream is flushed to stop mid-sentence playback.
"""

import asyncio
from typing import Optional
from src.utils.logger import get_logger

logger = get_logger("vani.bargein")


class BargeInController:
    """Manages barge-in state and in-flight task cancellation for a single call."""

    def __init__(self, call_sid: str):
        self.call_sid = call_sid
        self._active_turn_task: Optional[asyncio.Task] = None
        self._is_bot_speaking = False

    def set_turn_task(self, task: asyncio.Task) -> None:
        """Register the currently running response turn task."""
        self._active_turn_task = task
        self._is_bot_speaking = True

    def clear_turn_task(self) -> None:
        """Mark the current turn as complete (bot finished speaking)."""
        self._active_turn_task = None
        self._is_bot_speaking = False

    @property
    def is_bot_speaking(self) -> bool:
        return self._is_bot_speaking

    def detect_interruption(self, transcript: str, is_final: bool) -> bool:
        """Returns True if the caller has interrupted an active bot response.

        Barge-in triggers on any speech detection (interim or final) while
        the bot is actively streaming audio to Twilio.
        """
        if self._is_bot_speaking and transcript.strip():
            return True
        return False

    def cancel_active_turn(self) -> bool:
        """Cancels the in-flight LLM+TTS turn task. Returns True if task was cancelled."""
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()
            self._active_turn_task = None
            self._is_bot_speaking = False
            logger.info(f"Barge-in detected for Call {self.call_sid}: cancelled active turn task.")
            return True
        return False
