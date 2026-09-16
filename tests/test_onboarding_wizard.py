"""Unit tests for the Telegram Guided Onboarding Wizard."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram.ext import ConversationHandler

from src.services.business_store import business_store
from src.bot.handlers.onboarding import (
    start_onboarding,
    step_name,
    step_industry,
    step_description,
    step_phone,
    skip_phone,
    step_hours,
    cancel_onboarding,
    STEP_NAME,
    STEP_INDUSTRY,
    STEP_DESCRIPTION,
    STEP_PHONE,
    STEP_HOURS,
)


def create_mock_update(chat_id: int = 9999, text: str = ""):
    """Helper to create mock Telegram Update objects."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture(autouse=True)
def clean_store():
    business_store.reset()
    yield
    business_store.reset()


@pytest.mark.asyncio
async def test_onboarding_full_flow():
    chat_id = 1001
    context = MagicMock()

    # Step 0: Start onboarding
    update = create_mock_update(chat_id=chat_id, text="/onboard")
    next_state = await start_onboarding(update, context)
    assert next_state == STEP_NAME
    update.message.reply_text.assert_called_once()
    assert "Step 1 of 5" in update.message.reply_text.call_args[0][0]

    # Step 1: Provide Name
    update = create_mock_update(chat_id=chat_id, text="Metro Health Clinic")
    next_state = await step_name(update, context)
    assert next_state == STEP_INDUSTRY
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.business_name == "Metro Health Clinic"

    # Step 2: Provide Industry
    update = create_mock_update(chat_id=chat_id, text="Clinic")
    next_state = await step_industry(update, context)
    assert next_state == STEP_DESCRIPTION
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.industry == "Clinic"

    # Step 3: Provide Description
    desc = "We provide outpatient urgent care and preventive health checkups."
    update = create_mock_update(chat_id=chat_id, text=desc)
    next_state = await step_description(update, context)
    assert next_state == STEP_PHONE
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.description == desc

    # Step 4: Provide Phone
    update = create_mock_update(chat_id=chat_id, text="+1 555-0100")
    next_state = await step_phone(update, context)
    assert next_state == STEP_HOURS
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.phone_number == "+1 555-0100"

    # Step 5: Provide Operating Hours
    update = create_mock_update(chat_id=chat_id, text="Mon-Fri 8am-5pm")
    next_state = await step_hours(update, context)
    assert next_state == ConversationHandler.END
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.hours == "Mon-Fri 8am-5pm"
    assert biz.onboarding_completed is True


@pytest.mark.asyncio
async def test_onboarding_skip_phone():
    chat_id = 1002
    context = MagicMock()

    update = create_mock_update(chat_id=chat_id, text="/skip")
    next_state = await skip_phone(update, context)
    assert next_state == STEP_HOURS


@pytest.mark.asyncio
async def test_onboarding_cancel():
    chat_id = 1003
    context = MagicMock()

    update = create_mock_update(chat_id=chat_id, text="/cancel")
    next_state = await cancel_onboarding(update, context)
    assert next_state == ConversationHandler.END
    update.message.reply_text.assert_called_once()
    assert "cancelled" in update.message.reply_text.call_args[0][0].lower()
