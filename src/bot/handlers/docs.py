"""Document & Web Ingestion Handlers - Telegram commands for uploading PDFs/DOCX and crawling websites."""

from telegram import Update
from telegram.ext import ContextTypes

from src.database.session import get_session_maker
from src.services.business_store import business_store
from src.services.rag.ingestion_service import ingestion_service
from src.services.vector_store import EmbeddingQuotaExceededError
from src.bot.rate_limiter import rate_limited
from src.utils.logger import get_logger

logger = get_logger("vani.bot.docs")


@rate_limited()
async def upload_doc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explains how to upload documents to the knowledge base."""
    await update.message.reply_text(
        "📄 *Upload Knowledge Base Documents*\n\n"
        "You can teach your AI receptionist by uploading documents:\n"
        "• *Supported Formats:* PDF, DOCX, TXT, Markdown\n"
        "• *Examples:* Menus, price lists, clinic service guides, FAQ sheets\n\n"
        "➡️ *To upload:* Simply send the file as a document attachment right into this chat!",
        parse_mode="Markdown"
    )


@rate_limited(max_calls=5, window_seconds=60)
async def crawl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Crawl a business website and ingest its contents into the vector store."""
    chat_id = update.effective_chat.id
    target_url = context.args[0].strip() if context.args else ""

    if not target_url or not (target_url.startswith("http://") or target_url.startswith("https://")):
        await update.message.reply_text(
            "⚠️ Please provide a valid website URL starting with `http://` or `https://`.\n\n"
            "*Usage:* `/crawl https://mybusiness.com`\n"
            "_(Crawls up to 2 levels deep, max 50 pages, same domain only)_",
            parse_mode="Markdown"
        )
        return

    progress_msg = await update.message.reply_text(
        f"🌐 *Initiating scoped website crawl:* `{target_url}`\n"
        "Analyzing domain and parsing pages...",
        parse_mode="Markdown"
    )

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)

    try:
        session_maker = get_session_maker()
        async with session_maker() as session:
            result = await ingestion_service.ingest_url(
                session=session,
                business_id=biz.business_id,
                target_url=target_url
            )

        if not result.get("success"):
            await progress_msg.edit_text(
                f"❌ *Crawl failed:* {result.get('message', 'Unknown error')}",
                parse_mode="Markdown"
            )
            return

        pages_crawled = result.get("pages_crawled", 0)
        chunks_created = result.get("chunks_created", 0)
        total_stored = result.get("total_stored_chunks", 0)

        summary_card = (
            "🎉 *Website Crawl & Ingestion Complete!*\n\n"
            f"• *URL:* `{target_url}`\n"
            f"• *Pages Discovered & Parsed:* {pages_crawled}\n"
            f"• *Knowledge Chunks Created:* {chunks_created}\n"
            f"• *Total Stored Chunks:* {total_stored} / 10,000\n\n"
            "Your AI receptionist can now reference information from your website on incoming calls and `/test` queries!"
        )
        await progress_msg.edit_text(summary_card, parse_mode="Markdown")

    except EmbeddingQuotaExceededError as e:
        await progress_msg.edit_text(
            f"⚠️ *Quota Limit Reached:* {str(e)}\n\n"
            "Please remove older documents or upgrade storage allocation.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error during crawl for chat_id={chat_id}: {e}")
        await progress_msg.edit_text(
            f"❌ *Error while crawling:* `{str(e)}`",
            parse_mode="Markdown"
        )


@rate_limited(max_calls=5, window_seconds=60)
async def handle_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document attachments sent to the bot (PDF, DOCX, TXT)."""
    message = update.effective_message
    if not message or not message.document:
        return

    doc = message.document
    filename = doc.file_name or "document.txt"
    lower_name = filename.lower()

    if not (lower_name.endswith(".pdf") or lower_name.endswith(".docx") or lower_name.endswith(".txt") or lower_name.endswith(".md")):
        await message.reply_text(
            "⚠️ Unsupported file type. Please upload a `.pdf`, `.docx`, `.txt`, or `.md` file.",
            parse_mode="Markdown"
        )
        return

    progress_msg = await message.reply_text(
        f"📥 *Processing document:* `{filename}`\n"
        "Extracting text, running security sanitization, and generating vector embeddings...",
        parse_mode="Markdown"
    )

    chat_id = update.effective_chat.id
    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)

    try:
        # Download document file bytes from Telegram
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = await tg_file.download_as_bytearray()

        session_maker = get_session_maker()
        async with session_maker() as session:
            result = await ingestion_service.ingest_document(
                session=session,
                business_id=biz.business_id,
                filename=filename,
                content_bytes=bytes(file_bytes)
            )

        if not result.get("success"):
            await progress_msg.edit_text(
                f"❌ *Processing failed:* {result.get('message', 'No text could be extracted.')}",
                parse_mode="Markdown"
            )
            return

        chunks_created = result.get("chunks_created", 0)
        total_chunks = result.get("total_stored_chunks", 0)

        summary_card = (
            "✅ *Document Successfully Ingested!*\n\n"
            f"• *File:* `{filename}`\n"
            f"• *Knowledge Chunks Created:* {chunks_created}\n"
            f"• *Total Stored Chunks:* {total_chunks} / 10,000\n\n"
            "Your AI receptionist has updated its knowledge base with this document.\n"
            "Use `/test <question>` to test responses based on the uploaded material."
        )
        await progress_msg.edit_text(summary_card, parse_mode="Markdown")

    except EmbeddingQuotaExceededError as e:
        await progress_msg.edit_text(
            f"⚠️ *Quota Limit Exceeded:* {str(e)}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error ingesting document '{filename}' for chat_id={chat_id}: {e}")
        await progress_msg.edit_text(
            f"❌ *Failed to process file:* `{str(e)}`",
            parse_mode="Markdown"
        )
