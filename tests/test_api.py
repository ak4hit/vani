"""Integration tests for FastAPI endpoints."""

from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)


def test_health_check_endpoint():
    """Verify /health returns 200 and structured health details."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "Vani Voice AI Platform"
    assert "features" in data


def test_twilio_voice_webhook():
    """Verify /twilio/voice returns valid TwiML instructing Twilio to open a WebSocket stream."""
    response = client.post(
        "/twilio/voice",
        data={"CallSid": "CAtest123", "From": "+15551234567", "To": "+15559876543"},
        headers={"Host": "testserver"}
    )
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    xml_content = response.text
    assert "<Response>" in xml_content
    assert "<Connect>" in xml_content
    assert "<Stream" in xml_content
    assert "/twilio/stream" in xml_content
