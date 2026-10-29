from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.auth import only_allowed
from bot.checks import check_sites
from bot.config import BOT_TOKEN, SITES


@only_allowed
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Server bot activated.")

@only_allowed
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = await check_sites(SITES)
    lines = [f"{'✅' if r.ok else '❌'} {r.name}: {r.detail}" for r in results]
    await update.message.reply_text("\n".join(lines))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.run_polling()

if __name__ == "__main__":
    main()
