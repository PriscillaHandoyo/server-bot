"""Background monitor.

Runs every check on a fixed interval and sends a Telegram message only when
something changes (OK -> failing, or failing -> OK).

To avoid spam when a service flaps (up, down, up, down...), a new state only
counts once it has been seen CONFIRM_AFTER times in a row.
"""

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.checks import (
    CheckResult,
    check_containers,
    check_disk,
    check_sites,
    check_tunnels,
)

log = logging.getLogger(__name__)


# Run checks
async def run_all_checks() -> dict[str, list[CheckResult]]:
    """Run every configured check concurrently, grouped by section.

    Shared by /status and the background monitor, so both always agree.
    """
    sites, containers, tunnels = await asyncio.gather(
        check_sites(config.SITES),
        check_containers(config.CONTAINERS),
        check_tunnels(config.TUNNEL_IDS),
    )
    disk = check_disk(config.DISK_PATH, config.DISK_LIMIT_PERCENT)
    return {
        "Website": list(sites),
        "Container": containers,
        "Tunnel": list(tunnels),
        "Server": [disk],
    }


def format_result(r: CheckResult) -> str:
    # Escape the text: error messages may contain < or >, which would
    # break Telegram's HTML formatting.
    return f"{'✅' if r.ok else '❌'} {html.escape(r.name)}: {html.escape(r.detail)}"


# State tracking
@dataclass
class CheckState:
    confirmed: bool          # the state we last told the user about
    pending: bool | None = None  # a different state we are currently seeing
    streak: int = 0          # how many times in a row we've seen `pending`


def update_states(
    states: dict[str, CheckState],
    results: list[CheckResult],
    confirm_after: int,
) -> list[CheckResult]:
    """Feed new results into the state table. Returns results whose state changed.

    Pure function (no Telegram, no I/O) so it is easy to test.
    """
    changed = []
    for r in results:
        state = states.get(r.name)

        if state is None:
            # First time we see this check: remember it, don't notify.
            states[r.name] = CheckState(confirmed=r.ok)
            continue

        if r.ok == state.confirmed:
            # Back to (or still in) the known state: cancel any pending change.
            state.pending, state.streak = None, 0
            continue

        # Seeing a state different from the confirmed one.
        if state.pending == r.ok:
            state.streak += 1
        else:
            state.pending, state.streak = r.ok, 1

        if state.streak >= confirm_after:
            state.confirmed = r.ok
            state.pending, state.streak = None, 0
            changed.append(r)

    return changed


# Telegram
async def notify(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send a message to every allowed chat. One failing chat won't stop the rest."""
    for chat_id in config.ALLOWED_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception:
            log.exception("Failed to send notification to chat %s", chat_id)


def build_change_message(changed: list[CheckResult]) -> str:
    now = datetime.now().strftime("%H:%M")
    down = [r for r in changed if not r.ok]
    up = [r for r in changed if r.ok]

    lines = [f"🖥 <b>{html.escape(config.SERVER_NAME)}</b> · {now}"]
    if down:
        lines.append("\n🔴 <b>Problem detected</b>")
        lines += [format_result(r) for r in down]
    if up:
        lines.append("\n🟢 <b>Recovered</b>")
        lines += [format_result(r) for r in up]
    return "\n".join(lines)


def build_startup_message(results: list[CheckResult]) -> str:
    failing = [r for r in results if not r.ok]
    header = f"🚀 <b>Monitor started on {html.escape(config.SERVER_NAME)}</b>"
    if not failing:
        return f"{header}\nWatching {len(results)} checks, all OK."
    lines = [header, f"Watching {len(results)} checks. Currently failing:"]
    lines += [format_result(r) for r in failing]
    return "\n".join(lines)


async def monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by the job queue every MONITOR_INTERVAL seconds."""
    sections = await run_all_checks()
    results = [r for group in sections.values() for r in group]

    states: dict[str, CheckState] = context.bot_data.setdefault("states", {})
    first_run = not states

    changed = update_states(states, results, config.CONFIRM_AFTER)

    if first_run:
        await notify(context, build_startup_message(results))
    elif changed:
        log.info("State changed: %s", ", ".join(f"{r.name}={r.ok}" for r in changed))
        await notify(context, build_change_message(changed))
