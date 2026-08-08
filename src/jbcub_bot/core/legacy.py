"""The features that still route themselves, hosted inside the new pipeline.

**Phase F deletes this file**, and with it the last of `core/intents.py`. Until
then two shapes coexist in `features/`: `kb` keeps its `Router` and its `Intent`
until the 2026-08-07 spec rewrites it, and `help`, `impersonate` and `directory`
keep theirs until tasks 7, 8 and 10 migrate them one at a time. Two things have
to stay true while both shapes are loaded.

**A legacy `Intent` must still be offered plain text.** `LegacyIntents` is one
chain handler at `pipeline.LEGACY`, after every landmark, so a migrated feature
always gets first refusal -- and inside it the intents keep their registration
order, which is what keeps `directory`'s name search ahead of `kb`'s offer
exactly as `main.py` did.

**The core's entry points must decline whatever a legacy router still owns.** A
`Dispatcher` runs its own handlers before its sub-routers, so an entry point
that answered everything would swallow every legacy command -- which is the
problem `main.py` used to solve with `StateFilter(None), F.text &
~F.text.startswith("/")`. `core_owns_message` and `core_owns_callback` replace
that filter with one asked of the registry, so a feature migrating changes what
the core takes without anything in `main.py` changing.

They are aiogram *filters* rather than a check inside the handler, and that is
the load-bearing decision here: a filter runs before the inner middlewares, so
declining costs the update nothing -- no second session, and no second banner
from `impersonation.BannerMiddleware`, which answers before the handler it
wraps. Declining from inside the handler (`raise SkipHandler`, which does reach
the sub-routers) would run that whole stack twice. The filter is also what makes
"one answer, never two" structural: the core's entry point and a legacy router
cannot both run, because the choice is made before either of them has anything
to answer with.
"""
import logging
import re
from typing import Callable

from aiogram.types import CallbackQuery, Message

from jbcub_bot.core.buttons import match
from jbcub_bot.core.contract import CANCEL_COMMAND, TEXT, Registry
from jbcub_bot.core.intents import Intent, intent_allowed
from jbcub_bot.core.loader import LoadedFeature
from jbcub_bot.core.pipeline import LEGACY

logger = logging.getLogger(__name__)

# The name the shim registers under. Not a package in `features/`, and it
# declares no command, no button and no described message, so `core/help.py`
# renders no heading for it -- there is nothing a reader of /help could do with
# the knowledge that some features are older than others.
FEATURE = "legacy"


class LegacyIntents:
    """The legacy features' `Intent` objects, in one chain slot.

    The walk is `core/intents.py`'s `IntentRouter.dispatch` -- a matching
    `pattern`, a `min_role` the caller meets, and `False` to decline -- kept here
    rather than delegated so that phase E can reduce that module to the `Intent`
    dataclass and `intent_allowed` this reads.
    """

    def __init__(self):
        self._intents: list[Intent] = []

    def adopt(self, loaded: list[LoadedFeature]) -> None:
        """Host every legacy feature's intents, in discovery order.

        Called after `load_features`, since that is when the manifests exist.
        Discovery order is the order `main.py` registered them in, and for
        intents registration order is precedence.
        """
        self._intents = [intent for feature in loaded if feature.legacy
                         for intent in feature.manifest.intents]
        # Named rather than counted: the whole reason the chain is logged at
        # startup is that a reader should not have to open five files to see the
        # order, and inside this slot the order is still registration order.
        logger.info("legacy shim at=%s hosts %s intents: %s", LEGACY,
                    len(self._intents),
                    ", ".join(intent.name for intent in self._intents))

    async def offer(self, message: Message, principal, session) -> bool:
        """Offer the text to each matching intent until one takes it.

        `False` back means every intent declined and none of them answered, so
        the core's own last word still gets the final say -- the same contract
        the intents themselves are written to.
        """
        # `when=TEXT` on the registration, so there is always text here.
        text = message.text
        for intent in self._intents:
            if not re.search(intent.pattern, text, re.IGNORECASE):
                continue
            if not intent_allowed(principal, intent):
                continue
            if await intent.handler(message, principal, session) is not False:
                return True
        return False


def install(registry: Registry) -> LegacyIntents:
    """Claim the chain slot at `LEGACY` for the legacy features' intents.

    Called *before* `load_features`, so the slot is validated and logged beside
    every real feature's -- and so a feature declaring `at=LEGACY` itself
    collides loudly at boot instead of quietly sharing the position with the
    shim.
    """
    shim = LegacyIntents()
    bot = registry.api_for(FEATURE)
    bot.describe("🧩", "Legacy", "Features that still route themselves.")
    # public=True because `main.py`'s `nl_fallback` ran the intent router for an
    # unlinked caller too, and each intent checks its own `min_role` inside the
    # walk. A guard here would filter the whole slot instead.
    bot.message(at=LEGACY, when=TEXT, public=True)(shim.offer)
    return shim


def core_owns_message(registry: Registry,
                      loaded: list[LoadedFeature]) -> Callable:
    """The filter for the core's message entry point: True when the registry (or
    the core itself) has something for this message, False when a legacy router
    still owns it."""
    # The one command the core would otherwise take out from under a legacy
    # router: `validate()` forbids a feature registering `cancel`, so it can
    # never appear in `registry.commands()`, and the core answers it itself.
    # `directory` still owns one until task 10, and only its answer can redraw
    # the edit screen the sender is looking at.
    claimed_cancel = any(spec.name == CANCEL_COMMAND
                         for feature in loaded if feature.legacy
                         for spec in feature.manifest.commands)

    async def owned(message: Message, raw_state: str | None = None) -> bool:
        # The same reading as `pipeline.take_message`: a command in a caption is
        # just as deliberate an address as a command typed on its own.
        text = message.text or message.caption or ""
        if text.startswith("/"):
            name = text.split(maxsplit=1)[0][1:].split("@")[0]
            if name == CANCEL_COMMAND:
                return not claimed_cancel
            # A command the registry does not know is declined rather than
            # taken, because a legacy router may own one its manifest never
            # listed -- `/unas` is exactly that. What nothing owns still reaches
            # the fallback router, so an unknown command is answered either way.
            return name in registry.commands()
        # An open state no registered dialog claims belongs to a legacy router:
        # `directory`'s `EditProfile.value` and `kb`'s `KbChat.active` today.
        # This is the transitional stand-in for `StateFilter(None)`, and it is
        # what stops the chain answering a value that `on_value` is about to
        # save. A state a *registered* dialog owns is not declined: the pipeline
        # routes that one itself, and a dialog must not silence other features.
        #
        # `raw_state` is aiogram's own, defaulted the way `StateFilter` defaults
        # it: a message with no sender -- which only a group can produce --
        # resolves no FSM context, so `data` carries no state at all.
        return raw_state is None or raw_state in registry.dialogs()

    return owned


def core_owns_callback(registry: Registry,
                       loaded: list[LoadedFeature]) -> Callable:
    """The same decision for a tap: the longest registered key wins, and
    anything else is a legacy router's until there are none left."""
    any_legacy = any(feature.legacy for feature in loaded)

    async def owned(callback: CallbackQuery) -> bool:
        if match(registry.buttons(), callback.data or "") is not None:
            return True
        # No registered key matched. A legacy router may still own it, and
        # answering here as well as there would answer one tap twice. Once
        # nothing is legacy this filter is deleted with the file, and
        # `buttons.handle_callback` answers the unmatched tap itself rather than
        # leaving the button spinning.
        return not any_legacy

    return owned
