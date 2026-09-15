"""Audio utilities for telephony transcoding (G.711 mu-law <-> PCM16) and resampling.

Designed with a built-in ITU-T G.711 mu-law codec ensuring compatibility
with Python 3.13+ and 3.14 where audioop was removed from the standard library.
"""

import base64
import struct
from typing import Union

# Precompute ITU-T G.711 mu-law decompression table (8-bit -> 16-bit signed PCM)
BIAS = 0x84  # 132
CLIP = 32635

_MULAW_TO_PCM = []
for _u in range(256):
    _comp = ~_u & 0xFF
    _sign = _comp & 0x80
    _exponent = (_comp >> 4) & 0x07
    _mantissa = _comp & 0x0F
    _val = ((_mantissa << 3) + BIAS) << _exponent
    _val -= BIAS
    _sample = -_val if _sign else _val
    _MULAW_TO_PCM.append(_sample)

# Precomputed bytes table for fast decode
_MULAW_DECODE_STRUCT = struct.Struct(f"<{len(_MULAW_TO_PCM)}h")


def mulaw_to_pcm16(mulaw_data: bytes) -> bytes:
    """Converts 8kHz 8-bit mu-law audio to 16-bit linear PCM (little-endian)."""
    if not mulaw_data:
        return b""
    samples = [_MULAW_TO_PCM[b] for b in mulaw_data]
    return struct.pack(f"<{len(samples)}h", *samples)


def _pcm16_sample_to_mulaw(sample: int) -> int:
    """Compress a single 16-bit signed integer sample to 8-bit mu-law."""
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample

    if sample > CLIP:
        sample = CLIP

    sample += BIAS

    # Determine exponent
    exponent = 7
    for exp, threshold in enumerate((0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000)):
        if sample <= threshold:
            exponent = exp
            break

    mantissa = (sample >> (exponent + 3)) & 0x0F
    compressed = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return compressed


def pcm16_to_mulaw(pcm_data: bytes) -> bytes:
    """Converts 16-bit linear PCM (little-endian) to 8-bit mu-law."""
    if not pcm_data:
        return b""
    num_samples = len(pcm_data) // 2
    samples = struct.unpack(f"<{num_samples}h", pcm_data[: num_samples * 2])
    mulaw_bytes = bytearray(_pcm16_sample_to_mulaw(s) for s in samples)
    return bytes(mulaw_bytes)


def resample_pcm16(pcm_data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resamples 16-bit mono PCM audio from src_rate to dst_rate using linear interpolation."""
    if src_rate == dst_rate or not pcm_data:
        return pcm_data

    num_src_samples = len(pcm_data) // 2
    if num_src_samples == 0:
        return b""

    src_samples = struct.unpack(f"<{num_src_samples}h", pcm_data[: num_src_samples * 2])

    # Integer decimation shortcuts for common telephony downsamples
    if src_rate == 24000 and dst_rate == 8000:
        # Exact 3:1 decimation
        decimated = src_samples[::3]
        return struct.pack(f"<{len(decimated)}h", *decimated)

    if src_rate == 16000 and dst_rate == 8000:
        # Exact 2:1 decimation
        decimated = src_samples[::2]
        return struct.pack(f"<{len(decimated)}h", *decimated)

    # General linear interpolation
    ratio = dst_rate / src_rate
    num_dst_samples = int(num_src_samples * ratio)
    dst_samples = []

    for i in range(num_dst_samples):
        src_pos = i / ratio
        idx = int(src_pos)
        frac = src_pos - idx

        if idx + 1 < num_src_samples:
            s1 = src_samples[idx]
            s2 = src_samples[idx + 1]
            sample = int(s1 + frac * (s2 - s1))
        else:
            sample = src_samples[min(idx, num_src_samples - 1)]

        # Clamp to int16 range
        sample = max(-32768, min(32767, sample))
        dst_samples.append(sample)

    return struct.pack(f"<{len(dst_samples)}h", *dst_samples)


def base64_to_mulaw(payload: str) -> bytes:
    """Decode Twilio inbound base64 string to raw mu-law bytes."""
    return base64.b64decode(payload)


def mulaw_to_base64(audio: bytes) -> str:
    """Encode raw mu-law bytes to Twilio outbound base64 string."""
    return base64.b64encode(audio).decode("ascii")
