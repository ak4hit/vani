"""Settings Handlers - Update business profile, system prompt, persona, hours, and escalation."""

from telegram import Update
from telegram.ext import ContextTypes

from src.services.business_store import business_store
from src.bot.rate_limiter import rate_limited
from src.utils.logger import get_logger

logger = get_logger("vani.bot.settings")


@rate_limited()
async def set_prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update the business knowledge base / description prompt."""
    chat_id = update.effective_chat.id
    prompt_text = " ".join(context.args).strip() if context.args else ""

    if not prompt_text:
        await update.message.reply_text(
            "⚠️ Please provide your business description.\n\n"
            "*Usage:* `/setprompt We are a boutique hotel in downtown Seattle offering luxury rooms...`",
            parse_mode="Markdown"
        )
        return

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.update_profile(biz.business_id, description=prompt_text)

    await update.message.reply_text(
        "✅ *Knowledge base updated successfully!*\n\n"
        f"Your AI receptionist will now use this context on incoming calls.\n"
        "Try `/test <question>` to test responses.",
        parse_mode="Markdown"
    )


@rate_limited()
async def set_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update the AI receptionist persona name."""
    chat_id = update.effective_chat.id
    agent_name = " ".join(context.args).strip() if context.args else ""

    if not agent_name:
        await update.message.reply_text(
            "⚠️ Please provide the name for your AI receptionist.\n\n"
            "*Usage:* `/setagent Priya`",
            parse_mode="Markdown"
        )
        return

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.update_profile(biz.business_id, agent_name=agent_name)

    await update.message.reply_text(
        f"✅ AI receptionist name updated to *{agent_name}*!\n"
        f"The AI will now introduce itself as {agent_name} to callers.",
        parse_mode="Markdown"
    )


@rate_limited()
async def set_hours_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update operating hours."""
    chat_id = update.effective_chat.id
    hours_text = " ".join(context.args).strip() if context.args else ""

    if not hours_text:
        await update.message.reply_text(
            "⚠️ Please provide your business hours.\n\n"
            "*Usage:* `/sethours Mon-Fri 8am-7pm, Sat 9am-3pm`",
            parse_mode="Markdown"
        )
        return

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.update_profile(biz.business_id, hours=hours_text)

    await update.message.reply_text(
        f"✅ Operating hours updated to:\n`{hours_text}`",
        parse_mode="Markdown"
    )


@rate_limited()
async def escalate_to_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the human staff escalation transfer number."""
    chat_id = update.effective_chat.id
    phone = " ".join(context.args).strip() if context.args else ""

    if not phone:
        await update.message.reply_text(
            "⚠️ Please provide a destination phone number for call transfer.\n\n"
            "*Usage:* `/escalateto +15551234567`",
            parse_mode="Markdown"
        )
        return

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.update_profile(biz.business_id, escalation_number=phone)

    await update.message.reply_text(
        f"✅ Escalation destination set to: `{phone}`\n"
        "When callers ask for a human or when the bot is paused, calls will be routed here.",
        parse_mode="Markdown"
    )


@rate_limited()
async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick setup command to update business name and industry in one line."""
    chat_id = update.effective_chat.id
    args = context.args

    if not args or len(args) < 2:
        await update.message.reply_text(
            "ℹ️ *Usage:* `/setup <Business Name> | <Industry>`\n\n"
            "*Example:* `/setup Apex Dental Clinic | Clinic`",
            parse_mode="Markdown"
        )
        return

    full_arg = " ".join(args)
    if "|" in full_arg:
        name_part, industry_part = full_arg.split("|", 1)
    else:
        name_part, industry_part = full_arg, "general"

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    business_store.update_profile(
        biz.business_id,
        business_name=name_part.strip(),
        industry=industry_part.strip()
    )

    await update.message.reply_text(
        f"✅ Business updated!\n"
        f"• *Name:* {name_part.strip()}\n"
        f"• *Industry:* {industry_part.strip()}",
        parse_mode="Markdown"
    )
