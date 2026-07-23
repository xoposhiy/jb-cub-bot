import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

import sdt_bot.features as features_pkg
from sdt_bot.core.config import get_settings
from sdt_bot.core.db import get_session
from sdt_bot.core.intents import IntentRouter
from sdt_bot.core.loader import discover_features
from sdt_bot.core.middleware import PrincipalMiddleware

_intent_router = IntentRouter()


def build_dispatcher(session_factory, bootstrap_ids: set | None = None) -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))

    for feature in discover_features(features_pkg):
        dp.include_router(feature.router)
        for intent in feature.manifest.intents:
            _intent_router.register(intent)

    # NL fallback: any non-command text runs through the intent router.
    @dp.message(F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        await _intent_router.dispatch(message.text, message, principal, session)

    return dp


def run() -> None:
    settings = get_settings()
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(get_session, settings.bootstrap_admin_id_set)
    asyncio.run(dp.start_polling(bot))
