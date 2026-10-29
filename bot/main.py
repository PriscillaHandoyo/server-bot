import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot import config
from bot.auth import only_allowed
from bot.monitor import format_result, monitor_job, run_all_checks

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx logs every request (including Telegram polling), far too noisy
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")


@only_allowed
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Hi! {config.SERVER_NAME} bot is running.")


@only_allowed
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Checking...")

    sections = await run_all_checks()

    lines = [f"🖥 <b>{html.escape(config.SERVER_NAME)} status</b>"]
    for title, results in sections.items():
        if results:
            lines.append(f"\n<b>{title}</b>")
            lines += [format_result(r) for r in results]

    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Every uncaught error ends up here as a single tidy log line."""
    err = context.error
    if isinstance(err, Conflict):
        log.error("This bot token is used by another running program. Stop the other one first.")
    elif isinstance(err, NetworkError):
        log.warning("Network problem talking to Telegram: %s (will retry)", err)
    else:
        log.error("Unexpected error", exc_info=err)


def main():
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_error_handler(on_error)

    # First run 10s after startup (gives the network time to come up),
    # then every MONITOR_INTERVAL seconds.
    app.job_queue.run_repeating(
        monitor_job, interval=config.MONITOR_INTERVAL, first=10, name="monitor"
    )

    log.info(
        "Bot for %s running (monitor every %ss, confirm after %s checks)",
        config.SERVER_NAME, config.MONITOR_INTERVAL, config.CONFIRM_AFTER,
    )
    app.run_polling()


if __name__ == "__main__":
    main()
