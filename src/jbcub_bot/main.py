import asyncio
import logging
import sys
import threading

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, ErrorEvent, Message, Update

import jbcub_bot.features as features_pkg
from jbcub_bot.core import buttons, impersonation, pipeline
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.contract import Registry
from jbcub_bot.core.db import get_session, init_db
from jbcub_bot.core import oplog as oplog_mod
from jbcub_bot.core.dialogs import DialogMiddleware
from jbcub_bot.core.errors import report_exception
from jbcub_bot.core.loader import load_features
from jbcub_bot.core.principal import AccessMiddleware, PrincipalMiddleware

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
    # Authentication is four stages, and the order is the design. No stage
    # knows where it sits; this is the only place that does, so it is the only
    # place that can be wrong about it.
    #
    # 1. `PrincipalMiddleware` -- where we are, who is writing, and a session
    #    for the rest of the update. It refuses a non-private chat (the bot
    #    answers where it was addressed, and would otherwise post one person's
    #    profile into a group), and does so before the identity lookup, so a
    #    busy group costs no query per line. It refuses nothing else.
    # 2. `ImpersonationMiddleware` -- whose eyes the rest of the update looks
    #    through: it may replace `principal` with an `/as` target.
    # 3. `BannerMiddleware` -- says whose eyes those are.
    # 4. `AccessMiddleware` -- the one `departed_at` refusal, asked of whatever
    #    principal stage 2 settled on.
    #
    # Refusing *after* the swap is what makes `/as <departed student>` show the
    # admin that student's own refusal, from that student's own line of code,
    # rather than an impersonation-flavoured copy free to drift away from it.
    # Announcing before it is what leaves them a way out: the notice carries no
    # hint of its own, so the banner directly above it is what says /unas still
    # works. Move stage 4 up and the special case comes back; move stage 3 down
    # and the admin reads "the bot is closed to you" with nothing to explain it.
    #
    # Inner middlewares, all of them: aiogram resolves the parent chain's for a
    # sub-router's handler, and runs them once, for the handler that matched.
    dp.message.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.message.middleware(impersonation.ImpersonationMiddleware(bootstrap_ids))
    dp.callback_query.middleware(
        impersonation.ImpersonationMiddleware(bootstrap_ids))
    # Messages only, so the callback chain simply goes without a stage 3.
    dp.message.middleware(impersonation.BannerMiddleware())
    dp.message.middleware(AccessMiddleware(bootstrap_ids))
    dp.callback_query.middleware(AccessMiddleware(bootstrap_ids))
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
    # inherit neither the chain nor the per-chat taker record. The pair this
    # replaces -- a module-global `_intent_router` beside a `core/registry.py`
    # that was reset -- appended the whole chain again on every call, because
    # only one half of it was ever cleared.
    registry = Registry()
    load_features(features_pkg, registry)
    # Where a test -- or anything else wanting to see the resolved contract --
    # finds it, since there is deliberately no module global holding it.
    dp["registry"] = registry

    # Two entry points, no filters and no sub-routers: every update is the
    # core's, and what to do with one is `pipeline`'s decision rather than
    # aiogram's. The filters that used to sit here asked the registry whether a
    # legacy router still owned an update; nothing is legacy any more, and the
    # fallback router they fed -- which existed because a Dispatcher runs its
    # own handlers before its sub-routers -- went with them. `handle_message`
    # gives the last word itself, to every message, which is the one copy of it
    # left.
    @dp.message()
    async def route_message(message: Message, principal, session, bot: Bot,
                            dialog, impersonator=None):
        await pipeline.handle_message(
            registry, message, principal=principal, session=session, bot=bot,
            impersonator=impersonator, dialog=dialog, oplog=ops_log(bot),
        )

    @dp.callback_query()
    async def route_callback(callback: CallbackQuery, principal, session,
                             bot: Bot, dialog, impersonator=None):
        await buttons.handle_callback(
            registry, callback, principal=principal, session=session, bot=bot,
            impersonator=impersonator, dialog=dialog, oplog=ops_log(bot),
        )

    @dp.errors()
    async def on_unhandled_error(event: ErrorEvent, bot: Bot) -> bool:
        """Catch-all so a crashing handler answers instead of going quiet.

        Returning True marks the update handled, which keeps aiogram from
        logging the same traceback a second time without any of our context.

        What the sender gets is the apology and nothing else. The exception
        chain and the traceback have already gone to the log chat by then, and
        the person who typed the command can do nothing with a
        `ConnectionResetError` except be alarmed by it -- worse, the phase
        labels /sync raises name internal spreadsheet tabs. Whoever needs the
        detail is reading the other chat; do not put it back here.
        """
        exc = event.exception
        await report_exception(ops_log(bot), exc,
                              context=describe_update(event.update))
        try:
            if event.update.message is not None:
                await event.update.message.answer(
                    "⚠️ Something went wrong.\n"
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
