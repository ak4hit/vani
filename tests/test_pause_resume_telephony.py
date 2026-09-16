"""Tests for Phase 3: Pause and Resume non-interrupting telephony behavior."""

import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.config import settings
from src.services.business_store import business_store
from src.services.idempotency import idempotency_store
from src.services.voice_pipeline import VoicePipelineSession


@pytest.fixture(autouse=True)
def clean_state():
    business_store.reset()
    idempotency_store.clear()
    yield
    business_store.reset()
    idempotency_store.clear()


@pytest.mark.asyncio
async def test_inbound_call_when_active():
    """When active, Twilio webhook must return normal <Stream> connection."""
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/twilio/voice",
            data={"CallSid": "CA_ACTIVE_001", "From": "+15551112222", "To": "+15553334444"}
        )
        assert response.status_code == 200
        assert "<Stream url=" in response.text
        assert "callSid" in response.text


@pytest.mark.asyncio
async def test_inbound_call_when_paused_with_escalation():
    """When paused and escalation number is set, new call must dial escalation directly."""
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, True)
    business_store.update_profile(
        settings.DEFAULT_BUSINESS_ID,
        escalation_number="+18005559999"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/twilio/voice",
            data={"CallSid": "CA_PAUSED_001", "From": "+15551112222", "To": "+15553334444"}
        )
        assert response.status_code == 200
        assert "<Stream" not in response.text
        assert "<Dial>+18005559999</Dial>" in response.text
        assert "assistant is paused" in response.text


@pytest.mark.asyncio
async def test_inbound_call_when_paused_without_escalation():
    """When paused with no escalation number, new call must go to voicemail."""
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, True)
    business_store.update_profile(
        settings.DEFAULT_BUSINESS_ID,
        escalation_number=None
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/twilio/voice",
            data={"CallSid": "CA_PAUSED_002", "From": "+15551112222", "To": "+15553334444"}
        )
        assert response.status_code == 200
        assert "<Stream" not in response.text
        assert "<Record maxLength=" in response.text
        assert "temporarily paused" in response.text


@pytest.mark.asyncio
async def test_inflight_call_not_interrupted_by_pause():
    """In-flight call session continues processing audio even if bot is paused mid-call."""
    # 1. Start active call
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, False)

    sent_events = []
    async def fake_send(payload: dict):
        sent_events.append(payload)

    session = VoicePipelineSession(
        call_sid="CA_INFLIGHT_001",
        stream_sid="MZ_INFLIGHT_001",
        send_to_twilio=fake_send
    )
    await session.start()

    # Verify session is running
    assert session.call_sid == "CA_INFLIGHT_001"
    assert session.stream_sid == "MZ_INFLIGHT_001"

    # 2. Mid-call: pause is activated
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, True)
    assert business_store.is_paused(settings.DEFAULT_BUSINESS_ID) is True

    # 3. Active session continues processing audio without crashing or prematurely terminating
    fake_audio = b"\xff" * 160
    await session.handle_inbound_audio(fake_audio)
    assert session.call_sid == "CA_INFLIGHT_001"

    # 4. Session ends naturally
    await session.close()
