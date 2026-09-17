"""Prometheus Metrics Exporter and P95 Latency SLA Tracker.

Exposes standard Prometheus text exposition format (/metrics) and monitors
sub-second latency SLA (p95 threshold < 1500ms).
"""

from collections import deque
from datetime import datetime, timezone
import math
import threading
import time
from typing import Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger("vani.metrics")


class MetricsCollector:
    """Collects counters, gauges, and running latency percentiles."""

    def __init__(self, max_samples: int = 500, p95_threshold_ms: float = 1500.0):
        self._lock = threading.Lock()
        self.p95_threshold_ms = p95_threshold_ms
        self._last_p95_alert_time: float = 0.0
        self._p95_alert_cooldown: float = 300.0  # 5 minutes

        # Counters
        self._calls_total: Dict[str, int] = {
            "completed": 0,
            "failed": 0,
            "escalated": 0,
            "dropped": 0
        }
        self._tts_quota_exhausted_total: int = 0
        self._prompt_injections_blocked_total: int = 0
        self._bargein_interruptions_total: int = 0

        # Latency sample buffers (sliding window)
        self._total_latencies_ms: Deque[float] = deque(maxlen=max_samples)
        self._stt_latencies_ms: Deque[float] = deque(maxlen=max_samples)
        self._llm_latencies_ms: Deque[float] = deque(maxlen=max_samples)
        self._tts_latencies_ms: Deque[float] = deque(maxlen=max_samples)

    def increment_call(self, status: str = "completed") -> None:
        """Increment call counter by status."""
        with self._lock:
            self._calls_total[status] = self._calls_total.get(status, 0) + 1

    def increment_tts_quota_exhausted(self) -> None:
        """Increment TTS quota exhaustion counter."""
        with self._lock:
            self._tts_quota_exhausted_total += 1

    def increment_prompt_injection_blocked(self) -> None:
        """Increment blocked prompt injections counter."""
        with self._lock:
            self._prompt_injections_blocked_total += 1

    def increment_bargein(self) -> None:
        """Increment barge-in interruption counter."""
        with self._lock:
            self._bargein_interruptions_total += 1

    def record_turn_latency(
        self,
        total_ms: float,
        stt_ms: Optional[float] = None,
        llm_ms: Optional[float] = None,
        tts_ms: Optional[float] = None,
        call_sid: Optional[str] = None
    ) -> bool:
        """Record turn response latencies and check if p95 exceeds SLA threshold.

        Returns True if p95 latency exceeded threshold and triggered an alert.
        """
        alert_triggered = False
        with self._lock:
            if total_ms > 0:
                self._total_latencies_ms.append(total_ms)
            if stt_ms is not None and stt_ms > 0:
                self._stt_latencies_ms.append(stt_ms)
            if llm_ms is not None and llm_ms > 0:
                self._llm_latencies_ms.append(llm_ms)
            if tts_ms is not None and tts_ms > 0:
                self._tts_latencies_ms.append(tts_ms)

            # Check p95 SLA
            p95 = self._compute_percentile_locked(self._total_latencies_ms, 95)
            now = time.time()
            if p95 > self.p95_threshold_ms and (now - self._last_p95_alert_time) > self._p95_alert_cooldown:
                self._last_p95_alert_time = now
                alert_triggered = True
                logger.warning(
                    f"SLA ALERT: p95 turn latency ({p95:.1f}ms) exceeded threshold {self.p95_threshold_ms}ms "
                    f"for CallSid='{call_sid or 'N/A'}'"
                )

        return alert_triggered

    @staticmethod
    def _compute_percentile_locked(samples: Deque[float], percentile: float) -> float:
        if not samples:
            return 0.0
        sorted_samples = sorted(samples)
        k = (len(sorted_samples) - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_samples[int(k)])
        d0 = sorted_samples[int(f)] * (c - k)
        d1 = sorted_samples[int(c)] * (k - f)
        return round(float(d0 + d1), 2)

    def get_latency_stats(self) -> Dict[str, float]:
        """Compute running p50, p90, p95, p99 for total turn latency."""
        with self._lock:
            return {
                "count": len(self._total_latencies_ms),
                "p50_ms": self._compute_percentile_locked(self._total_latencies_ms, 50),
                "p90_ms": self._compute_percentile_locked(self._total_latencies_ms, 90),
                "p95_ms": self._compute_percentile_locked(self._total_latencies_ms, 95),
                "p99_ms": self._compute_percentile_locked(self._total_latencies_ms, 99)
            }

    def generate_prometheus_exposition(self, active_calls_count: int = 0) -> str:
        """Formats collected metrics in standard Prometheus text exposition format."""
        with self._lock:
            lines = [
                "# HELP vani_calls_total Total count of telephony voice calls handled by status.",
                "# TYPE vani_calls_total counter"
            ]
            for status_label, count in self._calls_total.items():
                lines.append(f'vani_calls_total{{status="{status_label}"}} {count}')

            lines.extend([
                "",
                "# HELP vani_active_calls Number of currently active concurrent voice calls.",
                "# TYPE vani_active_calls gauge",
                f"vani_active_calls {active_calls_count}",
                "",
                "# HELP vani_bargein_interruptions_total Total barge-in speech interruptions triggered.",
                "# TYPE vani_bargein_interruptions_total counter",
                f"vani_bargein_interruptions_total {self._bargein_interruptions_total}",
                "",
                "# HELP vani_tts_quota_exhausted_total Total occurrences of ElevenLabs TTS quota exhaustion.",
                "# TYPE vani_tts_quota_exhausted_total counter",
                f"vani_tts_quota_exhausted_total {self._tts_quota_exhausted_total}",
                "",
                "# HELP vani_prompt_injections_blocked_total Total prompt injection attempts neutralized.",
                "# TYPE vani_prompt_injections_blocked_total counter",
                f"vani_prompt_injections_blocked_total {self._prompt_injections_blocked_total}",
                "",
                "# HELP vani_turn_latency_ms Summary percentiles of voice turn response latency.",
                "# TYPE vani_turn_latency_ms summary",
                f'vani_turn_latency_ms{{quantile="0.5"}} {self._compute_percentile_locked(self._total_latencies_ms, 50)}',
                f'vani_turn_latency_ms{{quantile="0.9"}} {self._compute_percentile_locked(self._total_latencies_ms, 90)}',
                f'vani_turn_latency_ms{{quantile="0.95"}} {self._compute_percentile_locked(self._total_latencies_ms, 95)}',
                f'vani_turn_latency_ms{{quantile="0.99"}} {self._compute_percentile_locked(self._total_latencies_ms, 99)}',
                f"vani_turn_latency_ms_count {len(self._total_latencies_ms)}",
                ""
            ])
            return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics (for test isolation)."""
        with self._lock:
            for k in self._calls_total:
                self._calls_total[k] = 0
            self._tts_quota_exhausted_total = 0
            self._prompt_injections_blocked_total = 0
            self._bargein_interruptions_total = 0
            self._total_latencies_ms.clear()
            self._stt_latencies_ms.clear()
            self._llm_latencies_ms.clear()
            self._tts_latencies_ms.clear()
            self._last_p95_alert_time = 0.0


metrics_collector = MetricsCollector()
