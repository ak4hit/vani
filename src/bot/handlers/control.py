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


@rate_limited()
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the real-time operational status and configuration."""
    chat_id = update.effective_chat.id
    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    faqs = business_store.list_faqs(biz.business_id)

    status_icon = "⏸️ Paused" if biz.is_paused else "🟢 Live & Active"

    status_text = (
        "📊 *Vani Voice Bot Status Dashboard*\n\n"
        f"• *Status:* {status_icon}\n"
        f"• *Business:* {biz.business_name} ({biz.industry})\n"
        f"• *AI Persona:* {biz.agent_name}\n"
        f"• *Operating Hours:* {biz.hours}\n"
        f"• *Escalation Transfer:* {biz.escalation_number or 'Voicemail fallback'}\n"
        f"• *Active FAQs:* {len(faqs)}\n"
        f"• *Phone Forwarding:* {biz.phone_number or 'Not configured'}\n\n"
        "🔧 *Quick Commands:*\n"
        "• `/pause` or `/resume` — Toggle call answering\n"
        "• `/test <message>` — Test AI responses\n"
        "• `/addfaq` — Add knowledge Q&A\n"
        "• `/setprompt` — Edit knowledge base"
    )
    await update.message.reply_text(status_text, parse_mode="Markdown")
