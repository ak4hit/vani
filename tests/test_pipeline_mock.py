"""Mock pipeline end-to-end tests for VoicePipelineSession."""

import asyncio
import pytest
from src.services.voice_pipeline import VoicePipelineSession


@pytest.mark.asyncio
async def test_pipeline_session_lifecycle():
    """Verify session starts, plays greeting, and terminates without error."""
    sent_messages = []

    async def fake_send_to_twilio(payload: dict):
        sent_messages.append(payload)

    session = VoicePipelineSession(
        call_sid="CA_TEST_SESSION_1",
        stream_sid="MZ_STREAM_1",
        send_to_twilio=fake_send_to_twilio
    )

    # Start session (runs connect & plays greeting)
    await session.start()
    # Allow background tasks to run briefly
    await asyncio.sleep(0.1)

    # Verify at least the greeting audio chunks were generated and sent
    assert len(sent_messages) > 0
    first_msg = sent_messages[0]
    assert first_msg["event"] == "media"
    assert first_msg["streamSid"] == "MZ_STREAM_1"
    assert "payload" in first_msg["media"]

    # Close session
    await session.close()


@pytest.mark.asyncio
async def test_pipeline_turn_processing():
    """Verify speech transcript triggers LLM and TTS pipeline flow."""
    sent_messages = []

    async def fake_send_to_twilio(payload: dict):
        sent_messages.append(payload)

    session = VoicePipelineSession(
        call_sid="CA_TEST_SESSION_2",
        stream_sid="MZ_STREAM_2",
        send_to_twilio=fake_send_to_twilio
    )

    # Simulate inbound final transcript
    await session._process_turn("What are your business hours?")

    # Verify audio chunks were queued and sent to Twilio
    assert len(sent_messages) > 0

    # Verify latency tracker recorded turn metrics
    assert len(session.latency_tracker.turns) == 1
    turn = session.latency_tracker.turns[0]
    assert turn.turn_id == 1
    assert turn.t_twilio_send is not None

    await session.close()
