"""Google Gemini 1.5 Flash Token Streaming Service.

Provides conversational token streaming tailored for real-time telephony,
ensuring concise spoken replies without markdown formatting.
"""

import json
from typing import AsyncGenerator, List, Optional
import httpx

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("vani.llm")

DEFAULT_SYSTEM_PROMPT = (
    "You are Vani, an intelligent, polite, and helpful AI receptionist answering a live business phone call. "
    "Your responses will be spoken aloud to the caller in real-time over telephony. "
    "CRITICAL VOICE RULES:\n"
    "1. Speak in short, clear, conversational sentences.\n"
    "2. NEVER use markdown, bullet points, asterisks, hashtags, or formatting symbols.\n"
    "3. Keep each answer under 2-3 sentences unless the caller explicitly asks for a detailed explanation.\n"
    "4. Be friendly, reassuring, and professional.\n"
    "5. If you do not know the answer, politely offer to connect the caller to a team member."
)


class GeminiLLMService:
    """Streams conversational responses from Google Gemini 1.5 Flash."""

    def __init__(self, api_key: Optional[str] = None, system_prompt: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.model = "gemini-1.5-flash"

    async def stream_response(
        self,
        user_message: str,
        chat_history: Optional[List[dict]] = None
    ) -> AsyncGenerator[str, None]:
        """Streams text token chunks from Gemini 1.5 Flash via Server-Sent Events (SSE)."""
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not configured. Yielding simulated response.")
            yield f"Thank you for asking about '{user_message}'. I am running in test mode without an API key."
            return

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent?key={self.api_key}&alt=sse"

        # Build contents structure with conversation history
        contents = []
        if chat_history:
            for item in chat_history:
                contents.append({
                    "role": item.get("role", "user"),
                    "parts": [{"text": item.get("content", "")}]
                })
        contents.append({"role": "user", "parts": [{"text": user_message}]})

        payload = {
            "contents": contents,
            "systemInstruction": {
                "parts": [{"text": self.system_prompt}]
            },
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 200,
                "topP": 0.95
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        err_body = await response.aread()
                        logger.error(f"Gemini API returned error {response.status_code}: {err_body.decode('utf-8', errors='ignore')}")
                        yield "I apologize, but I am having trouble processing your request right now. Let me assist you with something else."
                        return

                    buffer = ""
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str:
                                try:
                                    chunk_data = json.loads(data_str)
                                    candidates = chunk_data.get("candidates", [])
                                    if candidates:
                                        parts = candidates[0].get("content", {}).get("parts", [])
                                        for part in parts:
                                            text = part.get("text", "")
                                            if text:
                                                yield text
                                except json.JSONDecodeError:
                                    continue
        except Exception as e:
            logger.error(f"Error streaming from Gemini: {e}")
            yield "I am having trouble with my connection. Please give me a moment."
