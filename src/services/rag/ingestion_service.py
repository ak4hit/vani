"""RAG Ingestion Service - Coordinates document parsing, embedding generation, and vector storage."""

import hashlib
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.services.rag.document_parser import extract_text, chunk_text
from src.services.rag.sanitizer import sanitize_text
from src.services.rag.web_crawler import ScopedWebCrawler
from src.services.vector_store import VectorStoreService, EmbeddingQuotaExceededError
from src.utils.logger import get_logger

logger = get_logger("vani.rag.ingestion")


def generate_deterministic_embedding(text: str, dim: int = 768) -> List[float]:
    """Generates a stable unit-length pseudo-embedding for testing or when cloud API key is absent."""
    hasher = hashlib.sha256()
    hasher.update(text.encode("utf-8"))
    seed_bytes = hasher.digest()

    raw_floats = []
    for i in range(dim):
        b = seed_bytes[i % len(seed_bytes)]
        raw_floats.append((b / 255.0) - 0.5)

    # Normalize to unit length
    magnitude = sum(x * x for x in raw_floats) ** 0.5
    if magnitude == 0:
        return [0.0] * dim
    return [x / magnitude for x in raw_floats]


class IngestionService:
    """Orchestrates parsing, sanitizing, chunking, and vector persistence."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.crawler = ScopedWebCrawler()

    async def get_embedding(self, text: str) -> List[float]:
        """Fetch embedding from Google Gemini Embedding API or fallback to deterministic vector."""
        if not self.api_key:
            return generate_deterministic_embedding(text, dim=settings.EMBEDDING_DIMENSION)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.api_key}"
        payload = {
            "model": "models/text-embedding-004",
            "content": {
                "parts": [{"text": text[:2000]}]  # truncate long chunks for embedding API
            }
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    values = data.get("embedding", {}).get("values", [])
                    if values:
                        return values
                logger.warning(f"Embedding API returned status {res.status_code}. Using local vector fallback.")
        except Exception as e:
            logger.warning(f"Error fetching remote embedding: {e}. Using local vector fallback.")

        return generate_deterministic_embedding(text, dim=settings.EMBEDDING_DIMENSION)

    async def ingest_document(
        self,
        session: AsyncSession,
        business_id: str,
        filename: str,
        content_bytes: bytes,
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ) -> Dict[str, Any]:
        """Extracts text from file, chunks it, generates embeddings, and saves to VectorStore."""
        logger.info(f"Ingesting document '{filename}' for business_id='{business_id}'")

        # 1. Parse and sanitize text
        clean_text = extract_text(filename, content_bytes)
        if not clean_text:
            return {
                "success": False,
                "message": "No extractable text found in file.",
                "chunks_created": 0
            }

        # 2. Chunk text
        chunks = chunk_text(clean_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            return {
                "success": False,
                "message": "Content too short for chunking.",
                "chunks_created": 0
            }

        # 3. Generate embeddings
        chunk_dicts = []
        for idx, text_segment in enumerate(chunks):
            embedding = await self.get_embedding(text_segment)
            chunk_dicts.append({
                "content": text_segment,
                "embedding": embedding,
                "metadata": {
                    "source": filename,
                    "chunk_index": idx,
                    "type": "document"
                }
            })

        # 4. Save to vector store (checks quota)
        created_records = await VectorStoreService.add_chunks(
            session=session,
            business_id=business_id,
            chunks=chunk_dicts
        )

        total_chunks = await VectorStoreService.get_chunk_count(session, business_id)

        return {
            "success": True,
            "filename": filename,
            "chunks_created": len(created_records),
            "total_stored_chunks": total_chunks
        }

    async def ingest_url(
        self,
        session: AsyncSession,
        business_id: str,
        target_url: str
    ) -> Dict[str, Any]:
        """Crawls website within scope limits, chunks pages, and embeds into VectorStore."""
        logger.info(f"Ingesting website URL '{target_url}' for business_id='{business_id}'")

        pages = await self.crawler.crawl_url(target_url)
        if not pages:
            return {
                "success": False,
                "message": "No accessible web pages discovered at specified URL.",
                "pages_crawled": 0,
                "chunks_created": 0
            }

        all_chunk_dicts = []
        for page in pages:
            page_chunks = chunk_text(page.content, chunk_size=500, chunk_overlap=50)
            for idx, text_seg in enumerate(page_chunks):
                emb = await self.get_embedding(text_seg)
                all_chunk_dicts.append({
                    "content": text_seg,
                    "embedding": emb,
                    "metadata": {
                        "source": page.url,
                        "title": page.title,
                        "depth": page.depth,
                        "chunk_index": idx,
                        "type": "web"
                    }
                })

        if not all_chunk_dicts:
            return {
                "success": True,
                "pages_crawled": len(pages),
                "chunks_created": 0,
                "total_stored_chunks": await VectorStoreService.get_chunk_count(session, business_id)
            }

        created = await VectorStoreService.add_chunks(
            session=session,
            business_id=business_id,
            chunks=all_chunk_dicts
        )

        total_chunks = await VectorStoreService.get_chunk_count(session, business_id)

        return {
            "success": True,
            "start_url": target_url,
            "pages_crawled": len(pages),
            "chunks_created": len(created),
            "total_stored_chunks": total_chunks
        }


ingestion_service = IngestionService()
