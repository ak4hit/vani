"""Prompt Injection Sanitizer - Active defense filter for ingested business documents and web content."""

import re
import unicodedata

# Common prompt injection, jailbreak, and LLM role hijacking patterns
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+|prior\s+|previous\s+)*instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+|prior\s+|previous\s+)*instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*override", re.IGNORECASE),
    re.compile(r"reveal\s+(other\s+customers'?\s+data|all\s+confidential|internal\s+prompts)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(in\s+developer\s+mode|dan|unrestricted)", re.IGNORECASE),
    re.compile(r"<\s*\|\s*im_start\s*\|\s*>", re.IGNORECASE),
    re.compile(r"<\s*\|\s*endoftext\s*\|\s*>", re.IGNORECASE),
    re.compile(r"\[\s*INST\s*\]", re.IGNORECASE),
    re.compile(r"\[\s*/\s*INST\s*\]", re.IGNORECASE),
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"<script[\s\S]*?>[\s\S]*?<\/script>", re.IGNORECASE),
]

# Zero-width / invisible unicode characters used for steganography or filter evasion
ZERO_WIDTH_CHARS = re.compile(r"[\u200B\u200C\u200D\uFEFF\u00AD\u2060\u200E\u200F]")


def sanitize_text(text: str) -> str:
    """Sanitize text by stripping hidden unicode and neutralizing prompt injection phrases."""
    if not text:
        return ""

    # 1. Normalize unicode characters (NFKC)
    cleaned = unicodedata.normalize("NFKC", text)

    # 2. Strip hidden / zero-width characters
    cleaned = ZERO_WIDTH_CHARS.sub("", cleaned)

    # 3. Replace malicious injection patterns with safety tags
    for pattern in INJECTION_PATTERNS:
        cleaned = pattern.sub("[REDACTED_PROMPT_INJECTION]", cleaned)

    # 4. Clean excessive whitespace and carriage returns
    cleaned = re.sub(r"\r\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()
