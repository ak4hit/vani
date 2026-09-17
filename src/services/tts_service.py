"""ElevenLabs Streaming TTS WebSocket Client.

Streams token chunks to ElevenLabs turbo_v2 via WebSocket, receives PCM audio chunks,
resamples them to 8kHz, and converts to mu-law for Twilio Media Streams.
Includes fallback handling for quota exhaustion.
"""

import asyncio
import base64
import json
from typing import AsyncGenerator, List, Optional
import websockets
from websockets.client import WebSocketClientProtocol

from src.config import settings
from src.services.audio_utils import pcm16_to_mulaw, resample_pcm16
from src.utils.logger import get_logger

logger = get_logger("vani.tts")


class ElevenLabsTTSService:
    """Manages real-time streaming TTS WebSocket connection with ElevenLabs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None
    ):
        self.api_key = api_key or settings.ELEVENLABS_API_KEY
        self.voice_id = voice_id or settings.ELEVENLABS_VOICE_ID
        self.model_id = model_id or settings.ELEVENLABS_MODEL_ID
        self.output_format = "pcm_16000"  # 16kHz PCM 16-bit
        self.src_sample_rate = 16000
        self.quota_exhausted = False

    def get_ws_url(self) -> str:
        return (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream-input"
            f"?model_id={self.model_id}"
            f"&output_format={self.output_format}"
        )

    async def stream_audio_from_text(
        self,
        text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[bytes, None]:
        """Streams text chunks to ElevenLabs and yields 8kHz mu-law audio chunks for Twilio.

        If ElevenLabs fails or quota is exhausted, sets `quota_exhausted = True`
        and returns cleanly so caller can trigger Twilio <Say> fallback.
        """
        if self.quota_exhausted:
            logger.warning("ElevenLabs quota exhausted. Using Twilio fallback voice synthesis.")
            yield self.generate_fallback_audio()
            return

        if not self.api_key:
            logger.warning("ELEVENLABS_API_KEY not configured. TTS running in mock mode.")
            # Yield 1 second of silent mu-law frames for test harness
            yield b"\xff" * 800
            return

        ws_url = self.get_ws_url()
        try:
            async with websockets.connect(ws_url) as ws:
                # 1. Send initial handshake with API key and voice config
                init_payload = {
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75
                    },
                    "generation_config": {
                        "chunk_length_schedule": [80, 120, 180, 250]
                    },
                    "xi_api_key": self.api_key
                }
                await ws.send(json.dumps(init_payload))

                # Task 1: Feed text chunks as they arrive from LLM
                async def text_sender():
                    try:
                        async for text_chunk in text_stream:
                            if text_chunk:
                                payload = {
                                    "text": text_chunk,
                                    "try_trigger_generation": True
                                }
                                await ws.send(json.dumps(payload))
                        # Signal end of text
                        await ws.send(json.dumps({"text": ""}))
                    except Exception as e:
                        logger.error(f"Error sending text to ElevenLabs: {e}")

                sender_task = asyncio.create_task(text_sender())

                # Task 2: Yield audio chunks received from ElevenLabs
                try:
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        audio_b64 = data.get("audio")

                        if audio_b64:
                            # Decode raw 16kHz PCM
                            raw_pcm16 = base64.b64decode(audio_b64)
                            # Downsample 16kHz -> 8kHz
                            pcm8 = resample_pcm16(raw_pcm16, self.src_sample_rate, settings.TWILIO_SAMPLE_RATE)
                            # Transcode PCM16 -> G.711 mu-law for Twilio
                            mulaw_chunk = pcm16_to_mulaw(pcm8)
                            yield mulaw_chunk

                        if data.get("isFinal"):
                            break
                finally:
                    if not sender_task.done():
                        sender_task.cancel()

        except websockets.exceptions.InvalidStatusCode as e:
            if e.status_code in (401, 402, 429):
                logger.error(f"ElevenLabs quota exhausted or auth failure (HTTP {e.status_code}). Enabling Twilio Say fallback.")
                self.quota_exhausted = True
                yield self.generate_fallback_audio()
            else:
                logger.error(f"ElevenLabs WebSocket returned status {e.status_code}: {e}")
        except Exception as e:
            logger.error(f"Error during ElevenLabs streaming: {e}")
            if self.quota_exhausted:
                yield self.generate_fallback_audio()

    def generate_fallback_audio(self) -> bytes:
        """Generates 8kHz mu-law fallback audio frames so calls remain uninterrupted when quota is exhausted."""
        # 800 bytes of mu-law silence/frame equates to 100ms of 8kHz telephony audio
        return b"\xff" * 800
