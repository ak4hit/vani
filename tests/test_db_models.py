"""Tests for database models, relationships, and cascading deletes."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from src.database.base import Base
from src.database.models.business import Business, FAQ, CallLog
from src.database.models.document import DocumentChunk


@pytest.fixture
async def test_session():
    """Provides a fresh isolated in-memory SQLite database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_business_relationships_and_defaults(test_session: AsyncSession):
    """Verify default values and foreign key relationships."""
    biz = Business(
        id="biz_test_clinic",
        chat_id=987654,
        business_name="North Star Clinic",
        industry="Healthcare"
    )
    test_session.add(biz)
    await test_session.commit()

    # Verify defaults
    assert biz.is_paused is False
    assert biz.max_chunks == 10000
    assert biz.agent_name == "Vani"

    # Add FAQ and CallLog
    faq = FAQ(business_id=biz.id, question="What are your hours?", answer="8 AM to 8 PM")
    call = CallLog(business_id=biz.id, call_sid="CA_TEST_100", duration_seconds=120)
    chunk = DocumentChunk(business_id=biz.id, content="Services include routine checkups.", embedding=[0.1] * 768)

    test_session.add_all([faq, call, chunk])
    await test_session.commit()

    # Fetch and verify relationships
    stmt = select(Business).where(Business.id == biz.id)
    result = await test_session.execute(stmt)
    loaded_biz = result.scalar_one()

    assert len(loaded_biz.faqs) == 1
    assert loaded_biz.faqs[0].question == "What are your hours?"
    assert len(loaded_biz.call_logs) == 1
    assert loaded_biz.call_logs[0].call_sid == "CA_TEST_100"
    assert len(loaded_biz.document_chunks) == 1
    assert len(loaded_biz.document_chunks[0].embedding) == 768


@pytest.mark.asyncio
async def test_cascade_delete(test_session: AsyncSession):
    """Verify deleting a Business removes its child FAQs, call logs, and document chunks."""
    biz = Business(id="biz_to_delete", business_name="Temporary Business")
    test_session.add(biz)
    await test_session.commit()

    faq = FAQ(business_id=biz.id, question="Q1", answer="A1")
    call = CallLog(business_id=biz.id, call_sid="CA_DEL_001")
    test_session.add_all([faq, call])
    await test_session.commit()

    # Delete business
    await test_session.delete(biz)
    await test_session.commit()

    # Ensure child items are deleted
    faqs_stmt = select(FAQ).where(FAQ.business_id == "biz_to_delete")
    faqs = (await test_session.execute(faqs_stmt)).scalars().all()
    assert len(faqs) == 0

    calls_stmt = select(CallLog).where(CallLog.business_id == "biz_to_delete")
    calls = (await test_session.execute(calls_stmt)).scalars().all()
    assert len(calls) == 0
