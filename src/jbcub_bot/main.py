import asyncio
import sys
import threading

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

import jbcub_bot.features as features_pkg
from jbcub_bot.core import registry
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.db import get_session, init_db
from jbcub_bot.core.intents import IntentRouter
from jbcub_bot.core.loader import discover_features
from jbcub_bot.core.middleware import PrincipalMiddleware

_intent_router = IntentRouter()


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

    # NL fallback: any non-command text runs through the intent router.
    @dp.message(F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        await _intent_router.dispatch(message.text, message, principal, session)

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
    settings = get_settings()
    init_db()  # create the DB schema from scratch if it doesn't exist yet
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(get_session, settings.bootstrap_admin_id_set)
    asyncio.run(_serve(bot, dp))
