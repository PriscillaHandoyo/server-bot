"""Access guard: only chat IDs listed in ALLOWED_CHAT_IDS may use the bot."""

import functools
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import ALLOWED_CHAT_IDS

log = logging.getLogger(__name__)


def only_allowed(handler):
    """Decorator for command handlers.

    Put it above every command function:

        @only_allowed
        async def status(update, context): ...

    If the sender is not an allowed chat ID, the handler never runs and the
    bot stays silent (so strangers can't tell the bot is alive).
    The attempt is written to the log.
    """

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        if chat is None or chat.id not in ALLOWED_CHAT_IDS:
            user = update.effective_user
            log.warning(
                "Access denied: chat_id=%s username=%s",
                chat.id if chat else None,
                user.username if user else None,
            )
            return None
        return await handler(update, context, *args, **kwargs)

    return wrapper
