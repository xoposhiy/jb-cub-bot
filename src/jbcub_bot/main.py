import asyncio
import logging
import sys
import threading

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import StateFilter
from aiogram.types import ErrorEvent, Message, Update

import jbcub_bot.features as features_pkg
from jbcub_bot.core import registry
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.db import get_session, init_db
from jbcub_bot.core.errors import report_exception, summarize
from jbcub_bot.core.intents import IntentRouter
from jbcub_bot.core.loader import discover_features
from jbcub_bot.core.middleware import PrincipalMiddleware

_intent_router = IntentRouter()
_log = logging.getLogger(__name__)


def configure_logging() -> None:
    """Send logs to stdout so the host's console shows tracebacks.

    Without this, the root logger falls back to a bare handler that hides
    anything below WARNING — including aiogram's own diagnostics — which is how
    a crashed handler ends up looking like a silent hang.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )


def describe_update(update: Update) -> str:
    """Short "what was the bot doing" line for a crash report."""
    event = update.message or update.callback_query
    parts = [f"update {update.update_id}"]
    if update.message is not None and update.message.text:
        parts.append(repr(update.message.text[:80]))
    elif update.callback_query is not None:
        parts.append(f"callback {update.callback_query.data!r}")
    user = getattr(event, "from_user", None)
    if user is not None:
        parts.append(f"from @{user.username}" if user.username else f"from {user.id}")
    return " · ".join(parts)


# What the bot says when no intent took the message. Search is the only intent
# today, so this is its "not found"; when the chain grows it becomes the
# generic "I didn't understand that".
NOTHING_MATCHED = "No one found."


def build_dispatcher(session_factory, bootstrap_ids: set | None = None) -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))

    registry.reset()
    for feature in discover_features(features_pkg):
        dp.include_router(feature.router)
        registry.register(feature.manifest)
        for intent in feature.manifest.intents:
            _intent_router.register(intent)

    # NL fallback: any non-command text runs through the intent router --
    # unless the sender is in a state. A Dispatcher's own handlers run before
    # its sub-routers, so without StateFilter(None) this handler would consume
    # every value a feature is waiting for. The `.+` search intent no longer
    # swallows the message by matching it -- below its threshold it declines --
    # but it still runs before any sub-router, so StateFilter(None) stays
    # load-bearing.
    @dp.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        handled = await _intent_router.dispatch(message.text, message,
                                                principal, session)
        if not handled:
            await message.answer(NOTHING_MATCHED)

    # Last word: a message no handler took must still get an answer. Sub-routers
    # run after the Dispatcher's own handlers, so this router is included last
    # and only sees what everything above it declined — unknown commands, and
    # anything that isn't text.
    fallback = Router(name="fallback")

    @fallback.message()
    async def nothing_understood(message: Message):
        command = (message.text or "").split()[0] if message.text else ""
        if command.startswith("/"):
            await message.answer(
                f"I don't know {command}. /help lists what I can do."
            )
        else:
            await message.answer(
                "I only read text. /help lists what I can do."
            )

    dp.include_router(fallback)

    @dp.errors()
    async def on_unhandled_error(event: ErrorEvent, bot: Bot) -> bool:
        """Catch-all so a crashing handler answers instead of going quiet.

        Returning True marks the update handled, which keeps aiogram from
        logging the same traceback a second time without any of our context.
        """
        exc = event.exception
        await report_exception(bot, bootstrap_ids, exc,
                              context=describe_update(event.update))
        try:
            if event.update.message is not None:
                await event.update.message.answer(
                    f"⚠️ Something went wrong.\n{summarize(exc)}\n\n"
                    "The bot admins got the full traceback."
                )
            elif event.update.callback_query is not None:
                # An unanswered callback leaves the button spinning in the client.
                await event.update.callback_query.answer(
                    "Something went wrong. The bot admins were notified.",
                    show_alert=True,
                )
        except Exception:  # noqa: BLE001 - the report already went out
            _log.exception("Could not tell the user about the %s",
                           type(exc).__name__)
        return True

    return dp


def _watch_for_quit(loop: asyncio.AbstractEventLoop, dp: Dispatcher) -> None:
    """Read stdin in a daemon thread; on 'q' (or EOF) stop polling.

    Runs only when stdin is a real terminal, so a non-interactive deployment
    (no TTY) keeps relying on signals/Ctrl+C instead of quitting immediately.
    """
    if not (sys.stdin and sys.stdin.isatty()):
        return

    def reader() -> None:
        for line in sys.stdin:
            if line.strip().lower() == "q":
                break  # EOF also falls through here

        def request_stop() -> None:
            async def _stop() -> None:
                try:
                    await dp.stop_polling()
                except RuntimeError:
                    pass  # polling already stopped

            asyncio.ensure_future(_stop())

        loop.call_soon_threadsafe(request_stop)

    threading.Thread(target=reader, daemon=True, name="quit-watcher").start()


async def _serve(bot: Bot, dp: Dispatcher) -> None:
    _watch_for_quit(asyncio.get_running_loop(), dp)
    print("Bot is running. Press 'q' + Enter to stop (Ctrl+C also works).")
    await dp.start_polling(bot)


def run() -> None:
    configure_logging()
    settings = get_settings()
    init_db()  # run pending migrations, creating the schema on a fresh database
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(get_session, settings.bootstrap_admin_id_set)
    asyncio.run(_serve(bot, dp))
