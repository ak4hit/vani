"""Unit tests for BusinessRepository and multi-tenant isolation."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.database.base import Base
from src.services.db_business_repository import BusinessRepository


@pytest.fixture
async def repo_session():
    """Provides an isolated database session for repository testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_business_crud(repo_session: AsyncSession):
    # 1. Create
    biz = await BusinessRepository.get_or_create_business(
        repo_session,
        business_id="biz_alpha",
        chat_id=111222
    )
    assert biz.id == "biz_alpha"
    assert biz.chat_id == 111222

    # 2. Get by chat_id
    by_chat = await BusinessRepository.get_by_chat_id(repo_session, 111222)
    assert by_chat is not None
    assert by_chat.id == "biz_alpha"

    # 3. Update
    updated = await BusinessRepository.update_profile(
        repo_session,
        "biz_alpha",
        business_name="Alpha Dynamics",
        agent_name="Atlas",
        hours="9am-5pm"
    )
    assert updated.business_name == "Alpha Dynamics"
    assert updated.agent_name == "Atlas"

    # 4. Pause / Resume
    assert await BusinessRepository.is_paused(repo_session, "biz_alpha") is False
    await BusinessRepository.set_paused(repo_session, "biz_alpha", True)
    assert await BusinessRepository.is_paused(repo_session, "biz_alpha") is True
    await BusinessRepository.set_paused(repo_session, "biz_alpha", False)
    assert await BusinessRepository.is_paused(repo_session, "biz_alpha") is False


@pytest.mark.asyncio
async def test_repository_tenant_isolation_faqs(repo_session: AsyncSession):
    """Verify Business A cannot view, modify, or delete Business B's FAQs."""
    # Create Business A and Business B
    await BusinessRepository.get_or_create_business(repo_session, "biz_A")
    await BusinessRepository.get_or_create_business(repo_session, "biz_B")

    # Add FAQ for Business A
    faq_a = await BusinessRepository.add_faq(
        repo_session,
        business_id="biz_A",
        question="What is Business A's secret discount?",
        answer="Code ALPHA50"
    )

    # Add FAQ for Business B
    faq_b = await BusinessRepository.add_faq(
        repo_session,
        business_id="biz_B",
        question="What is Business B's secret discount?",
        answer="Code BETA75"
    )

    # Business A queries its FAQs: must ONLY see faq_a
    faqs_a = await BusinessRepository.list_faqs(repo_session, "biz_A")
    assert len(faqs_a) == 1
    assert faqs_a[0].question == "What is Business A's secret discount?"
    assert "BETA75" not in [f.answer for f in faqs_a]

    # Business B queries its FAQs: must ONLY see faq_b
    faqs_b = await BusinessRepository.list_faqs(repo_session, "biz_B")
    assert len(faqs_b) == 1
    assert faqs_b[0].question == "What is Business B's secret discount?"
    assert "ALPHA50" not in [f.answer for f in faqs_b]

    # Business A tries to delete Business B's FAQ: must FAIL (return False)
    delete_attempt = await BusinessRepository.remove_faq(
        repo_session,
        business_id="biz_A",
        faq_id=faq_b.id
    )
    assert delete_attempt is False

    # Verify faq_b still exists in Business B
    faqs_b_after = await BusinessRepository.list_faqs(repo_session, "biz_B")
    assert len(faqs_b_after) == 1
    assert faqs_b_after[0].id == faq_b.id


@pytest.mark.asyncio
async def test_repository_log_call(repo_session: AsyncSession):
    """Verify call logs are stored per tenant."""
    await BusinessRepository.get_or_create_business(repo_session, "biz_clinic")

    log = await BusinessRepository.log_call(
        repo_session,
        business_id="biz_clinic",
        call_sid="CA_LOG_001",
        caller_number="+15551239876",
        duration_seconds=95,
        transcript="Caller: Appointment needed. Bot: Confirmed for 2 PM.",
        summary="Scheduled appointment for 2 PM."
    )
    assert log.id is not None
    assert log.business_id == "biz_clinic"
    assert log.duration_seconds == 95
