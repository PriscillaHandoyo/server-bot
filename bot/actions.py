"""Actions the bot can perform on the server: restart an app, restart the
Cloudflare tunnel, reboot.

Safety rules:
- Every action needs a confirmation tap on an inline button.
- Confirmation buttons expire after CONFIRM_TIMEOUT seconds, so an old
  button left in the chat can't trigger anything by accident.
- Only one action runs at a time.
- Which actions exist is decided by .env, so a server only offers what
  actually makes sense there (e.g. no ESPE restart on Cikarang).
- There is deliberately no shutdown: nobody could turn the server back on remotely.
"""

import asyncio
import html
import logging
import time
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot import config
from bot.auth import only_allowed
from bot.checks import run_command

log = logging.getLogger(__name__)

CONFIRM_TIMEOUT = 60  # seconds


# Registry
@dataclass(frozen=True)
class Action:
    key: str      # internal id, also used inside button callback data
    title: str    # shown to the user
    warning: str  # what will happen, shown before confirming


def get_actions() -> dict[str, Action]:
    """Build the list of actions available on THIS server, based on .env."""
    actions: dict[str, Action] = {}

    for app in config.APPS:
        key = f"restart-{app}"
        actions[key] = Action(
            key,
            f"Restart {app.upper()}",
            f"All <code>{html.escape(app)}-*</code> containers restart. "
            f"{html.escape(app.upper())} will be unavailable for about 10-30 seconds.",
        )

    if config.TUNNEL_SERVICE:
        actions["restart-tunnel"] = Action(
            "restart-tunnel",
            "Restart Cloudflare tunnel",
            "Every website served by this server goes offline for a few seconds.",
        )

    if config.ALLOW_REBOOT:
        actions["reboot"] = Action(
            "reboot",
            f"Reboot {html.escape(config.SERVER_NAME)}",
            "The whole server restarts. Everything is down for 1-3 minutes and the "
            "bot goes silent until the server is back.",
        )

    return actions


# -------------------------------------------------------------- executors ---

async def restart_app(app: str) -> tuple[bool, str]:
    """Restart every container whose name starts with '<app>-'."""
    code, out, err = await run_command(
        "docker", "ps", "-a", "--filter", f"name=^{app}-", "--format", "{{.Names}}"
    )
    if code != 0:
        return False, f"Could not list containers: {err or code}"

    names = out.split()
    if not names:
        return False, f"No containers found with names starting with '{app}-'"

    code, _, err = await run_command("docker", "restart", *names, timeout=120)
    if code != 0:
        return False, f"docker restart failed: {err or code}"
    return True, "Restarted: " + ", ".join(names)


async def restart_tunnel() -> tuple[bool, str]:
    # sudo -n = never ask for a password; fail immediately if not permitted.
    code, _, err = await run_command(
        "sudo", "-n", "systemctl", "restart", config.TUNNEL_SERVICE, timeout=60
    )
    if code != 0:
        return False, f"Tunnel restart failed: {err or code}"
    return True, f"{config.TUNNEL_SERVICE} restarted"


async def reboot() -> tuple[bool, str]:
    code, _, err = await run_command("sudo", "-n", "systemctl", "reboot")
    if code != 0:
        return False, f"Reboot failed: {err or code}"
    return True, "Reboot command sent"


async def execute(key: str) -> tuple[bool, str]:
    if key.startswith("restart-") and key != "restart-tunnel":
        return await restart_app(key.removeprefix("restart-"))
    if key == "restart-tunnel":
        return await restart_tunnel()
    if key == "reboot":
        return await reboot()
    return False, f"Unknown action: {key}"


# ---------------------------------------------------------------- confirm ---

def confirm_keyboard(key: str) -> InlineKeyboardMarkup:
    issued_at = int(time.time())
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, do it", callback_data=f"run|{key}|{issued_at}"),
        InlineKeyboardButton("✖️ Cancel", callback_data="cancel"),
    ]])


async def ask_confirmation(update: Update, action: Action) -> None:
    await update.message.reply_text(
        f"⚠️ <b>{action.title}?</b>\n{action.warning}\n\n"
        f"<i>This button expires in {CONFIRM_TIMEOUT} seconds.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard(action.key),
    )


async def ask_or_explain(update: Update, key: str) -> None:
    action = get_actions().get(key)
    if action is None:
        await update.message.reply_text("That action is not enabled on this server.")
        return
    await ask_confirmation(update, action)


# --------------------------------------------------------------- commands ---

@only_allowed
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/restart <app>, e.g. /restart sge"""
    if not config.APPS:
        await update.message.reply_text("No apps can be restarted on this server.")
        return

    available = ", ".join(config.APPS)
    if not context.args:
        await update.message.reply_text(f"Usage: /restart &lt;app&gt;\nAvailable: {available}",
                                        parse_mode=ParseMode.HTML)
        return

    app = context.args[0].lower()
    if app not in config.APPS:
        await update.message.reply_text(f"Unknown app '{app}'. Available: {available}")
        return

    await ask_or_explain(update, f"restart-{app}")


@only_allowed
async def cmd_restart_tunnel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_or_explain(update, "restart-tunnel")


@only_allowed
async def cmd_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_or_explain(update, "reboot")


@only_allowed
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # stops the loading spinner on the button

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return

    try:
        _, key, issued_at = query.data.split("|")
        issued_at = int(issued_at)
    except ValueError:
        await query.edit_message_text("Invalid button.")
        return

    if time.time() - issued_at > CONFIRM_TIMEOUT:
        await query.edit_message_text("⌛ This confirmation expired. Send the command again.")
        return

    action = get_actions().get(key)
    if action is None:
        await query.edit_message_text("That action is no longer available.")
        return

    lock: asyncio.Lock = context.bot_data.setdefault("action_lock", asyncio.Lock())
    if lock.locked():
        await query.edit_message_text("Another action is still running. Try again in a moment.")
        return

    async with lock:
        user = update.effective_user
        log.warning("Action '%s' confirmed by %s (%s)",
                    key, user.username if user else "?", user.id if user else "?")

        if key == "reboot":
            # Say it before rebooting: if the reboot works, we won't get another chance.
            await query.edit_message_text(
                "🔄 Rebooting... I'll send <b>Monitor started</b> when the server is back.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.edit_message_text(f"⏳ {action.title}...", parse_mode=ParseMode.HTML)

        ok, detail = await execute(key)

        icon = "✅" if ok else "❌"
        await query.edit_message_text(
            f"{icon} <b>{action.title}</b>\n{html.escape(detail)}",
            parse_mode=ParseMode.HTML,
        )


# --------------------------------------------------------------- register ---

def register(app: Application) -> None:
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("restart_tunnel", cmd_restart_tunnel))
    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(run\||cancel$)"))
