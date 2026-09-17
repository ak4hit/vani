"""Unit and Integration Tests for Prometheus Metrics, P95 Latency SLA, and Concurrent Calls Load Testing.

Verifies Phase 8 Half 2 requirements:
1. Standard Prometheus text exposition format (/metrics) with counters, gauges, and summaries.
2. Running p50, p90, p95 latency percentiles and alert trigger when p95 > 1500ms.
3. Concurrency test simulating 3+ simultaneous voice pipeline sessions with isolated state.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport

from src.services.health_monitor import health_monitor
from src.services.metrics import MetricsCollector, metrics_collector
from src.services.voice_pipeline import VoicePipelineSession
from src.main import app


@pytest.fixture(autouse=True)
def clean_metrics_state():
    """Ensure clean metrics and active call state for each test."""
    metrics_collector.reset()
    health_monitor.call_tracker.clear()
    yield
    metrics_collector.reset()
    health_monitor.call_tracker.clear()


def test_metrics_counters():
    """Verify call counters, barge-in, tts quota, and injection counters."""
    metrics = MetricsCollector()
    metrics.increment_call("completed")
    metrics.increment_call("completed")
    metrics.increment_call("failed")
    metrics.increment_bargein()
    metrics.increment_tts_quota_exhausted()
    metrics.increment_prompt_injection_blocked()

    exposition = metrics.generate_prometheus_exposition(active_calls_count=2)

    assert 'vani_calls_total{status="completed"} 2' in exposition
    assert 'vani_calls_total{status="failed"} 1' in exposition
    assert "vani_active_calls 2" in exposition
    assert "vani_bargein_interruptions_total 1" in exposition
    assert "vani_tts_quota_exhausted_total 1" in exposition
    assert "vani_prompt_injections_blocked_total 1" in exposition


def test_metrics_latency_percentiles():
    """Verify calculation of running p50, p90, p95, and p99 turn response latencies."""
    metrics = MetricsCollector()
    # Feed 100 sample latencies from 100ms to 1000ms
    for i in range(1, 101):
        metrics.record_turn_latency(total_ms=i * 10.0)

    stats = metrics.get_latency_stats()
    assert stats["count"] == 100
    assert 490.0 <= stats["p50_ms"] <= 515.0
    assert 890.0 <= stats["p90_ms"] <= 910.0
    assert 940.0 <= stats["p95_ms"] <= 960.0
    assert 980.0 <= stats["p99_ms"] <= 1000.0


def test_p95_latency_alert_threshold():
    """Verify SLA trigger fires when p95 latency exceeds 1500ms threshold."""
    metrics = MetricsCollector(p95_threshold_ms=1500.0)

    # Feed latencies well above 1.5s
    alert_1 = metrics.record_turn_latency(total_ms=1800.0, call_sid="CA_SLOW_1")
    assert alert_1 is True

    # Immediate subsequent slow turn should not re-alert due to cooldown
    alert_2 = metrics.record_turn_latency(total_ms=2000.0, call_sid="CA_SLOW_2")
    assert alert_2 is False


@pytest.mark.asyncio
async def test_get_metrics_endpoint():
    """Verify GET /metrics HTTP endpoint produces standard Prometheus text format."""
    metrics_collector.increment_call("completed")
    health_monitor.call_tracker.register_call("CA_METRICS_TEST")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "# HELP vani_calls_total" in body
    assert "# TYPE vani_calls_total counter"
    assert 'vani_calls_total{status="completed"} 1' in body
    assert "vani_active_calls 1" in body
    assert "vani_turn_latency_ms" in body


@pytest.mark.asyncio
async def test_concurrency_three_simultaneous_calls():
    """MANDATORY CONCURRENCY GATE:
    Simulates 3 concurrent voice pipeline sessions, verifying thread-safe call tracking,
    independent lifecycle management, and accurate gauge reporting.
    """
    call_sids = ["CA_CONCURRENT_1", "CA_CONCURRENT_2", "CA_CONCURRENT_3"]
    sessions = []

    for sid in call_sids:
        send_mock = AsyncMock()
        pipeline = VoicePipelineSession(
            call_sid=sid,
            stream_sid=f"MZ_{sid}",
            caller_number=f"+1555000{sid[-1]}",
            send_to_twilio=send_mock,
            business_id="biz_concurrency"
        )
        # Mock STT connect and greeting so no real external network call runs
        pipeline.stt.connect = AsyncMock()
        pipeline.play_greeting = AsyncMock()
        sessions.append(pipeline)

    # 1. Start all 3 calls concurrently
    await asyncio.gather(*(s.start() for s in sessions))

    # 2. Verify all 3 are tracked simultaneously
    assert health_monitor.call_tracker.get_active_count() == 3
    active_calls = health_monitor.call_tracker.get_active_calls()
    for sid in call_sids:
        assert sid in active_calls

    # 3. Check Prometheus exposition reflects 3 active calls
    expo = metrics_collector.generate_prometheus_exposition(health_monitor.call_tracker.get_active_count())
    assert "vani_active_calls 3" in expo
    assert 'vani_calls_total{status="completed"} 3' in expo

    # 4. Close first call
    sessions[0].stt.close = AsyncMock()
    with patch("src.services.notification_service.NotificationService.process_post_call", new_callable=AsyncMock):
        await sessions[0].close()

    assert health_monitor.call_tracker.get_active_count() == 2
    assert "CA_CONCURRENT_1" not in health_monitor.call_tracker.get_active_calls()

    # 5. Close remaining 2 calls
    for s in sessions[1:]:
        s.stt.close = AsyncMock()
        with patch("src.services.notification_service.NotificationService.process_post_call", new_callable=AsyncMock):
            await s.close()

    assert health_monitor.call_tracker.get_active_count() == 0
