"""Tests for Phase 2: Barge-in detection and idempotency guard."""

import asyncio
import pytest
from src.services.bargein_controller import BargeInController
from src.services.idempotency import IdempotencyStore


# ── Barge-in Tests ─────────────────────────────────────────────────────────────

def test_bargein_no_interruption_when_bot_silent():
    """Barge-in should NOT trigger if the bot is not currently speaking."""
    ctrl = BargeInController("CA_TEST_1")
    # Bot is not speaking yet
    assert ctrl.detect_interruption("hello", is_final=False) is False


@pytest.mark.asyncio
async def test_bargein_triggers_when_bot_speaking():
    """Barge-in SHOULD trigger when the bot is mid-response and caller speaks."""
    ctrl = BargeInController("CA_TEST_2")

    async def dummy_task():
        await asyncio.sleep(10)

    task = asyncio.create_task(dummy_task())
    ctrl.set_turn_task(task)

    assert ctrl.is_bot_speaking is True
    assert ctrl.detect_interruption("wait", is_final=False) is True

    result = ctrl.cancel_active_turn()
    assert result is True
    assert ctrl.is_bot_speaking is False


@pytest.mark.asyncio
async def test_bargein_clear_resets_state():
    """clear_turn_task should reset bot speaking state."""
    ctrl = BargeInController("CA_TEST_3")

    async def dummy_task():
        await asyncio.sleep(10)

    task = asyncio.create_task(dummy_task())
    ctrl.set_turn_task(task)
    assert ctrl.is_bot_speaking is True

    ctrl.clear_turn_task()
    assert ctrl.is_bot_speaking is False


# ── Idempotency Tests ──────────────────────────────────────────────────────────

def test_idempotency_first_call_passes():
    """First call with a new CallSid should pass idempotency check."""
    store = IdempotencyStore()
    result = store.check_and_set("CA_UNIQUE_001")
    assert result is True


def test_idempotency_duplicate_rejected():
    """Second call with the same CallSid should be rejected."""
    store = IdempotencyStore()
    store.check_and_set("CA_DUPE_001")
    result = store.check_and_set("CA_DUPE_001")
    assert result is False


def test_idempotency_release_allows_reuse():
    """Releasing a CallSid should allow it to be accepted again."""
    store = IdempotencyStore()
    store.check_and_set("CA_RELEASE_001")
    store.release("CA_RELEASE_001")
    result = store.check_and_set("CA_RELEASE_001")
    assert result is True


def test_idempotency_different_sids_independent():
    """Different CallSids must be tracked independently."""
    store = IdempotencyStore()
    assert store.check_and_set("CA_AAA") is True
    assert store.check_and_set("CA_BBB") is True
    assert store.check_and_set("CA_AAA") is False  # duplicate
    assert store.check_and_set("CA_BBB") is False  # duplicate


# ── Pipeline Barge-in Integration Test ────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_session_bargein_flush():
    """Verify that when caller interrupts mid-response, the turn is cancelled and Twilio clear is sent."""
    from src.services.voice_pipeline import VoicePipelineSession

    sent_events = []

    async def fake_send_to_twilio(payload: dict):
        sent_events.append(payload)

    session = VoicePipelineSession(
        call_sid="CA_BARGEIN_TEST",
        stream_sid="MZ_BARGEIN_STREAM",
        send_to_twilio=fake_send_to_twilio
    )

    # Trigger a turn
    session._handle_transcript("Tell me about your services", is_final=True, speech_final=True)
    assert session.bargein.is_bot_speaking is True

    # Caller interrupts mid-speech
    session._handle_transcript("Stop wait", is_final=False, speech_final=False)

    # Let asyncio loop process tasks
    await asyncio.sleep(0.05)

    # Verify a "clear" event was sent to Twilio
    clear_events = [e for e in sent_events if e.get("event") == "clear"]
    assert len(clear_events) >= 1
    assert clear_events[0]["streamSid"] == "MZ_BARGEIN_STREAM"

    # Verify bot speaking state was cleared
    assert session.bargein.is_bot_speaking is False

    await session.close()

