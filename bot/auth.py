# Restriction: only chat ID that is registered in ALLOWED_CHAT_IDS that can use the bot.

import functools
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import ALLOWED_CHAT_IDS

log = logging.getLogger(__name__)

def only_allowed(handler):
    """ A decorator for command handlers.
        Use it above each command function:
            @only_allowed
            async def status(update, context):...

        If the sender is not from a registered chat ID, the handler will not be executed at all,
        and the bot will remain silent (so as not to let unauthorized users know it is active).
        Any attempted access is recorded in the logs.
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
