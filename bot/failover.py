"""Automatic failover.

Each server's bot watches the app that is normally served by the OTHER server:
- Home bot watches SGE (normally served from Cikarang)
- Cikarang bot watches ESPE (normally served from home)

The failover script is run automatically only when ALL of these hold,
continuously for FAILOVER_AFTER_MIN minutes:
1. The app's website fails through Cloudflare (what users actually see)
2. The other server does not answer over Tailscale (port 22)
   -> if the server is alive but the app is broken, moving won't help
   -> this is what would have prevented the false failover of 9 November
3. This server itself has internet
   -> if WE are offline, everything looks dead from here, but it isn't

Moving back (failback) is always manual: data written here after the
failover must be copied back first.
"""

import asyncio
import html
import logging
import os
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from bot import config
from bot.auth import only_allowed
from bot.checks import CheckResult, run_command

log = logging.getLogger(__name__)

RETRY_AFTER_FAILURE = 30 * 60   # seconds before retrying a failover that failed


# ----------------------------------------------------------------- probes ---

async def tcp_ok(host: str, port: int, timeout: float = 5.0) -> bool:
    """Can we open a TCP connection? (no data sent)"""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def internet_ok() -> bool:
    """Is THIS server online? Two independent public hosts, either is enough."""
    results = await asyncio.gather(tcp_ok("1.1.1.1", 443), tcp_ok("8.8.8.8", 443))
    return any(results)


# ------------------------------------------------------------------ state ---

def configured() -> bool:
    return bool(config.FAILOVER_SCRIPT and config.FAILOVER_SITE and config.FAILOVER_PEER)


def auto_enabled(bot_data: dict) -> bool:
    # /autofailover on|off overrides .env until the bot restarts
    return bot_data.get("auto_failover", config.AUTO_FAILOVER)


def already_primary() -> bool:
    """The failover script leaves this flag file behind once it has run."""
    return bool(config.FAILOVER_FLAG) and os.path.exists(config.FAILOVER_FLAG)


def _reset(bot_data: dict) -> None:
    bot_data.pop("fo_down_since", None)
    bot_data.pop("fo_warned", None)


# --------------------------------------------------------------- evaluate ---

async def evaluate(context: ContextTypes.DEFAULT_TYPE, results: list[CheckResult], notify) -> None:
    """Called by the monitor after every round of checks."""
    if not configured():
        return
    bd = context.bot_data

    if already_primary():
        _reset(bd)
        return

    site = next((r for r in results if r.name == config.FAILOVER_SITE), None)
    if site is None or site.ok:
        _reset(bd)
        return

    if await tcp_ok(config.FAILOVER_PEER, 22):
        # Other server is alive: the app is broken there, moving won't fix it.
        _reset(bd)
        return

    if not await internet_ok():
        # We're the ones offline. Don't touch anything.
        _reset(bd)
        return

    now = time.monotonic()
    if now < bd.get("fo_retry_after", 0):
        return

    since = bd.setdefault("fo_down_since", now)
    minutes = (now - since) / 60
    if minutes < config.FAILOVER_AFTER_MIN:
        log.warning("%s down and peer unreachable for %.1f min (failover at %s min)",
                    config.FAILOVER_SITE, minutes, config.FAILOVER_AFTER_MIN)
        return

    app_name = html.escape(config.FAILOVER_SITE)
    if not auto_enabled(bd):
        if not bd.get("fo_warned"):
            bd["fo_warned"] = True
            await notify(context,
                f"⚠️ <b>{app_name} down for {minutes:.0f} min and the other server is unreachable.</b>\n"
                f"Automatic failover is <b>OFF</b>, so nothing was moved.\n"
                f"Run the failover script manually or send /autofailover on.")
        return

    await run_failover(context, notify, f"down for {minutes:.0f} min, other server unreachable")


async def run_failover(context: ContextTypes.DEFAULT_TYPE, notify, reason: str) -> None:
    bd = context.bot_data
    lock: asyncio.Lock = bd.setdefault("fo_lock", asyncio.Lock())
    if lock.locked():
        return

    async with lock:
        app_name = html.escape(config.FAILOVER_SITE)
        server = html.escape(config.SERVER_NAME)
        log.warning("Starting automatic failover of %s: %s", config.FAILOVER_SITE, reason)
        await notify(context,
            f"🚨 <b>Automatic failover: moving {app_name} to {server}</b>\nReason: {html.escape(reason)}")

        code, out, err = await run_command("bash", config.FAILOVER_SCRIPT, timeout=900)
        _reset(bd)

        tail = "\n".join((out + "\n" + err).strip().splitlines()[-8:])
        if code == 0:
            await notify(context,
                f"✅ <b>{app_name} is now served from {server}.</b>\n"
                f"<pre>{html.escape(tail)}</pre>\n"
                f"Moving back is manual: copy the data back first.")
        else:
            bd["fo_retry_after"] = time.monotonic() + RETRY_AFTER_FAILURE
            await notify(context,
                f"❌ <b>Automatic failover of {app_name} FAILED</b> (exit {code}). "
                f"Retrying in {RETRY_AFTER_FAILURE // 60} min. Check it manually!\n"
                f"<pre>{html.escape(tail)}</pre>")


# ---------------------------------------------------------------- command ---

@only_allowed
async def cmd_autofailover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/autofailover [on|off] - show or change automatic failover (until bot restart)."""
    if not configured():
        await update.message.reply_text("Automatic failover is not configured on this server.")
        return

    bd = context.bot_data
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("on", "off"):
        bd["auto_failover"] = arg == "on"
        _reset(bd)
        log.warning("Auto failover turned %s by %s", arg, update.effective_user.username if update.effective_user else "?")

    state = "ON ✅" if auto_enabled(bd) else "OFF ⏸"
    lines = [
        f"<b>Automatic failover: {state}</b>",
        f"Watching: {html.escape(config.FAILOVER_SITE)}",
        f"Other server: {html.escape(config.FAILOVER_PEER)}",
        f"Moves after: {config.FAILOVER_AFTER_MIN} min down",
    ]
    if already_primary():
        lines.append(f"\n{html.escape(config.FAILOVER_SITE)} is <b>already served from here</b> (flag file present).")
    if arg in ("on", "off"):
        lines.append("\n<i>Resets to the .env setting when the bot restarts.</i>")
    else:
        lines.append("\nUse /autofailover on or /autofailover off")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("autofailover", cmd_autofailover))
