import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
TOKEN=os.environ["BOT_TOKEN"]
ALLOWED={int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED:
        await update.message.reply_text(f"Access denied. Your chat ID: {chat_id}")
        return
    await update.message.reply_text("Hello! Server bot activated.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()

if __name__ == "__main__":
    main()
