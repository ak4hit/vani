"""FAQ Handlers - Add, list, and remove explicit question-and-answer pairs."""

from telegram import Update
from telegram.ext import ContextTypes

from src.services.business_store import business_store
from src.bot.rate_limiter import rate_limited
from src.utils.logger import get_logger

logger = get_logger("vani.bot.faq")


@rate_limited()
async def add_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a question and answer pair."""
    chat_id = update.effective_chat.id
    raw_text = " ".join(context.args).strip() if context.args else ""

    if not raw_text or "|" not in raw_text:
        await update.message.reply_text(
            "⚠️ Please provide both a question and an answer separated by `|`.\n\n"
            "*Usage:* `/addfaq Do you accept walk-ins? | Yes, walk-ins are welcome from 9 AM to 3 PM.`",
            parse_mode="Markdown"
        )
        return

    question, answer = raw_text.split("|", 1)
    question = question.strip()
    answer = answer.strip()

    if not question or not answer:
        await update.message.reply_text("⚠️ Neither question nor answer can be empty.")
        return

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    item = business_store.add_faq(biz.business_id, question=question, answer=answer)

    await update.message.reply_text(
        f"✅ *FAQ #{item.id} Added!*\n\n"
        f"• *Q:* {item.question}\n"
        f"• *A:* {item.answer}\n\n"
        "Your voice bot will now reference this answer when asked this or similar questions.",
        parse_mode="Markdown"
    )


@rate_limited()
async def list_faqs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all registered FAQs."""
    chat_id = update.effective_chat.id
    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    faqs = business_store.list_faqs(biz.business_id)

    if not faqs:
        await update.message.reply_text(
            "ℹ️ No FAQs added yet.\n\n"
            "Use `/addfaq <question> | <answer>` to add your first FAQ!",
            parse_mode="Markdown"
        )
        return

    lines = ["📚 *Current FAQs:*"]
    for item in faqs:
        lines.append(f"\n*#{item.id}* Q: {item.question}\nA: {item.answer}")

    lines.append("\n_Use `/removefaq <id>` to delete a FAQ._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@rate_limited()
async def remove_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a FAQ by ID."""
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify the FAQ ID to remove.\n\n"
            "*Usage:* `/removefaq 1`\n"
            "_(Type `/listfaqs` to see your FAQ IDs)_",
            parse_mode="Markdown"
        )
        return

    try:
        faq_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ FAQ ID must be a number.")
        return

    biz = business_store.get_or_create_business(f"biz_{chat_id}", chat_id=chat_id)
    removed = business_store.remove_faq(biz.business_id, faq_id)

    if removed:
        await update.message.reply_text(f"✅ FAQ #{faq_id} removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ FAQ #{faq_id} not found.", parse_mode="Markdown")
