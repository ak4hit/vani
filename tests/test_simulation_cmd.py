"""Unit tests for the /test simulation pipeline."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from src.services.business_store import business_store, BusinessProfile, FAQItem
from src.services.prompt_builder import build_system_prompt
from src.services.simulation_service import SimulationService
from src.bot.handlers.test_cmd import handle_test_command


def test_build_system_prompt():
    profile = BusinessProfile(
        business_id="biz_prompt_test",
        business_name="Greenwood Dental",
        industry="Dentistry",
        description="Comprehensive dental clinic for children and adults.",
        agent_name="Maya",
        hours="Mon-Fri 8:00 AM - 5:00 PM",
        escalation_number="+18005550199",
        faqs=[
            FAQItem(id=1, question="Do you do whitening?", answer="Yes, in-office and take-home.")
        ]
    )

    prompt = build_system_prompt(profile)
    assert "Maya" in prompt
    assert "Greenwood Dental" in prompt
    assert "Dentistry" in prompt
    assert "Mon-Fri 8:00 AM - 5:00 PM" in prompt
    assert "+18005550199" in prompt
    assert "Do you do whitening?" in prompt
    assert "in-office and take-home" in prompt
    assert "Never collect, store, or ask for sensitive payment card details" in prompt


@pytest.mark.asyncio
async def test_simulation_service_run_query():
    business_store.reset()
    biz = business_store.get_or_create_business("biz_sim", chat_id=777)
    business_store.update_profile(
        biz.business_id,
        business_name="Sunrise Cafe",
        agent_name="Sam"
    )

    class MockLLM:
        def __init__(self, system_prompt: str = None):
            self.system_prompt = system_prompt

        async def stream_response(self, query: str):
            yield "We open "
            yield "at 7 AM every day!"

    service = SimulationService(llm_service_cls=MockLLM)
    result = await service.run_test_query(
        user_message="When do you open?",
        chat_id=777
    )

    assert result["success"] is True
    assert result["business_name"] == "Sunrise Cafe"
    assert result["agent_name"] == "Sam"
    assert result["response"] == "We open at 7 AM every day!"


@pytest.mark.asyncio
async def test_test_command_handler():
    chat_id = 888
    business_store.reset()
    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.update_profile(biz.business_id, business_name="City Spa", agent_name="Aria")

    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message = MagicMock()
    update.message = update.effective_message

    placeholder_msg = MagicMock()
    placeholder_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=placeholder_msg)

    context = MagicMock()
    context.args = ["Do", "you", "offer", "massages?"]

    await handle_test_command(update, context)

    update.message.reply_text.assert_called_once()
    placeholder_msg.edit_text.assert_called_once()
    edit_text_arg = placeholder_msg.edit_text.call_args[0][0]
    assert "Aria — Simulation Reply" in edit_text_arg
