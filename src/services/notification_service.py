"""Notification Service - Dispatches post-call summaries, alerts, and transcripts to business owners via Telegram."""

import asyncio
from datetime import datetime, timezone
import time
from typing import List, Optional
import httpx

from src.config import settings
from src.database.session import get_session_maker
from src.database.models.business import CallLog
from src.services.db_business_repository import BusinessRepository
from src.services.llm_service import GeminiLLMService
from src.utils.logger import get_logger

logger = get_logger("vani.notifications")


class NotificationService:
    """Handles Telegram delivery of call summaries, transcripts, and urgent alerts."""

    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN

    @staticmethod
    def format_duration(seconds: int) -> str:
        """Format duration into human-readable string (e.g., '1m 24s' or '45s')."""
        if seconds < 60:
            return f"{seconds}s"
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}m {secs:02d}s"

    @staticmethod
    def format_summary_card(
        business_name: str,
        caller_number: str,
        duration_seconds: int,
        summary: str,
        transcript: Optional[str] = None
    ) -> str:
        """Construct a structured Markdown summary card for Telegram."""
        duration_str = NotificationService.format_duration(duration_seconds)
        caller_display = caller_number if caller_number and caller_number != "UNKNOWN" else "Private / Anonymous"

        card = (
            "📞 *New Call Completed*\n\n"
            f"• *Business:* {business_name}\n"
            f"• *Caller:* `{caller_display}`\n"
            f"• *Duration:* {duration_str}\n\n"
            f"📋 *Executive Summary:*\n{summary}\n"
        )

        if transcript:
            # Truncate transcript to prevent hitting Telegram's 4096 char limit
            clean_transcript = transcript.strip()
            if len(clean_transcript) > 600:
                clean_transcript = clean_transcript[:597] + "..."
            card += f"\n💬 *Transcript Excerpt:*\n_{clean_transcript}_"

        return card

    async def send_telegram_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown"
    ) -> bool:
        """Send a message to a Telegram chat via Bot API."""
        if not self.bot_token or self.bot_token == "MOCK_TOKEN":
            logger.info(f"[MOCK TELEGRAM DISPATCH] chat_id={chat_id}\n{text}")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    logger.info(f"Telegram notification sent to chat_id={chat_id}")
                    return True
                else:
                    logger.error(f"Telegram send failed: {res.status_code} {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Error dispatching Telegram message to {chat_id}: {e}")
            return False

    async def summarize_transcript(
        self,
        chat_history: List[dict]
    ) -> str:
        """Uses LLM to produce a concise 1-2 sentence executive summary of the conversation."""
        if not chat_history:
            return "No conversation recorded (call connected but no dialogue occurred)."

        conversation_text = "\n".join(
            f"{item.get('role', 'Speaker').capitalize()}: {item.get('content', '')}"
            for item in chat_history
            if item.get('content')
        )

        if not conversation_text.strip():
            return "Caller connected but did not speak."

        prompt = (
            "You are an executive assistant for a business. Summarize the following phone call conversation "
            "in 1 to 2 clear, concise sentences. Highlight what the caller needed and any outcome or action item. "
            "Do NOT include markdown formatting or bullet points.\n\n"
            f"CONVERSATION:\n{conversation_text}"
        )

        llm = GeminiLLMService(system_prompt="You summarize customer care calls concisely.")
        chunks = []
        try:
            async for chunk in llm.stream_response(prompt):
                chunks.append(chunk)
            summary = "".join(chunks).strip()
            if summary:
                return summary
        except Exception as e:
            logger.warning(f"Failed to generate LLM summary: {e}")

        # Rule-based fallback summary if LLM call is unavailable
        user_msgs = [m.get("content", "") for m in chat_history if m.get("role") == "user"]
        if user_msgs:
            return f"Caller inquired about: '{user_msgs[0][:80]}...'."
        return "Call concluded with standard customer assistance."

    async def process_post_call(
        self,
        business_id: str,
        call_sid: str,
        caller_number: str,
        duration_seconds: int,
        chat_history: List[dict],
        chat_id: Optional[int] = None,
        business_name: str = "Vani Reception",
        session_maker_override: Optional[Any] = None
    ) -> Optional[CallLog]:
        """Orchestrates summary generation, database recording, and Telegram delivery."""
        logger.info(f"Processing post-call summary for CallSid={call_sid} (duration={duration_seconds}s)")

        # 1. Generate summary
        summary = await self.summarize_transcript(chat_history)

        # 2. Compile full transcript
        full_transcript = "\n".join(
            f"{m.get('role', 'Speaker').capitalize()}: {m.get('content', '')}"
            for m in chat_history
            if m.get('content')
        )

        # 3. Persist to CallLog in DB
        call_log = None
        try:
            session_maker = session_maker_override or get_session_maker()
            async with session_maker() as db_session:
                call_log = await BusinessRepository.log_call(
                    session=db_session,
                    business_id=business_id,
                    call_sid=call_sid,
                    caller_number=caller_number,
                    duration_seconds=duration_seconds,
                    transcript=full_transcript,
                    summary=summary
                )
        except Exception as e:
            logger.error(f"Failed to record call log in DB for CallSid={call_sid}: {e}")

        # 4. Dispatch Telegram notification card
        if chat_id is not None:
            card_msg = self.format_summary_card(
                business_name=business_name,
                caller_number=caller_number,
                duration_seconds=duration_seconds,
                summary=summary,
                transcript=full_transcript
            )
            await self.send_telegram_message(chat_id=chat_id, text=card_msg)

        return call_log


notification_service = NotificationService()
