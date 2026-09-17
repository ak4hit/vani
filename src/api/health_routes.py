"""Health check endpoints for Vani service."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from src.config import settings
from src.services.health_monitor import health_monitor
from src.services.metrics import metrics_collector

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


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Exposes real-time Prometheus telemetry metrics (call counters, latency percentiles, active gauges)."""
    active_count = health_monitor.call_tracker.get_active_count()
    return PlainTextResponse(
        metrics_collector.generate_prometheus_exposition(active_calls_count=active_count),
        media_type="text/plain; version=0.0.4"
    )
