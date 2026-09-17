"""Critical Integration Tests: Cross-Tenant RAG Isolation and Embedding Quota Enforcement.

Mandatory Compliance Gate (Phase 4):
Verifies that multi-tenant vector searches strictly isolate documents by business_id,
preventing any possibility of cross-tenant information leaks.
"""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.database.base import Base
from src.database.models.business import Business
from src.services.vector_store import VectorStoreService, EmbeddingQuotaExceededError
from src.services.business_store import business_store


@pytest.fixture
async def isolation_session():
    """Provides an isolated database session for tenant isolation testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_vector_search_isolation(isolation_session: AsyncSession):
    """MANDATORY SECURITY GATE:
    Verifies that Business A CANNOT retrieve any document chunks belonging to Business B,
    even when vector embeddings are identical or highly similar.
    """
    # 1. Create two distinct tenants
    biz_clinic = Business(id="biz_clinic_001", business_name="Metro Clinic")
    biz_hotel = Business(id="biz_hotel_002", business_name="Grandview Hotel")
    isolation_session.add_all([biz_clinic, biz_hotel])
    await isolation_session.commit()

    # Identical vector representing a general query concept
    shared_concept_vector = [0.0] * 768
    shared_concept_vector[0] = 1.0
    shared_concept_vector[1] = 0.5

    # 2. Ingest confidential documents for both tenants
    clinic_chunk = {
        "content": "Confidential Clinic Data: Patient John Doe scheduled for Cardiology with Dr. Evans.",
        "embedding": shared_concept_vector,
        "metadata": {"doc_type": "patient_schedule"}
    }
    hotel_chunk = {
        "content": "Confidential Hotel Data: VIP Presidential Suite door passcode is 9876.",
        "embedding": shared_concept_vector,
        "metadata": {"doc_type": "security_code"}
    }

    await VectorStoreService.add_chunks(isolation_session, "biz_clinic_001", [clinic_chunk])
    await VectorStoreService.add_chunks(isolation_session, "biz_hotel_002", [hotel_chunk])

    # 3. Query as Clinic Tenant: MUST ONLY RETURN CLINIC CHUNKS
    clinic_results = await VectorStoreService.search(
        isolation_session,
        business_id="biz_clinic_001",
        query_embedding=shared_concept_vector,
        top_k=10
    )
    assert len(clinic_results) == 1
    assert "Cardiology" in clinic_results[0]["content"]
    assert "Presidential Suite" not in clinic_results[0]["content"]
    assert "9876" not in clinic_results[0]["content"]

    # 4. Query as Hotel Tenant: MUST ONLY RETURN HOTEL CHUNKS
    hotel_results = await VectorStoreService.search(
        isolation_session,
        business_id="biz_hotel_002",
        query_embedding=shared_concept_vector,
        top_k=10
    )
    assert len(hotel_results) == 1
    assert "Presidential Suite" in hotel_results[0]["content"]
    assert "Cardiology" not in hotel_results[0]["content"]
    assert "John Doe" not in hotel_results[0]["content"]

    # 5. Query as Non-Existent Tenant: MUST RETURN 0 RESULTS
    unknown_results = await VectorStoreService.search(
        isolation_session,
        business_id="biz_unknown_999",
        query_embedding=shared_concept_vector,
        top_k=10
    )
    assert len(unknown_results) == 0


@pytest.mark.asyncio
async def test_embedding_quota_enforcement(isolation_session: AsyncSession):
    """Verify that per-business max_chunks prevents unbounded document ingestion."""
    # Create business with quota limit of 3 chunks
    biz = Business(id="biz_quota_test", business_name="Small Business", max_chunks=3)
    isolation_session.add(biz)
    await isolation_session.commit()

    dummy_vector = [0.1] * 768

    # Ingest 2 chunks (allowed)
    chunks_1 = [
        {"content": "Chunk 1", "embedding": dummy_vector},
        {"content": "Chunk 2", "embedding": dummy_vector},
    ]
    await VectorStoreService.add_chunks(isolation_session, "biz_quota_test", chunks_1)
    assert await VectorStoreService.get_chunk_count(isolation_session, "biz_quota_test") == 2

    # Ingest 1 chunk (reaches limit of 3)
    chunks_2 = [{"content": "Chunk 3", "embedding": dummy_vector}]
    await VectorStoreService.add_chunks(isolation_session, "biz_quota_test", chunks_2)
    assert await VectorStoreService.get_chunk_count(isolation_session, "biz_quota_test") == 3

    # Attempt to ingest 1 more chunk: MUST RAISE EmbeddingQuotaExceededError
    chunks_overflow = [{"content": "Chunk 4 (Overflow)", "embedding": dummy_vector}]
    with pytest.raises(EmbeddingQuotaExceededError) as exc_info:
        await VectorStoreService.add_chunks(isolation_session, "biz_quota_test", chunks_overflow)

    assert "quota exceeded" in str(exc_info.value).lower()
    # Ensure count remained capped at 3
    assert await VectorStoreService.get_chunk_count(isolation_session, "biz_quota_test") == 3


@pytest.mark.asyncio
async def test_tenant_chunk_deletion_isolation(isolation_session: AsyncSession):
    """Verify deleting chunks for Tenant A leaves Tenant B's chunks completely untouched."""
    biz_a = Business(id="biz_del_A", business_name="Tenant A")
    biz_b = Business(id="biz_del_B", business_name="Tenant B")
    isolation_session.add_all([biz_a, biz_b])
    await isolation_session.commit()

    vec = [0.2] * 768
    await VectorStoreService.add_chunks(isolation_session, "biz_del_A", [{"content": "A doc", "embedding": vec}])
    await VectorStoreService.add_chunks(isolation_session, "biz_del_B", [{"content": "B doc", "embedding": vec}])

    # Delete all chunks for Tenant A
    deleted_count = await VectorStoreService.delete_all_chunks(isolation_session, "biz_del_A")
    assert deleted_count == 1

    # Verify Tenant A has 0 chunks
    assert await VectorStoreService.get_chunk_count(isolation_session, "biz_del_A") == 0

    # Verify Tenant B STILL has 1 chunk
    assert await VectorStoreService.get_chunk_count(isolation_session, "biz_del_B") == 1
    results_b = await VectorStoreService.search(isolation_session, "biz_del_B", vec)
    assert len(results_b) == 1
    assert results_b[0]["content"] == "B doc"


@pytest.mark.asyncio
async def test_business_store_persistence_bridge(isolation_session: AsyncSession):
    """Verify BusinessStore load_from_db and save_to_db methods."""
    biz = Business(
        id="biz_bridge_test",
        chat_id=123999,
        business_name="Bridge Healthcare",
        agent_name="Sam",
        is_paused=True
    )
    isolation_session.add(biz)
    await isolation_session.commit()

    # 1. Load into BusinessStore
    loaded_profile = await business_store.load_from_db(isolation_session, "biz_bridge_test")
    assert loaded_profile is not None
    assert loaded_profile.business_name == "Bridge Healthcare"
    assert loaded_profile.agent_name == "Sam"
    assert loaded_profile.is_paused is True

    # 2. Modify in BusinessStore and save back to DB
    business_store.update_profile("biz_bridge_test", business_name="Bridge Health Modern")
    await business_store.save_to_db(isolation_session, "biz_bridge_test")

    # 3. Reload directly from DB and assert update persisted
    reloaded = await business_store.load_from_db(isolation_session, "biz_bridge_test")
    assert reloaded.business_name == "Bridge Health Modern"
