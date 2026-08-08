import asyncio
import logging
import sys
import threading

from aiogram import Bot, Dispatcher, Router
from aiogram.types import CallbackQuery, ErrorEvent, Message, Update

import jbcub_bot.features as features_pkg
from jbcub_bot.core import buttons, impersonation, legacy, pipeline
# The pre-contract list of manifests. Nothing reads it any more -- task 7 moved
# /help onto `core/help.py` and the contract registry -- and task 11 deletes the
# module along with these two calls; aliased until then so `registry` here means
# the contract's own.
from jbcub_bot.core import registry as manifests
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.contract import Registry
from jbcub_bot.core.db import get_session, init_db
from jbcub_bot.core import oplog as oplog_mod
from jbcub_bot.core.dialogs import DialogMiddleware
from jbcub_bot.core.errors import report_exception, summarize
from jbcub_bot.core.loader import load_features
from jbcub_bot.core.middleware import PrincipalMiddleware

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


def build_dispatcher(session_factory, bootstrap_ids: set | None = None,
                     log_chat_id: str = "") -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    # After PrincipalMiddleware, which is what puts the impersonator in `data`.
    # Inner middleware, like the one above: aiogram resolves the parent chain's
    # inner middlewares for a sub-router's handler, and runs them once, for the
    # handler that actually matched.
    dp.message.middleware(impersonation.BannerMiddleware())
    # `dialog` for both kinds, because a tap opens the prompt the next message
    # answers and the two must see the same FSM context. aiogram resolves that
    # context into `data["state"]` in an outer middleware at update level, so it
    # is there whichever inner middleware reads it -- and reading it after
    # PrincipalMiddleware means the one event that resolves no context at all (a
    # message with no sender, which only a group can produce) has already been
    # refused for being outside a private chat.
    dp.message.middleware(DialogMiddleware())
    dp.callback_query.middleware(DialogMiddleware())

    def ops_log(bot):
        """One per update: the Bot instance only exists per-update."""
        return oplog_mod.OpsLog(bot, log_chat_id, bootstrap_ids or ())

    # Local, and nothing here is a module global: a second build_dispatcher must
    # inherit neither the chain nor the per-chat taker record. The
    # `_intent_router` this replaces was reset nowhere while the manifest list
    # beside it was, so every call appended the whole chain again.
    registry = Registry()
    # Phase B/E only: the legacy features' intents live in the chain at LEGACY,
    # after every landmark. Claimed before loading so `load_features` validates
    # and logs the slot beside every real feature's, and filled from the
    # manifests once they exist. Phase F deletes core/legacy.py and these lines.
    shim = legacy.install(registry)
    manifests.reset()
    loaded = load_features(features_pkg, registry)
    shim.adopt(loaded)
    for feature in loaded:
        if feature.legacy:
            dp.include_router(feature.router)
            manifests.register(feature.manifest)
    # Where a test -- or anything else wanting to see the resolved contract --
    # finds it, since there is deliberately no module global holding it.
    dp["registry"] = registry

    # The core routes every message it can prove is its own, and declines the
    # rest to the legacy routers below. The filter is what decides; see
    # core/legacy.py for why that decision cannot live inside the handler.
    @dp.message(legacy.core_owns_message(registry, loaded))
    async def route_message(message: Message, principal, session, bot: Bot,
                            dialog, impersonator=None):
        await pipeline.handle_message(
            registry, message, principal=principal, session=session, bot=bot,
            impersonator=impersonator, dialog=dialog, oplog=ops_log(bot),
        )

    @dp.callback_query(legacy.core_owns_callback(registry, loaded))
    async def route_callback(callback: CallbackQuery, principal, session,
                             bot: Bot, dialog, impersonator=None):
        await buttons.handle_callback(
            registry, callback, principal=principal, session=session, bot=bot,
            impersonator=impersonator, dialog=dialog, oplog=ops_log(bot),
        )

    # Last word: a message no handler took must still get an answer. Sub-routers
    # run after the Dispatcher's own handlers, so this router is included last
    # and only sees what everything above it declined — unknown commands, and
    # anything that isn't text. The wording and the ops-log miss are
    # `pipeline.last_word`'s now, which is also what answers a message the entry
    # point above took nothing from; phase F leaves only that one.
    fallback = Router(name="fallback")

    @fallback.message()
    async def nothing_understood(message: Message, bot: Bot, principal=None,
                                 impersonator=None):
        await pipeline.last_word(message, principal=principal,
                                 impersonator=impersonator,
                                 oplog=ops_log(bot))

    dp.include_router(fallback)

    @dp.errors()
    async def on_unhandled_error(event: ErrorEvent, bot: Bot) -> bool:
        """Catch-all so a crashing handler answers instead of going quiet.

        Returning True marks the update handled, which keeps aiogram from
        logging the same traceback a second time without any of our context.
        """
        exc = event.exception
        await report_exception(ops_log(bot), exc,
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
    dp = build_dispatcher(get_session, settings.bootstrap_admin_id_set,
                          settings.log_chat_id)
    asyncio.run(_serve(bot, dp))
