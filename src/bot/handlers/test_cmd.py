"""Test Command Handler - Execute text queries through the LLM prompt pipeline."""

from telegram import Update
from telegram.ext import ContextTypes

from src.services.simulation_service import simulation_service
from src.bot.rate_limiter import rate_limited
from src.utils.logger import get_logger

logger = get_logger("vani.bot.test_cmd")


@rate_limited(max_calls=15, window_seconds=60)
async def handle_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send text through the LLM + business knowledge base pipeline without telephony."""
    chat_id = update.effective_chat.id
    query = " ".join(context.args).strip() if context.args else ""

    if not query:
        await update.message.reply_text(
            "⚠️ Please provide a question to test.\n\n"
            "*Usage:* `/test What are your operating hours?`\n"
            "*(This runs your prompt + FAQs through the AI brain without placing a phone call)*",
            parse_mode="Markdown"
        )
        return

    # Send processing indicator
    placeholder = await update.message.reply_text("🤔 _Thinking..._", parse_mode="Markdown")

    try:
        result = await simulation_service.run_test_query(
            user_message=query,
            chat_id=chat_id
        )

        response_text = (
            f"🤖 *[{result['agent_name']} — Simulation Reply]*\n\n"
            f"{result['response']}\n\n"
            f"_Context: {result['business_name']} | FAQs consulted: active_"
        )

        await placeholder.edit_text(response_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error running /test simulation for chat_id={chat_id}: {e}")
        await placeholder.edit_text(
            f"❌ *Error evaluating query:* `{str(e)}`",
            parse_mode="Markdown"
        )
