# All bot settings are read from the .env file here, so that other files can simply import them

import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Chat IDs permitted to use the bot. You may enter more than one; separate them with commas
ALLOWED_CHAT_IDS = {
    int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
}

# Monitored website. The name on the left appears in Telegram messages
SITES = {
    "SGE": "https://sge.phsecretlabs.com",
    "ESPE": "https://espe.phsecretlabs.com",
}
