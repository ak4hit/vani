from telegram.ext import Application, CommandHandler, MessageHandler, filters

from src.config import settings
from src.utils.logger import get_logger

from src.bot.handlers.onboarding import get_onboarding_conversation_handler
from src.bot.handlers.settings import (
    setup_command,
    set_prompt_command,
    set_agent_command,
    set_hours_command,
    escalate_to_command,
)
from src.bot.handlers.faq import (
    add_faq_command,
    list_faqs_command,
    remove_faq_command,
)
from src.bot.handlers.control import (
    pause_command,
    resume_command,
    status_command,
)
from src.bot.handlers.test_cmd import handle_test_command
from src.bot.handlers.docs import (
    upload_doc_command,
    crawl_command,
    handle_document_upload,
)

logger = get_logger("vani.bot")


def build_application(token: Optional[str] = None) -> Application:
    """Build and configure the Telegram Application with all command and conversation handlers."""
    bot_token = token or settings.TELEGRAM_BOT_TOKEN or "MOCK_TOKEN"

    app = Application.builder().token(bot_token).build()

    # Register Onboarding wizard conversation handler
    app.add_handler(get_onboarding_conversation_handler())

    # Register Settings & Knowledge base commands
    app.add_handler(CommandHandler("setup", setup_command))
    app.add_handler(CommandHandler("setprompt", set_prompt_command))
    app.add_handler(CommandHandler("setagent", set_agent_command))
    app.add_handler(CommandHandler("sethours", set_hours_command))
    app.add_handler(CommandHandler("escalateto", escalate_to_command))

    # Register FAQ commands
    app.add_handler(CommandHandler("addfaq", add_faq_command))
    app.add_handler(CommandHandler("listfaqs", list_faqs_command))
    app.add_handler(CommandHandler("removefaq", remove_faq_command))

    # Register Document & Web Ingestion commands
    app.add_handler(CommandHandler("uploaddoc", upload_doc_command))
    app.add_handler(CommandHandler("crawl", crawl_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_upload))

    # Register Control commands
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("status", status_command))

    # Register Simulation command
    app.add_handler(CommandHandler("test", handle_test_command))

    logger.info("Vani Telegram Admin Bot handlers successfully registered.")
    return app


def main():
    """Entry point for standalone bot execution."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not configured in .env. "
            "Please create a bot via @BotFather and add your token to run the live bot."
        )
        sys.exit(1)

    logger.info("Starting Vani Telegram Admin Bot...")
    app = build_application(token)
    app.run_polling()


if __name__ == "__main__":
    main()
