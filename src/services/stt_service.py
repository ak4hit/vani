"""Deepgram Live Streaming STT WebSocket Client.

Streams real-time 8kHz mu-law telephony audio to Deepgram Nova-2
and emits transcripts (interim and speech_final endpointed utterances).
"""

import asyncio
import json
from typing import AsyncGenerator, Callable, Optional
import websockets
from websockets.client import WebSocketClientProtocol

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("vani.stt")


class DeepgramSTTService:
    """Manages a persistent live streaming WebSocket session with Deepgram STT."""

    DEEPGRAM_WS_URL = (
        "wss://api.deepgram.com/v1/listen"
        "?model=nova-2"
        "&encoding=mulaw"
        "&sample_rate=8000"
        "&channels=1"
        "&punctuate=true"
        "&interim_results=true"
        "&endpointing=350"
        "&smart_format=true"
    )

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.DEEPGRAM_API_KEY
        self.ws: Optional[WebSocketClientProtocol] = None
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None

    async def connect(self, on_transcript: Callable[[str, bool, bool], None]) -> bool:
        """Connects to Deepgram's live streaming WebSocket."""
        if not self.api_key:
            logger.warning("DEEPGRAM_API_KEY not configured. STT service running in mock mode.")
            return False

        headers = {"Authorization": f"Token {self.api_key}"}
        try:
            self.ws = await websockets.connect(self.DEEPGRAM_WS_URL, additional_headers=headers)
            self._running = True
            self._receive_task = asyncio.create_task(self._receiver_loop(on_transcript))
            logger.info("Connected to Deepgram Live Streaming STT WebSocket.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Deepgram STT: {e}")
            self.ws = None
            return False

    async def send_audio(self, mulaw_chunk: bytes) -> None:
        """Sends raw 8kHz mu-law audio chunk to Deepgram."""
        if self.ws and self._running:
            try:
                await self.ws.send(mulaw_chunk)
            except Exception as e:
                logger.error(f"Error sending audio to Deepgram: {e}")

    async def _receiver_loop(self, on_transcript: Callable[[str, bool, bool], None]) -> None:
        """Listens for transcription messages from Deepgram."""
        try:
            while self._running and self.ws:
                msg = await self.ws.recv()
                if isinstance(msg, str):
                    data = json.loads(msg)
                    channel = data.get("channel", {})
                    alternatives = channel.get("alternatives", [])
                    if alternatives:
                        transcript = alternatives[0].get("transcript", "").strip()
                        is_final = data.get("is_final", False)
                        speech_final = data.get("speech_final", False)

                        if transcript:
                            on_transcript(transcript, is_final, speech_final)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Deepgram WebSocket connection closed.")
        except Exception as e:
            logger.error(f"Deepgram receiver loop error: {e}")
        finally:
            self._running = False

    async def close(self) -> None:
        """Closes the Deepgram streaming WebSocket session."""
        self._running = False
        if self.ws:
            try:
                # Send close stream message to Deepgram
                await self.ws.send(json.dumps({"type": "CloseStream"}))
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
