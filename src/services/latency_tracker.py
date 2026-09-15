"""Latency tracking and milestone benchmarking for Vani real-time telephony pipeline."""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.utils.logger import get_logger

logger = get_logger("vani.latency")


@dataclass
class TurnLatencyRecord:
    """Records precise milestone timestamps for a single conversational turn."""
    turn_id: int
    call_sid: str
    t_caller_speech_end: Optional[float] = None
    t_stt_final: Optional[float] = None
    t_llm_start: Optional[float] = None
    t_llm_first_token: Optional[float] = None
    t_tts_start: Optional[float] = None
    t_tts_first_chunk: Optional[float] = None
    t_twilio_send: Optional[float] = None

    def mark_caller_speech_end(self) -> None:
        self.t_caller_speech_end = time.perf_counter()

    def mark_stt_final(self) -> None:
        self.t_stt_final = time.perf_counter()

    def mark_llm_start(self) -> None:
        self.t_llm_start = time.perf_counter()

    def mark_llm_first_token(self) -> None:
        self.t_llm_first_token = time.perf_counter()

    def mark_tts_start(self) -> None:
        self.t_tts_start = time.perf_counter()

    def mark_tts_first_chunk(self) -> None:
        self.t_tts_first_chunk = time.perf_counter()

    def mark_twilio_send(self) -> None:
        self.t_twilio_send = time.perf_counter()

    @property
    def stt_latency_ms(self) -> Optional[float]:
        if self.t_stt_final and self.t_caller_speech_end:
            return (self.t_stt_final - self.t_caller_speech_end) * 1000
        return None

    @property
    def llm_ttft_ms(self) -> Optional[float]:
        if self.t_llm_first_token and (self.t_llm_start or self.t_stt_final):
            start = self.t_llm_start or self.t_stt_final
            return (self.t_llm_first_token - start) * 1000
        return None

    @property
    def tts_ttfb_ms(self) -> Optional[float]:
        if self.t_tts_first_chunk and (self.t_tts_start or self.t_llm_first_token):
            start = self.t_tts_start or self.t_llm_first_token
            return (self.t_tts_first_chunk - start) * 1000
        return None

    @property
    def total_turnaround_ms(self) -> Optional[float]:
        if self.t_twilio_send and self.t_caller_speech_end:
            return (self.t_twilio_send - self.t_caller_speech_end) * 1000
        return None

    def to_dict(self) -> Dict[str, Optional[float]]:
        return {
            "turn_id": self.turn_id,
            "call_sid": self.call_sid,
            "stt_latency_ms": round(self.stt_latency_ms, 2) if self.stt_latency_ms is not None else None,
            "llm_ttft_ms": round(self.llm_ttft_ms, 2) if self.llm_ttft_ms is not None else None,
            "tts_ttfb_ms": round(self.tts_ttfb_ms, 2) if self.tts_ttfb_ms is not None else None,
            "total_turnaround_ms": round(self.total_turnaround_ms, 2) if self.total_turnaround_ms is not None else None,
        }

    def log_metrics(self) -> None:
        metrics = self.to_dict()
        total = metrics["total_turnaround_ms"]
        status = "PASS (<1000ms)" if (total is not None and total < 1000) else "EXCEEDED (>1000ms)"
        logger.info(
            f"Latency Turn #{self.turn_id} for Call {self.call_sid}: "
            f"Total={total}ms [{status}] | "
            f"STT={metrics['stt_latency_ms']}ms | "
            f"LLM_TTFT={metrics['llm_ttft_ms']}ms | "
            f"TTS_TTFB={metrics['tts_ttfb_ms']}ms",
            extra={"call_sid": self.call_sid, "metrics": metrics}
        )


class CallLatencyTracker:
    """Manages turn records for an active phone call."""

    def __init__(self, call_sid: str):
        self.call_sid = call_sid
        self.turns: List[TurnLatencyRecord] = []
        self._current_turn_id = 0

    def start_new_turn(self) -> TurnLatencyRecord:
        self._current_turn_id += 1
        record = TurnLatencyRecord(turn_id=self._current_turn_id, call_sid=self.call_sid)
        self.turns.append(record)
        return record

    @property
    def current_turn(self) -> Optional[TurnLatencyRecord]:
        return self.turns[-1] if self.turns else None
