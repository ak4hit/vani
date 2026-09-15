"""Unit tests for audio transcoding and resampling utilities."""

import struct
from src.services.audio_utils import (
    base64_to_mulaw,
    mulaw_to_base64,
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    resample_pcm16,
)


def test_empty_audio():
    """Verify empty byte strings handle gracefully without error."""
    assert mulaw_to_pcm16(b"") == b""
    assert pcm16_to_mulaw(b"") == b""
    assert resample_pcm16(b"", 16000, 8000) == b""


def test_mulaw_pcm16_roundtrip():
    """Verify linear PCM converts to mu-law and back within expected G.711 quantization noise."""
    # Generate a range of 16-bit linear samples
    test_samples = [-25000, -10000, -500, 0, 500, 10000, 25000]
    pcm_in = struct.pack(f"<{len(test_samples)}h", *test_samples)

    # Encode to mu-law
    mulaw_bytes = pcm16_to_mulaw(pcm_in)
    assert len(mulaw_bytes) == len(test_samples)

    # Decode back to PCM
    pcm_out = mulaw_to_pcm16(mulaw_bytes)
    out_samples = struct.unpack(f"<{len(test_samples)}h", pcm_out)

    # Check that reconstructed samples match within G.711 tolerance (logarithmic quantization)
    for orig, reconstructed in zip(test_samples, out_samples):
        diff = abs(orig - reconstructed)
        # Mu-law error tolerance scales with amplitude, maximum ~3-5% of amplitude
        max_allowed_error = max(100, int(abs(orig) * 0.06))
        assert diff <= max_allowed_error, f"Original: {orig}, Reconstructed: {reconstructed}, Diff: {diff}"


def test_resample_24k_to_8k():
    """Verify 3:1 integer decimation from 24kHz to 8kHz."""
    # 24 samples at 24kHz = 1 millisecond -> should produce 8 samples at 8kHz
    samples_24k = list(range(240))
    pcm_24k = struct.pack(f"<{len(samples_24k)}h", *samples_24k)

    pcm_8k = resample_pcm16(pcm_24k, 24000, 8000)
    samples_8k = struct.unpack(f"<{len(pcm_8k)//2}h", pcm_8k)

    assert len(samples_8k) == 80
    assert samples_8k[0] == 0
    assert samples_8k[1] == 3
    assert samples_8k[2] == 6


def test_resample_16k_to_8k():
    """Verify 2:1 integer decimation from 16kHz to 8kHz."""
    samples_16k = list(range(100))
    pcm_16k = struct.pack(f"<{len(samples_16k)}h", *samples_16k)

    pcm_8k = resample_pcm16(pcm_16k, 16000, 8000)
    samples_8k = struct.unpack(f"<{len(pcm_8k)//2}h", pcm_8k)

    assert len(samples_8k) == 50
    assert samples_8k[0] == 0
    assert samples_8k[1] == 2


def test_base64_conversion():
    """Verify base64 encode/decode matches Twilio Media Streams format."""
    raw_audio = b"\x7f\x80\xff\x00\x55"
    b64_str = mulaw_to_base64(raw_audio)
    decoded = base64_to_mulaw(b64_str)
    assert decoded == raw_audio
