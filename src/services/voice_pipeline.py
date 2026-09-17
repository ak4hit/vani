"""Core Voice Pipeline Orchestrator.

Manages bidirectional communication between:
Twilio WebSocket ↔ Deepgram STT ↔ Gemini LLM ↔ ElevenLabs TTS ↔ Twilio WebSocket
and tracks per-turn latency milestones.
Includes barge-in (interruption) detection.
"""

import asyncio
import time
from typing import AsyncGenerator, Callable, List, Optional
from src.config import settings
from src.services.audio_utils import mulaw_to_base64
from src.services.bargein_controller import BargeInController
from src.services.latency_tracker import CallLatencyTracker
from src.services.llm_service import GeminiLLMService
from src.services.stt_service import DeepgramSTTService
from src.services.tts_service import ElevenLabsTTSService
from src.utils.logger import get_logger

logger = get_logger("vani.pipeline")


class VoicePipelineSession:
    """Represents an active telephone conversation session for a single call."""

    def __init__(
        self,
        call_sid: str,
        stream_sid: str,
        send_to_twilio: Callable[[dict], asyncio.Future],
        business_prompt: Optional[str] = None,
        caller_number: str = "UNKNOWN",
        business_id: Optional[str] = None
    ):
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.send_to_twilio = send_to_twilio
        self.caller_number = caller_number
        self.business_id = business_id or settings.DEFAULT_BUSINESS_ID
        self.start_time = time.time()
        self.latency_tracker = CallLatencyTracker(call_sid)

        # Service instances
        self.stt = DeepgramSTTService()
        self.llm = GeminiLLMService(system_prompt=business_prompt)
        self.tts = ElevenLabsTTSService()

        # State tracking
        self.chat_history: List[dict] = []
        self._is_processing_turn = False
        self._is_greeting_sent = False

        # Phase 2: Barge-in controller
        self.bargein = BargeInController(call_sid)

    async def start(self) -> None:
        """Initializes the STT connection and sends the initial AI disclosure greeting."""
        logger.info(f"Starting VoicePipelineSession for Call {self.call_sid}")
        # Connect to Deepgram live WebSocket
        await self.stt.connect(on_transcript=self._handle_transcript)

        # Send mandatory AI disclosure greeting
        asyncio.create_task(self.play_greeting())

    async def play_greeting(self) -> None:
        """Generates and plays the mandatory initial AI disclosure."""
        if self._is_greeting_sent:
            return
        self._is_greeting_sent = True

        logger.info(f"Playing initial AI disclosure for Call {self.call_sid}")
        async def text_generator():
            yield settings.MANDATORY_DISCLOSURE

        async for mulaw_chunk in self.tts.stream_audio_from_text(text_generator()):
            await self._send_audio_chunk(mulaw_chunk)

    async def handle_inbound_audio(self, mulaw_chunk: bytes) -> None:
        """Feeds inbound telephony audio from Twilio into the STT engine."""
        await self.stt.send_audio(mulaw_chunk)

    def _handle_transcript(self, transcript: str, is_final: bool, speech_final: bool) -> None:
        """Callback invoked by Deepgram when caller speech is recognized."""
        if not transcript:
            return

        logger.info(f"STT [{self.call_sid}] (final={is_final}, speech_final={speech_final}): {transcript}")

        # Phase 2: Barge-in — if bot is mid-response and caller speaks, cancel immediately
        if self.bargein.detect_interruption(transcript, is_final):
            cancelled = self.bargein.cancel_active_turn()
            if cancelled:
                self._is_processing_turn = False
                asyncio.create_task(self._flush_twilio_audio())

        # Trigger LLM response when an utterance is finished
        if speech_final or (is_final and not self._is_processing_turn):
            self._is_processing_turn = True
            turn_task = asyncio.create_task(self._process_turn(transcript))
            self.bargein.set_turn_task(turn_task)

    async def _flush_twilio_audio(self) -> None:
        """Sends a Twilio clear event to stop any buffered audio playback mid-stream."""
        try:
            await self.send_to_twilio({
                "event": "clear",
                "streamSid": self.stream_sid
            })
            logger.info(f"Twilio audio buffer flushed for Call {self.call_sid} after barge-in.")
        except Exception as e:
            logger.error(f"Error flushing Twilio audio: {e}")

    async def _process_turn(self, user_utterance: str) -> None:
        """Executes a single conversational turn: LLM token streaming -> TTS -> Twilio."""
        turn = self.latency_tracker.start_new_turn()
        turn.mark_caller_speech_end()
        turn.mark_stt_final()

        logger.info(f"Processing turn #{turn.turn_id} for Call {self.call_sid}: '{user_utterance}'")
        turn.mark_llm_start()

        # Check for human escalation request
        lower_utterance = user_utterance.lower()
        if any(kw in lower_utterance for kw in [
            "transfer me", "speak to a person", "speak to a human", "talk to a person",
            "talk to a human", "operator", "receptionist", "speak to someone",
            "human please", "connect me to a representative"
        ]):
            logger.warning(f"Escalation requested during Call {self.call_sid}: '{user_utterance}'")
            from src.services.notification_service import notification_service
            from src.services.business_store import business_store
            biz = business_store.get_business(self.business_id)
            if biz and biz.chat_id:
                asyncio.create_task(
                    notification_service.send_escalation_alert(
                        chat_id=biz.chat_id,
                        business_name=biz.business_name,
                        caller_number=self.caller_number,
                        call_sid=self.call_sid,
                        reason=user_utterance,
                        destination_number=biz.escalation_number
                    )
                )

        # Queue to pass tokens from LLM stream to TTS generator
        token_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        first_token_marked = False
        first_audio_marked = False

        async def llm_worker():
            nonlocal first_token_marked
            full_response = []
            try:
                async for token in self.llm.stream_response(user_utterance, self.chat_history):
                    if not first_token_marked:
                        turn.mark_llm_first_token()
                        first_token_marked = True
                    full_response.append(token)
                    await token_queue.put(token)
            finally:
                await token_queue.put(None)  # Sentinel to end TTS stream
                bot_text = "".join(full_response).strip()
                if bot_text:
                    self.chat_history.append({"role": "user", "content": user_utterance})
                    self.chat_history.append({"role": "assistant", "content": bot_text})

        async def tts_input_generator() -> AsyncGenerator[str, None]:
            turn.mark_tts_start()
            while True:
                token = await token_queue.get()
                if token is None:
                    break
                yield token

        # Launch LLM worker
        llm_task = asyncio.create_task(llm_worker())
        if not self.bargein.is_bot_speaking:
            self.bargein.set_turn_task(asyncio.current_task())

        # Stream generated audio to Twilio
        try:
            async for mulaw_chunk in self.tts.stream_audio_from_text(tts_input_generator()):
                if not first_audio_marked:
                    turn.mark_tts_first_chunk()
                    first_audio_marked = True

                await self._send_audio_chunk(mulaw_chunk)

                if turn.t_twilio_send is None:
                    turn.mark_twilio_send()

            # If ElevenLabs quota was exhausted, log and trigger Twilio fallback
            if self.tts.quota_exhausted:
                logger.warning(f"Quota exhausted for Call {self.call_sid}. Notifying fallback.")
        except asyncio.CancelledError:
            logger.info(f"Turn #{turn.turn_id} cancelled by barge-in for Call {self.call_sid}.")
        except Exception as e:
            logger.error(f"Error during turn processing: {e}")
        finally:
            if not llm_task.done():
                llm_task.cancel()
            self.bargein.clear_turn_task()
            if turn.t_twilio_send:
                turn.log_metrics()
            self._is_processing_turn = False

    async def _send_audio_chunk(self, mulaw_chunk: bytes) -> None:
        """Encodes mu-law audio chunk and sends it over Twilio Media Stream."""
        payload = mulaw_to_base64(mulaw_chunk)
        message = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {
                "payload": payload
            }
        }
        await self.send_to_twilio(message)

    async def close(self) -> None:
        """Cleans up active connections and triggers background post-call processing."""
        logger.info(f"Closing VoicePipelineSession for Call {self.call_sid}")
        await self.stt.close()

        duration = int(time.time() - self.start_time)
        # Import dynamically to avoid circular imports
        from src.services.notification_service import notification_service
        from src.services.business_store import business_store

        biz = business_store.get_business(self.business_id)
        chat_id = biz.chat_id if biz else None
        biz_name = biz.business_name if biz else "Vani Reception"

        asyncio.create_task(
            notification_service.process_post_call(
                business_id=self.business_id,
                call_sid=self.call_sid,
                caller_number=self.caller_number,
                duration_seconds=duration,
                chat_history=self.chat_history,
                chat_id=chat_id,
                business_name=biz_name
            )
        )
