"""Twilio Voice Webhook and Media Streams WebSocket endpoints."""

import json
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from src.config import settings
from src.services.audio_utils import base64_to_mulaw
from src.services.voice_pipeline import VoicePipelineSession
from src.utils.logger import get_logger

logger = get_logger("vani.twilio")
router = APIRouter(prefix="/twilio", tags=["Twilio Telephony"])


@router.post("/voice")
async def inbound_voice_webhook(request: Request):
    """Twilio incoming voice webhook.

    Returns TwiML instructing Twilio to establish a bidirectional Media Stream
    WebSocket connection to /twilio/stream.
    """
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "UNKNOWN")
    from_number = form_data.get("From", "UNKNOWN")
    to_number = form_data.get("To", "UNKNOWN")

    logger.info(f"Inbound call received: CallSid={call_sid}, From={from_number}, To={to_number}")

    # Determine host for WebSocket URL
    host = request.headers.get("host", f"localhost:{settings.PORT}")
    ws_protocol = "wss" if request.url.scheme == "https" else "ws"
    stream_url = f"{ws_protocol}://{host}/twilio/stream"

    # TwiML with Media Stream connection and graceful fallback
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}">
            <Parameter name="callSid" value="{call_sid}" />
        </Stream>
    </Connect>
    <!-- Fallback if stream drops or disconnects -->
    <Say>The automated assistant is currently unavailable. Please call back shortly.</Say>
    <Hangup/>
</Response>"""

    return Response(content=twiml_response, media_type="application/xml")


@router.websocket("/stream")
async def twilio_media_stream_websocket(websocket: WebSocket):
    """Bidirectional WebSocket endpoint for Twilio Media Streams audio."""
    await websocket.accept()
    logger.info("Twilio WebSocket connection accepted.")

    session: VoicePipelineSession = None
    call_sid = "UNKNOWN"
    stream_sid = "UNKNOWN"

    async def send_to_twilio(payload: dict):
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception as e:
            logger.error(f"Error sending message to Twilio WebSocket: {e}")

    try:
        while True:
            raw_message = await websocket.receive_text()
            data = json.loads(raw_message)
            event_type = data.get("event")

            if event_type == "connected":
                logger.info("Twilio Media Stream event: connected")

            elif event_type == "start":
                start_info = data.get("start", {})
                stream_sid = start_info.get("streamSid", "UNKNOWN")
                call_sid = start_info.get("callSid", "UNKNOWN")
                logger.info(f"Twilio Media Stream started: CallSid={call_sid}, StreamSid={stream_sid}")

                # Create pipeline session
                session = VoicePipelineSession(
                    call_sid=call_sid,
                    stream_sid=stream_sid,
                    send_to_twilio=send_to_twilio
                )
                await session.start()

            elif event_type == "media":
                if session:
                    media_payload = data.get("media", {}).get("payload", "")
                    if media_payload:
                        mulaw_chunk = base64_to_mulaw(media_payload)
                        await session.handle_inbound_audio(mulaw_chunk)

            elif event_type == "stop":
                logger.info(f"Twilio Media Stream stopped for CallSid={call_sid}")
                if session:
                    await session.close()
                break

    except WebSocketDisconnect:
        logger.info(f"Twilio WebSocket disconnected for CallSid={call_sid}")
    except Exception as e:
        logger.error(f"Twilio WebSocket error: {e}")
    finally:
        if session:
            await session.close()
