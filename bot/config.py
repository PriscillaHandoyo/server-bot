"""All bot settings, loaded from the .env file.

The same code runs on both servers (home and Cikarang).
Only the .env contents differ.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str) -> list[str]:
    """Read a comma-separated setting, e.g. "a, b,c" -> ["a", "b", "c"]."""
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


BOT_TOKEN = os.environ["BOT_TOKEN"]

# Name of this server, shown in Telegram messages (e.g. "Cikarang" or "Home")
SERVER_NAME = os.getenv("SERVER_NAME", "Server")

# Chat IDs allowed to use the bot. Comma-separated for more than one.
ALLOWED_CHAT_IDS = {int(x) for x in _list("ALLOWED_CHAT_IDS")}

# Websites to monitor. The name on the left is shown in Telegram.
SITES = {
    "SGE": "https://sge.phsecretlabs.com",
    "ESPE": "https://espe.phsecretlabs.com",
}

# Docker containers that must be running on this server. Empty = skip.
CONTAINERS = _list("CONTAINERS")

# Disk to check, and the usage percentage considered dangerous
DISK_PATH = os.getenv("DISK_PATH", "/")
DISK_LIMIT_PERCENT = float(os.getenv("DISK_LIMIT_PERCENT", "85"))

# Cloudflare tunnels whose connector count is checked (must be exactly 1).
# Needs a cloudflared login (~/.cloudflared/cert.pem) on this server. Empty = skip.
TUNNEL_IDS = _list("TUNNEL_IDS")

# Background monitor: how often to check (seconds), and how many checks
# in a row a new state must be seen before we send a notification.
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))
CONFIRM_AFTER = int(os.getenv("CONFIRM_AFTER", "2"))

# Actions. Leave empty / false to disable an action on this server.
# APPS: apps that can be restarted with /restart <app>.
#       Restarts every container whose name starts with "<app>-".
APPS = [a.lower() for a in _list("APPS")]
# Name of the systemd service running the Cloudflare tunnel on this server
TUNNEL_SERVICE = os.getenv("TUNNEL_SERVICE", "").strip()
# Allow /reboot. Keep false on your laptop!
ALLOW_REBOOT = os.getenv("ALLOW_REBOOT", "false").strip().lower() in ("1", "true", "yes")
