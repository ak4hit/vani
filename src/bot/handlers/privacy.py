"""Telegram Bot Handlers for Caller Privacy and GDPR / DPDP 'Forget Me' Compliance.

Provides business owners with the ability to permanently purge all transcripts, call logs,
and metadata for a specified caller number upon request.
"""

from telegram import Update
from telegram.ext import ContextTypes

import src.database.session as db_session
from src.services.business_store import business_store
from src.services.db_business_repository import BusinessRepository
from src.utils.logger import get_logger

logger = get_logger("vani.bot.privacy")


async def handle_delete_caller_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deletecaller <number> command to permanently purge caller records."""
    chat_id = update.effective_chat.id
    profile = business_store.get_by_chat_id(chat_id)
    if not profile:
        await update.effective_message.reply_text(
            "⚠️ Please configure your business first with /start or /onboard."
        )
        return

    if not context.args:
        help_card = (
            "🗑️ *Caller Data Deletion (GDPR / DPDP 'Forget Me')*\n\n"
            "Permanently delete all call logs, summaries, and recorded transcripts for a specific caller.\n\n"
            "📌 *Usage:*\n"
            "`/deletecaller <phone_number>`\n\n"
            "📌 *Example:*\n"
            "`/deletecaller +15551234567`\n\n"
            "⚠️ *Warning:* This action is irreversible and cascades across all database records for this caller."
        )
        await update.effective_message.reply_text(help_card, parse_mode="Markdown")
        return

    caller_number = context.args[0].strip()
    digits = [c for c in caller_number if c.isdigit()]
    if len(digits) < 5:
        await update.effective_message.reply_text(
            "❌ Invalid phone number format. Please provide a valid phone number with at least 5 digits.\n"
            "Example: `/deletecaller +15551234567`",
            parse_mode="Markdown"
        )
        return

    status_msg = await update.effective_message.reply_text("⏳ Purging caller data...")

    try:
        session_maker = db_session.get_session_maker()
        async with session_maker() as session:
            result = await BusinessRepository.delete_caller_records(
                session=session,
                business_id=profile.business_id,
                caller_number=caller_number
            )

        count = result["deleted_call_logs"]
        success_card = (
            "🗑️ *Caller Records Permanently Deleted*\n\n"
            f"• **Caller Number:** `{caller_number}`\n"
            f"• **Deleted Call Records:** {count}\n"
            f"• **Compliance Status:** Successfully Purged ✅\n\n"
            "All call logs, summaries, and transcripts associated with this caller have been permanently deleted "
            "pursuant to applicable privacy regulations (GDPR / DPDP)."
        )
        await status_msg.edit_text(success_card, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error deleting caller records for {caller_number}: {e}")
        await status_msg.edit_text(f"❌ Failed to delete caller records: {e}")
