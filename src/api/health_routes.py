"""Health check endpoints for Vani service."""

from fastapi import APIRouter
from src.config import settings
from src.services.health_monitor import health_monitor

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Returns basic system health and service configuration status."""
    return {
        "status": "healthy",
        "service": "Vani Voice AI Platform",
        "version": "0.1.0",
        "environment": settings.ENVIRONMENT,
        "features": {
            "stt_configured": bool(settings.DEEPGRAM_API_KEY),
            "llm_configured": bool(settings.GEMINI_API_KEY),
            "tts_configured": bool(settings.ELEVENLABS_API_KEY),
            "twilio_configured": bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)
        }
    }


@router.get("/api/status")
async def detailed_system_status():
    """Returns comprehensive real-time system diagnostics (active calls, DB, Redis, API keys, quota, errors)."""
    return await health_monitor.get_system_health()
