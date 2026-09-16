"""Simulation Service - Executes text-only conversational queries against LLM with business context.

Allows business owners to test their configuration, prompt, and FAQs without telephony or audio overhead.
"""

from typing import Dict, Any, Optional
from src.services.business_store import business_store, BusinessProfile
from src.services.prompt_builder import build_system_prompt
from src.services.llm_service import GeminiLLMService
from src.utils.logger import get_logger

logger = get_logger("vani.simulation")


class SimulationService:
    """Runs test queries through the LLM + business context pipeline without telephony."""

    def __init__(self, llm_service_cls=GeminiLLMService):
        self.llm_service_cls = llm_service_cls

    async def run_test_query(
        self,
        user_message: str,
        business_id: Optional[str] = None,
        chat_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Process a simulated user message against the business's prompt and FAQs."""
        profile: Optional[BusinessProfile] = None

        if chat_id is not None:
            profile = business_store.get_by_chat_id(chat_id)

        if not profile and business_id:
            profile = business_store.get_business(business_id)

        if not profile:
            # Fallback or default profile
            profile = business_store.get_or_create_business(
                business_id=business_id or "biz_default",
                chat_id=chat_id
            )

        system_prompt = build_system_prompt(profile)
        llm = self.llm_service_cls(system_prompt=system_prompt)

        tokens = []
        async for chunk in llm.stream_response(user_message):
            tokens.append(chunk)

        response_text = "".join(tokens).strip()

        logger.info(
            f"Simulation test completed for business='{profile.business_name}' "
            f"query='{user_message[:40]}...' response_len={len(response_text)}"
        )

        return {
            "success": True,
            "business_id": profile.business_id,
            "business_name": profile.business_name,
            "agent_name": profile.agent_name,
            "query": user_message,
            "response": response_text,
            "prompt_used": system_prompt
        }


simulation_service = SimulationService()
