"""Control Handlers - Manage bot pause/resume operational states and view real-time status."""

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.services.business_store import business_store
from src.bot.rate_limiter import rate_limited
from src.utils.logger import get_logger

logger = get_logger("vani.bot.control")


@rate_limited()
async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pause the voice receptionist."""
    chat_id = update.effective_chat.id
    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.set_paused(biz.business_id, True)

    # Also pause the default business id for single-tenant local telephony mapping
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, True)

    logger.info(f"Bot PAUSED by admin chat_id={chat_id}")

    forward_info = (
        f"transferred to `{biz.escalation_number}`"
        if biz.escalation_number
        else "directed to voicemail"
    )

    await update.message.reply_text(
        "⏸️ *Bot Paused.*\n\n"
        "• Current active calls will complete naturally without interruption.\n"
        f"• Any new incoming calls will be {forward_info}.\n\n"
        "Send `/resume` when you are ready to reactivate AI answering.",
        parse_mode="Markdown"
    )


@rate_limited()
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume the voice receptionist."""
    chat_id = update.effective_chat.id
    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.set_paused(biz.business_id, False)

    # Also resume the default business id for local telephony mapping
    business_store.set_paused(settings.DEFAULT_BUSINESS_ID, False)

    logger.info(f"Bot RESUMED by admin chat_id={chat_id}")

    await update.message.reply_text(
        "🟢 *Bot Resumed & Active!*\n\n"
        "Your AI voice receptionist is now live and answering all incoming phone calls.",
        parse_mode="Markdown"
    )


from src.services.health_monitor import health_monitor


@rate_limited()
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the real-time operational status, service diagnostics, and health dashboard."""
    chat_id = update.effective_chat.id
    biz = business_store.get_by_chat_id(chat_id)
    if not biz:
        biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)

    status_text = await health_monitor.format_telegram_dashboard(biz)
    await update.message.reply_text(status_text, parse_mode="Markdown")
