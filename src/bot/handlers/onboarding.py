"""Onboarding Handler - Guided 6-step setup wizard for new business onboarding."""

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.config import settings
from src.services.business_store import business_store
from src.utils.logger import get_logger

logger = get_logger("vani.bot.onboarding")

# Conversation States
(
    STEP_NAME,
    STEP_INDUSTRY,
    STEP_DESCRIPTION,
    STEP_PHONE,
    STEP_HOURS,
) = range(5)


async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /start and /onboard. Initiates setup wizard."""
    chat_id = update.effective_chat.id
    biz_id = f"biz_{chat_id}"
    business = business_store.get_or_create_business(business_id=biz_id, chat_id=chat_id)

    # Also link default business id if needed
    business_store.link_chat_id(chat_id, biz_id)

    welcome_msg = (
        "👋 *Welcome to Vani AI Voice Receptionist!*\n\n"
        "I'll help you configure your intelligent AI receptionist in just a few quick steps.\n\n"
        "*Step 1 of 5:* What is your *business name*?\n"
        "_(Example: Apex Dental Clinic)_"
    )
    await update.message.reply_text(
        welcome_msg,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return STEP_NAME


async def step_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save business name and ask for industry."""
    chat_id = update.effective_chat.id
    name = update.message.text.strip()

    business_store.update_profile(f"biz_{chat_id}", business_name=name)

    industry_keyboard = [["Clinic", "Restaurant"], ["Hotel", "Retail"], ["Other"]]
    reply_markup = ReplyKeyboardMarkup(industry_keyboard, one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        f"Great! Business name set to: *{name}*.\n\n"
        "*Step 2 of 5:* What *industry* does your business belong to?\n"
        "Select an option below or type your own.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return STEP_INDUSTRY


async def step_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save industry and ask for business description / knowledge base."""
    chat_id = update.effective_chat.id
    industry = update.message.text.strip()

    business_store.update_profile(f"biz_{chat_id}", industry=industry)

    await update.message.reply_text(
        f"Got it! Industry set to *{industry}*.\n\n"
        "*Step 3 of 5:* Describe your business in a few sentences.\n"
        "I will use this description as my foundational *knowledge base* to answer customer questions.\n\n"
        "_(Example: We are a family dental practice offering cleanings, fillings, and emergency dental care. We accept most major insurances.)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return STEP_DESCRIPTION


async def step_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save business description and ask for phone number (optional)."""
    chat_id = update.effective_chat.id
    description = update.message.text.strip()

    business_store.update_profile(f"biz_{chat_id}", description=description)

    await update.message.reply_text(
        "Knowledge base updated! 🧠\n\n"
        "*Step 4 of 5:* What is your *business phone number*?\n"
        "We'll use this for customer caller ID and call forwarding.\n\n"
        "_(Send your phone number e.g. `+1 555-0199`, or type `/skip` to skip for now)_",
        parse_mode="Markdown"
    )
    return STEP_PHONE


async def step_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save phone number and ask for operating hours."""
    chat_id = update.effective_chat.id
    phone = update.message.text.strip()

    business_store.update_profile(f"biz_{chat_id}", phone_number=phone)

    await update.message.reply_text(
        f"Phone number saved: `{phone}`\n\n"
        "*Step 5 of 5:* What are your *operating business hours*?\n"
        "_(Example: Mon–Fri 9:00 AM – 6:00 PM, Sat 10:00 AM – 2:00 PM)_",
        parse_mode="Markdown"
    )
    return STEP_HOURS


async def skip_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip phone number step."""
    await update.message.reply_text(
        "Phone number skipped.\n\n"
        "*Step 5 of 5:* What are your *operating business hours*?\n"
        "_(Example: Mon–Fri 9:00 AM – 6:00 PM, Sat 10:00 AM – 2:00 PM)_",
        parse_mode="Markdown"
    )
    return STEP_HOURS


async def step_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save operating hours and display onboarding summary card."""
    chat_id = update.effective_chat.id
    hours = update.message.text.strip()

    biz = business_store.update_profile(
        f"biz_{chat_id}",
        hours=hours,
        onboarding_completed=True
    )

    summary_card = (
        "🎉 *Setup Complete! Your AI Receptionist is Ready!*\n\n"
        "📋 *Business Profile Summary:*\n"
        f"• *Name:* {biz.business_name}\n"
        f"• *Industry:* {biz.industry}\n"
        f"• *Agent Persona:* {biz.agent_name}\n"
        f"• *Hours:* {biz.hours}\n"
        f"• *Phone:* {biz.phone_number or 'Not set'}\n"
        f"• *Escalation:* {biz.escalation_number or 'Not set'}\n"
        f"• *Status:* {'⏸️ Paused' if biz.is_paused else '🟢 Active'}\n\n"
        "🚀 *Recommended Next Steps:*\n"
        "1. `/test <question>` — Test how your AI answers questions right in Telegram!\n"
        "2. `/addfaq <question> | <answer>` — Add custom Q&A pairs\n"
        "3. `/escalateto <number>` — Set a staff phone number to transfer calls\n"
        "4. `/pause` or `/resume` — Control whether the AI answers incoming calls\n"
        "5. `/status` — View your real-time bot settings"
    )
    await update.message.reply_text(summary_card, parse_mode="Markdown")
    return ConversationHandler.END


async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the setup wizard."""
    await update.message.reply_text(
        "Onboarding cancelled. You can resume anytime by typing `/onboard` or `/start`.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def get_onboarding_conversation_handler() -> ConversationHandler:
    """Construct the ConversationHandler for the onboarding wizard."""
    return ConversationHandler(
        entry_points=[
            CommandHandler(["start", "onboard"], start_onboarding),
        ],
        states={
            STEP_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_name)
            ],
            STEP_INDUSTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_industry)
            ],
            STEP_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_description)
            ],
            STEP_PHONE: [
                CommandHandler("skip", skip_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_phone)
            ],
            STEP_HOURS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_hours)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_onboarding)
        ],
        allow_reentry=True
    )
