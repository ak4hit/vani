"""Unit tests for the latency tracking and benchmarking service."""

import time
from src.services.latency_tracker import CallLatencyTracker, TurnLatencyRecord


def test_latency_milestones():
    """Verify latency tracker computes accurate deltas across turn milestones."""
    tracker = CallLatencyTracker(call_sid="CA123456789")
    turn = tracker.start_new_turn()

    assert turn.turn_id == 1
    assert turn.call_sid == "CA123456789"

    # Simulate realistic milestone timing
    base_time = time.perf_counter()
    turn.t_caller_speech_end = base_time
    turn.t_stt_final = base_time + 0.220        # STT = 220ms
    turn.t_llm_start = base_time + 0.225
    turn.t_llm_first_token = base_time + 0.525   # LLM TTFT = 300ms
    turn.t_tts_start = base_time + 0.530
    turn.t_tts_first_chunk = base_time + 0.730   # TTS TTFB = 200ms
    turn.t_twilio_send = base_time + 0.745       # Network send = 15ms

    # Total turnaround = 745ms (well within <1000ms gate)
    metrics = turn.to_dict()

    assert metrics["stt_latency_ms"] == 220.0
    assert metrics["llm_ttft_ms"] == 300.0
    assert metrics["tts_ttfb_ms"] == 200.0
    assert metrics["total_turnaround_ms"] == 745.0
    assert metrics["total_turnaround_ms"] < 1000.0


def test_multiple_turns():
    """Verify sequential turns increment turn_id correctly."""
    tracker = CallLatencyTracker(call_sid="CA999")
    turn1 = tracker.start_new_turn()
    turn2 = tracker.start_new_turn()

    assert turn1.turn_id == 1
    assert turn2.turn_id == 2
    assert len(tracker.turns) == 2
    assert tracker.current_turn == turn2
