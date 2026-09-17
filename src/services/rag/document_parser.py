"""Document Parser - Extracts clean text from PDF, DOCX, and TXT files with semantic chunking."""

import io
from typing import List
from pypdf import PdfReader
from docx import Document

from src.services.rag.sanitizer import sanitize_text
from src.utils.logger import get_logger

logger = get_logger("vani.rag.parser")


def extract_text_from_pdf(content: bytes) -> str:
    """Extract textual content from PDF byte stream."""
    reader = PdfReader(io.BytesIO(content))
    pages_text = []
    for idx, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages_text.append(text.strip())
    return "\n\n".join(pages_text)


def extract_text_from_docx(content: bytes) -> str:
    """Extract paragraphs and table text from DOCX byte stream."""
    doc = Document(io.BytesIO(content))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                paragraphs.append(row_text)
    return "\n\n".join(paragraphs)


def extract_text(filename: str, content: bytes) -> str:
    """Extract sanitized text from file based on file extension."""
    lower_name = filename.lower()
    raw_text = ""
    try:
        if lower_name.endswith(".pdf"):
            raw_text = extract_text_from_pdf(content)
        elif lower_name.endswith(".docx"):
            raw_text = extract_text_from_docx(content)
        elif lower_name.endswith(".txt") or lower_name.endswith(".md"):
            raw_text = content.decode("utf-8", errors="replace")
        else:
            # Fallback to UTF-8 decoding
            raw_text = content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Failed to parse document '{filename}': {e}")
        raise ValueError(f"Could not parse document format for '{filename}': {e}")

    return sanitize_text(raw_text)


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """Split text into overlapping character chunks preserving word boundaries when possible."""
    clean = sanitize_text(text)
    if not clean:
        return []

    if len(clean) <= chunk_size:
        return [clean]

    chunks: List[str] = []
    start = 0
    total_len = len(clean)

    while start < total_len:
        end = start + chunk_size
        if end >= total_len:
            chunks.append(clean[start:].strip())
            break

        # Attempt to split at a newline or whitespace near the end of the chunk
        split_idx = clean.rfind("\n", start, end)
        if split_idx == -1 or split_idx <= start + (chunk_size // 2):
            split_idx = clean.rfind(" ", start, end)

        if split_idx != -1 and split_idx > start:
            chunk = clean[start:split_idx].strip()
            start = split_idx + 1 - chunk_overlap
        else:
            chunk = clean[start:end].strip()
            start = end - chunk_overlap

        if chunk:
            chunks.append(chunk)

    return [c for c in chunks if len(c) > 10]
