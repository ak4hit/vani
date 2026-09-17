"""Vector Store Service - Manages document chunk embeddings with strict tenant isolation and quota enforcement."""

import math
from typing import Any, Dict, List, Optional
import numpy as np
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.business import Business
from src.database.models.document import DocumentChunk
from src.utils.logger import get_logger

logger = get_logger("vani.vector_store")


class EmbeddingQuotaExceededError(Exception):
    """Raised when a business exceeds its configured max_chunks embedding cap."""
    pass


class VectorStoreService:
    """Manages document chunk storage, similarity search, and per-tenant quotas."""

    @staticmethod
    async def get_chunk_count(session: AsyncSession, business_id: str) -> int:
        """Count total stored document chunks for a specific business."""
        stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.business_id == business_id)
        result = await session.execute(stmt)
        return result.scalar_one() or 0

    @staticmethod
    async def add_chunks(
        session: AsyncSession,
        business_id: str,
        chunks: List[Dict[str, Any]]
    ) -> List[DocumentChunk]:
        """Insert new document chunks with embedding quota verification.
        
        Each chunk dict must have 'content', optional 'embedding' (list of floats), and optional 'metadata'.
        """
        # Fetch business to check max_chunks limit
        stmt = select(Business).where(Business.id == business_id)
        biz_res = await session.execute(stmt)
        biz = biz_res.scalar_one_or_none()

        max_limit = biz.max_chunks if biz else 10000
        current_count = await VectorStoreService.get_chunk_count(session, business_id)

        if current_count + len(chunks) > max_limit:
            raise EmbeddingQuotaExceededError(
                f"Embedding quota exceeded for business '{business_id}'. "
                f"Current chunks: {current_count}, adding: {len(chunks)}, limit: {max_limit}."
            )

        created_chunks = []
        for c in chunks:
            chunk = DocumentChunk(
                business_id=business_id,
                content=c["content"],
                metadata_json=c.get("metadata", {}),
                embedding=c.get("embedding")
            )
            session.add(chunk)
            created_chunks.append(chunk)

        await session.commit()
        for chunk in created_chunks:
            await session.refresh(chunk)

        logger.info(
            f"Stored {len(created_chunks)} document chunks for business_id='{business_id}' "
            f"(total now: {current_count + len(created_chunks)}/{max_limit})"
        )
        return created_chunks

    @staticmethod
    async def search(
        session: AsyncSession,
        business_id: str,
        query_embedding: List[float],
        top_k: int = 5,
        score_threshold: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Search document chunks strictly isolated to business_id.
        
        CRITICAL COMPLIANCE RULE:
        Every vector similarity search MUST be filtered by `WHERE business_id = :business_id`.
        """
        engine = session.bind

        # 1. If PostgreSQL with pgvector is active
        if engine and engine.dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector
            stmt = (
                select(
                    DocumentChunk,
                    (1 - DocumentChunk.embedding.cosine_distance(query_embedding)).label("score")
                )
                .where(DocumentChunk.business_id == business_id)
                .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
                .limit(top_k)
            )
            result = await session.execute(stmt)
            matches = []
            for chunk, score in result:
                if score >= score_threshold:
                    matches.append({
                        "id": chunk.id,
                        "business_id": chunk.business_id,
                        "content": chunk.content,
                        "metadata": chunk.metadata_json or {},
                        "score": float(score)
                    })
            return matches

        # 2. Universal / SQLite fallback using high-performance numpy cosine similarity
        stmt = select(DocumentChunk).where(DocumentChunk.business_id == business_id)
        result = await session.execute(stmt)
        chunks = list(result.scalars().all())

        if not chunks:
            return []

        q_vec = np.array(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return []

        scored_chunks = []
        for chunk in chunks:
            if not chunk.embedding:
                continue
            c_vec = np.array(chunk.embedding, dtype=np.float32)
            c_norm = np.linalg.norm(c_vec)
            if c_norm == 0:
                continue
            cos_sim = float(np.dot(q_vec, c_vec) / (q_norm * c_norm))
            if cos_sim >= score_threshold:
                scored_chunks.append({
                    "id": chunk.id,
                    "business_id": chunk.business_id,
                    "content": chunk.content,
                    "metadata": chunk.metadata_json or {},
                    "score": cos_sim
                })

        scored_chunks.sort(key=lambda x: x["score"], reverse=True)
        return scored_chunks[:top_k]

    @staticmethod
    async def delete_all_chunks(session: AsyncSession, business_id: str) -> int:
        """Remove all document chunks for a specific business."""
        stmt = delete(DocumentChunk).where(DocumentChunk.business_id == business_id)
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount
