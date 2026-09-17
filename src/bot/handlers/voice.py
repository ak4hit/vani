"""Telegram Bot Handlers for Voice Cloning and Voice Design.

Enforces legal consent verification workflows for voice cloning per Texas SB 140 and TCPA.
"""

from io import BytesIO
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.services.business_store import business_store
from src.services.voice_service import (
    voice_service,
    LEGAL_VOICE_CONSENT_STATEMENT,
    VoiceConsentRequiredError,
)
from src.utils.logger import get_logger

logger = get_logger("vani.bot.voice")


async def handle_upload_voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /uploadvoice command providing instructions on cloning voice samples."""
    chat_id = update.effective_chat.id
    profile = business_store.get_by_chat_id(chat_id)
    if not profile:
        await update.effective_message.reply_text(
            "⚠️ Please complete onboarding first with /start or /onboard."
        )
        return

    text = (
        "🎙️ *Voice Cloning Setup*\n\n"
        "You can clone your receptionist's voice so Vani sounds just like your team!\n\n"
        "📌 *Requirements:*\n"
        "1. Send an audio file (`.mp3`, `.wav`, `.m4a`) or record a Telegram voice note.\n"
        "2. Length: **10 to 30 seconds** of clear, uninterrupted speech.\n"
        "3. Minimal background noise.\n\n"
        "⚖️ *Legal Notice:*\n"
        "State and federal laws require explicit consent from the person being cloned. "
        "You will be required to confirm consent before the voice is activated.\n\n"
        "👉 *Send your audio file or voice note now to begin.*"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def handle_voice_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming voice note or audio file for voice cloning."""
    chat_id = update.effective_chat.id
    profile = business_store.get_by_chat_id(chat_id)
    if not profile:
        await update.effective_message.reply_text(
            "⚠️ Please configure your business first with /start."
        )
        return

    message = update.effective_message
    file_id = None
    filename = "voice_sample.mp3"

    if message.voice:
        file_id = message.voice.file_id
        filename = f"voice_note_{message.voice.file_unique_id}.ogg"
    elif message.audio:
        file_id = message.audio.file_id
        filename = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"
    elif message.document and (message.document.mime_type or "").startswith("audio/"):
        file_id = message.document.file_id
        filename = message.document.file_name or "uploaded_sample.mp3"

    if not file_id:
        return

    try:
        status_msg = await message.reply_text("⏳ Downloading voice sample...")
        tg_file = await context.bot.get_file(file_id)
        buffer = BytesIO()
        await tg_file.download_to_memory(buffer)
        audio_bytes = buffer.getvalue()

        if len(audio_bytes) < 1000:
            await status_msg.edit_text(
                "❌ Audio sample is too short. Please provide a 10-30 second clear voice recording."
            )
            return

        # Store pending sample in user_data awaiting consent confirmation
        context.user_data["pending_voice_bytes"] = audio_bytes
        context.user_data["pending_voice_filename"] = filename
        context.user_data["pending_business_id"] = profile.business_id

        keyboard = [
            [
                InlineKeyboardButton("✅ I Confirm Legal Consent", callback_data="consent_voice_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="consent_voice_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        consent_card = (
            "⚖️ *Mandatory Voice Cloning Consent Confirmation*\n\n"
            f"> \"{LEGAL_VOICE_CONSENT_STATEMENT}\"\n\n"
            "By clicking **'I Confirm Legal Consent'**, you create an immutable audit record "
            "with your Telegram User ID and timestamp certifying that you have legal permission "
            "to synthesize this voice."
        )

        await status_msg.edit_text(consent_card, reply_markup=reply_markup, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error handling audio upload: {e}")
        await message.reply_text(f"❌ Failed to process audio sample: {e}")


async def handle_voice_consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback button clicks for voice cloning consent confirmation."""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = update.effective_chat.id

    if data == "consent_voice_cancel":
        context.user_data.pop("pending_voice_bytes", None)
        context.user_data.pop("pending_voice_filename", None)
        context.user_data.pop("pending_business_id", None)
        await query.edit_message_text("❌ Voice cloning cancelled. The uploaded sample was discarded.")
        return

    if data == "consent_voice_confirm":
        audio_bytes = context.user_data.get("pending_voice_bytes")
        filename = context.user_data.get("pending_voice_filename", "sample.mp3")
        business_id = context.user_data.get("pending_business_id")

        if not audio_bytes or not business_id:
            await query.edit_message_text("⚠️ No pending voice sample found. Please send the audio file again.")
            return

        await query.edit_message_text("⏳ Synthesizing and activating custom voice with verified consent...")

        try:
            result = await voice_service.clone_voice_from_audio(
                business_id=business_id,
                audio_bytes=audio_bytes,
                filename=filename,
                voice_name=f"Cloned Voice for {business_id}",
                consent_confirmed=True,
                chat_id=chat_id
            )

            # Clear temporary buffer
            context.user_data.pop("pending_voice_bytes", None)
            context.user_data.pop("pending_voice_filename", None)
            context.user_data.pop("pending_business_id", None)

            success_card = (
                "🎉 *Voice Cloned and Activated Successfully!*\n\n"
                f"• **Voice ID:** `{result['voice_id']}`\n"
                f"• **Consent Status:** Logged & Verified ✅\n"
                f"• **Audit Timestamp:** `{result['consent_timestamp']}`\n\n"
                "Your AI receptionist will now use this custom cloned voice for incoming calls!"
            )
            await query.edit_message_text(success_card, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Failed to clone voice after consent: {e}")
            await query.edit_message_text(f"❌ Failed to activate cloned voice: {e}")


async def handle_voice_design_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /voicedesign <prompt> command for instant text-based voice generation."""
    chat_id = update.effective_chat.id
    profile = business_store.get_by_chat_id(chat_id)
    if not profile:
        await update.effective_message.reply_text(
            "⚠️ Please configure your business first with /start."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "✨ *Voice Design (Text-to-Voice)*\n\n"
            "Create a unique custom voice from natural language without uploading any audio!\n\n"
            "*Usage:*\n"
            "`/voicedesign <description>`\n\n"
            "*Example:*\n"
            "`/voicedesign Warm, friendly, calm receptionist with an articulate American accent`",
            parse_mode="Markdown"
        )
        return

    description = " ".join(context.args).strip()
    status_msg = await update.effective_message.reply_text("⏳ Generating custom voice from description...")

    try:
        result = await voice_service.design_voice_from_description(
            business_id=profile.business_id,
            description=description,
            chat_id=chat_id
        )

        reply_card = (
            "✨ *Voice Designed & Activated!*\n\n"
            f"• **Voice ID:** `{result['voice_id']}`\n"
            f"• **Description:** \"{description}\"\n\n"
            "Your receptionist has been updated to use this custom synthesized persona."
        )
        await status_msg.edit_text(reply_card, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in /voicedesign: {e}")
        await status_msg.edit_text(f"❌ Voice design failed: {e}")
