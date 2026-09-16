"""Unit tests for Telegram admin commands (settings, FAQ, control, and rate limiting)."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from src.services.business_store import business_store
from src.bot.rate_limiter import RateLimiter, bot_rate_limiter
from src.bot.handlers.settings import (
    set_prompt_command,
    set_agent_command,
    set_hours_command,
    escalate_to_command,
    setup_command,
)
from src.bot.handlers.faq import (
    add_faq_command,
    list_faqs_command,
    remove_faq_command,
)
from src.bot.handlers.control import (
    pause_command,
    resume_command,
    status_command,
)


def create_mock_update(chat_id: int = 5001):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    update.message = update.effective_message
    return update


@pytest.fixture(autouse=True)
def reset_all():
    business_store.reset()
    bot_rate_limiter.reset()
    yield
    business_store.reset()
    bot_rate_limiter.reset()


@pytest.mark.asyncio
async def test_set_prompt_command():
    chat_id = 5001
    update = create_mock_update(chat_id)
    context = MagicMock()
    context.args = ["We", "are", "an", "organic", "bakery."]

    await set_prompt_command(update, context)
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.description == "We are an organic bakery."
    assert "updated successfully" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_set_agent_command():
    chat_id = 5002
    update = create_mock_update(chat_id)
    context = MagicMock()
    context.args = ["Priya"]

    await set_agent_command(update, context)
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.agent_name == "Priya"


@pytest.mark.asyncio
async def test_set_hours_and_escalateto():
    chat_id = 5003
    update = create_mock_update(chat_id)
    context = MagicMock()

    context.args = ["9am-9pm"]
    await set_hours_command(update, context)

    context.args = ["+14155551234"]
    await escalate_to_command(update, context)

    biz = business_store.get_by_chat_id(chat_id)
    assert biz.hours == "9am-9pm"
    assert biz.escalation_number == "+14155551234"


@pytest.mark.asyncio
async def test_setup_command():
    chat_id = 5004
    update = create_mock_update(chat_id)
    context = MagicMock()
    context.args = ["Sunny", "Bistro", "|", "Restaurant"]

    await setup_command(update, context)
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.business_name == "Sunny Bistro"
    assert biz.industry == "Restaurant"


@pytest.mark.asyncio
async def test_faq_lifecycle():
    chat_id = 5005
    update = create_mock_update(chat_id)
    context = MagicMock()

    # 1. Add FAQ
    context.args = ["Do", "you", "deliver?", "|", "Yes,", "via", "DoorDash."]
    await add_faq_command(update, context)
    biz = business_store.get_by_chat_id(chat_id)
    assert len(biz.faqs) == 1
    assert biz.faqs[0].question == "Do you deliver?"

    # 2. List FAQs
    await list_faqs_command(update, context)
    assert "Current FAQs" in update.message.reply_text.call_args[0][0]

    # 3. Remove FAQ
    context.args = [str(biz.faqs[0].id)]
    await remove_faq_command(update, context)
    assert len(biz.faqs) == 0


@pytest.mark.asyncio
async def test_pause_resume_and_status():
    chat_id = 5006
    update = create_mock_update(chat_id)
    context = MagicMock()

    # Pause
    await pause_command(update, context)
    biz = business_store.get_by_chat_id(chat_id)
    assert biz.is_paused is True
    assert "Bot Paused" in update.message.reply_text.call_args[0][0]

    # Status
    await status_command(update, context)
    assert "Paused" in update.message.reply_text.call_args[0][0]

    # Resume
    await resume_command(update, context)
    assert biz.is_paused is False
    assert "Bot Resumed" in update.message.reply_text.call_args[0][0]


def test_rate_limiter():
    limiter = RateLimiter(max_calls=3, window_seconds=10)
    chat_id = 9999

    assert limiter.is_allowed(chat_id) is True
    assert limiter.is_allowed(chat_id) is True
    assert limiter.is_allowed(chat_id) is True
    # 4th call should be blocked
    assert limiter.is_allowed(chat_id) is False
