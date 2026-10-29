import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot import config
from bot.auth import only_allowed
from bot.checks import (
    CheckResult,
    check_containers,
    check_disk,
    check_sites,
    check_tunnels,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx logs every request (including Telegram polling) - it's too noisy
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

def format_section(title: str, results: list[CheckResult]) -> list[str]:
    if not results:
        return []
    lines = [f"\n<b>{title}</b>"]
    lines += [f"{'✅' if r.ok else '❌'} {r.name}: {r.detail}" for r in results]
    return lines

@only_allowed
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Server bot activated.")

@only_allowed
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Checking...")

    # All checks are carried out simultaneously
    sites, containers, tunnels = await asyncio.gather(
        check_sites(config.SITES),
        check_containers(config.CONTAINERS),
        check_tunnels(config.TUNNEL_IDS),
    )
    disk = check_disk(config.DISK_PATH, config.DISK_LIMIT_PERCENT)

    lines = [f"🖥 <b>Status {config.SERVER_NAME}</b>"]
    lines += format_section("Website", sites)
    lines += format_section("Container", containers)
    lines += format_section("Tunnel", tunnels)
    lines += format_section("Server", [disk])

    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    # All uncaught errors end up here, consolidated into a single, neat log line
    err = context.error
    if isinstance(err, Conflict):
        log.error("This token is already being used by another program. Shut down the other bot first")
    elif isinstance(err, NetworkError):
        log.warning("Network disruption to Telegram: %s (automatic retry)", err)
    else:
        log.error("Unexpected error", exc_info=err)

def main():
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_error_handler(on_error)
    log.info("Bot %s is running", config.SERVER_NAME)
    app.run_polling()

if __name__ == "__main__":
    main()
