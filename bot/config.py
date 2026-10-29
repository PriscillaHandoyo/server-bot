# All bot settings are read from the .env file here, so that other files can simply import them

import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str) -> list[str]:
    # Read setting
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]

BOT_TOKEN = os.environ["BOT_TOKEN"]

# This server name appears in the title of Telegram messages (e.g., Cikarang or Home)
SERVER_NAME = os.getenv("SERVER_NAME", "Server")

# Chat IDs permitted to use the bot. You may enter more than one; separate them with commas
ALLOWED_CHAT_IDS = {int(x) for x in _list("ALLOWED_CHAT_IDS")}

# Monitored website. The name on the left appears in Telegram messages
SITES = {
    "SGE": "https://sge.phsecretlabs.com",
    "ESPE": "https://espe.phsecretlabs.com",
}

# Docker containers that must be running on this server. Leave empty to skip checks
CONTAINERS = _list("CONTAINERS")

# Folder for which disk capacity is checked, along with the percentage threshold considered dangerous
DISK_PATH = os.getenv("DISK_PATH", "/")
DISK_LIMIT_PERCENT = float(os.getenv("DISK_LIMIT_PERCENT", "85"))

# Cloudflare Tunnel for which the number of connectors is checked (must be exactly 1)
# Requires a `cloudflare` login (`~/.cloudflared/cert.pem`) on this server. Leave empty to skip the check
TUNNEL_IDS = _list("TUNNEL_IDS")
