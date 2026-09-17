"""Tests for Human Escalation Flash Alerts and Twilio Missed Call Webhook Tracking."""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app
from src.config import settings
from src.services.business_store import business_store
from src.services.notification_service import NotificationService, notification_service
from src.services.voice_pipeline import VoicePipelineSession


@pytest.fixture(autouse=True)
def setup_business():
    business_store.reset()
    biz = business_store.get_or_create_business(settings.DEFAULT_BUSINESS_ID, chat_id=88888)
    business_store.update_profile(
        biz.business_id,
        business_name="Apex Care",
        escalation_number="+15550001111"
    )
    yield
    business_store.reset()


def test_format_escalation_alert():
    """Verify markdown output of human escalation alert."""
    alert = NotificationService.format_escalation_alert(
        business_name="Apex Care",
        caller_number="+15551234567",
        call_sid="CA_ESC_001",
        reason="I want to speak to a doctor immediately",
        destination_number="+15550001111"
    )
    assert "URGENT: Human Escalation Requested" in alert
    assert "Apex Care" in alert
    assert "+15551234567" in alert
    assert "CA_ESC_001" in alert
    assert "+15550001111" in alert
    assert "speak to a doctor immediately" in alert


@pytest.mark.asyncio
async def test_send_escalation_alert_mock():
    """Verify sending escalation alert to Telegram mock succeeds."""
    service = NotificationService()
    success = await service.send_escalation_alert(
        chat_id=88888,
        business_name="Apex Care",
        caller_number="+15551234567",
        call_sid="CA_ESC_002",
        reason="Transfer to receptionist",
        destination_number="+15550001111"
    )
    assert success is True


def test_format_missed_call_card():
    """Verify markdown formatting of missed call notification."""
    card = NotificationService.format_missed_call_card(
        business_name="Apex Care",
        caller_number="+15559990000",
        call_sid="CA_MISSED_001",
        call_status="no-answer",
        timestamp="2026-09-17 12:00:00 UTC"
    )
    assert "Missed Call Alert" in card
    assert "Apex Care" in card
    assert "+15559990000" in card
    assert "NO-ANSWER" in card
    assert "CA_MISSED_001" in card


@pytest.mark.asyncio
async def test_twilio_status_webhook_missed_call():
    """POST /twilio/status with CallStatus=no-answer must trigger missed call alert."""
    transport = ASGITransport(app=app)
    with patch.object(notification_service, "send_missed_call_alert", new_callable=AsyncMock) as mock_alert:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/twilio/status",
                data={
                    "CallSid": "CA_STATUS_NO_ANS",
                    "CallStatus": "no-answer",
                    "From": "+15551113333"
                }
            )
            assert response.status_code == 200
            assert "<Response/>" in response.text

            mock_alert.assert_called_once_with(
                chat_id=88888,
                business_name="Apex Care",
                caller_number="+15551113333",
                call_sid="CA_STATUS_NO_ANS",
                call_status="no-answer"
            )


@pytest.mark.asyncio
async def test_twilio_status_webhook_busy():
    """POST /twilio/status with CallStatus=busy must trigger missed call alert."""
    transport = ASGITransport(app=app)
    with patch.object(notification_service, "send_missed_call_alert", new_callable=AsyncMock) as mock_alert:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/twilio/status",
                data={
                    "CallSid": "CA_STATUS_BUSY",
                    "CallStatus": "busy",
                    "From": "+15552224444"
                }
            )
            assert response.status_code == 200
            mock_alert.assert_called_once()


@pytest.mark.asyncio
async def test_twilio_status_webhook_completed_no_missed_alert():
    """POST /twilio/status with CallStatus=completed must NOT trigger missed call alert."""
    transport = ASGITransport(app=app)
    with patch.object(notification_service, "send_missed_call_alert", new_callable=AsyncMock) as mock_alert:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/twilio/status",
                data={
                    "CallSid": "CA_STATUS_COMPLETED",
                    "CallStatus": "completed",
                    "From": "+15553335555"
                }
            )
            assert response.status_code == 200
            mock_alert.assert_not_called()


@pytest.mark.asyncio
async def test_escalation_intent_in_pipeline_turn():
    """Turn processing must dispatch escalation alert when user asks for a human."""
    sent_msgs = []
    async def fake_send(payload):
        sent_msgs.append(payload)

    session = VoicePipelineSession(
        call_sid="CA_ESC_TURN_001",
        stream_sid="MZ_ESC_001",
        send_to_twilio=fake_send,
        caller_number="+15554446666",
        business_id=settings.DEFAULT_BUSINESS_ID
    )

    with patch.object(notification_service, "send_escalation_alert", new_callable=AsyncMock) as mock_esc:
        await session._process_turn("Please transfer me to a human right now.")
        mock_esc.assert_called_once()
        assert "transfer me" in mock_esc.call_args[1]["reason"].lower()
