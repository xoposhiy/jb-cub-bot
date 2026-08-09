"""The features that still route themselves, hosted inside the new pipeline.

**Phase F deletes this file**, and with it the last of `core/intents.py`. Until
then two shapes coexist in `features/`: `kb` keeps its `Router` and its
`Intent` until the 2026-08-07 spec rewrites it, and it is the only one left --
`help`, `impersonate` and `directory` are all migrated. Two things have to stay
true while both shapes are loaded.

**A legacy `Intent` must still be offered plain text.** `LegacyIntents` is one
chain handler at `pipeline.LEGACY`, after every landmark, so a migrated feature
always gets first refusal -- which is what keeps `directory`'s name search at
`LOOKUP` ahead of `kb`'s offer, exactly as `main.py`'s registration order did.

**A legacy feature must still appear in /help.** `features/help` renders what the
`Registry` holds and deliberately knows nothing about legacy, so `adopt` also
republishes every `Manifest` through a real `BotApi` -- see `_declare`. Without
it, the first migrated `help` would have listed itself and the rest of the bot
would have vanished from /help; with it, a feature's block reads the same
before and after its own migration, which is the only way each migration could
be judged behaviour-preserving -- and is what `kb`'s will be judged by too.

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
from jbcub_bot.core.contract import (
    CANCEL_COMMAND,
    TEXT,
    ContractError,
    Registry,
)
from jbcub_bot.core.intents import Intent, intent_allowed
from jbcub_bot.core.loader import LoadedFeature
from jbcub_bot.core.models import Role
from jbcub_bot.core.pipeline import LEGACY, command_of

logger = logging.getLogger(__name__)

# The name the shim registers under. Not a package in `features/`, and it
# declares no command, no button and no described message, so `features/help`
# renders no heading for it -- there is nothing a reader of /help could do with
# the knowledge that some features are older than others.
FEATURE = "legacy"


class LegacyIntents:
    """The legacy features' `Intent` objects, in one chain slot.

    The walk -- a matching `pattern`, a `min_role` the caller meets, and `False`
    to decline -- was `core/intents.py`'s `IntentRouter.dispatch` and is now
    only here: keeping it rather than delegating is what let that module shrink
    to the `Intent` dataclass and the `intent_allowed` this reads, and what
    makes this the single copy production actually runs.
    """

    def __init__(self, registry: Registry):
        self._registry = registry
        self._intents: list[Intent] = []

    def adopt(self, loaded: list[LoadedFeature]) -> None:
        """Host every legacy feature's intents, and declare its manifest.

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
        _declare(self._registry, loaded)

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
    shim = LegacyIntents(registry)
    bot = registry.api_for(FEATURE)
    bot.describe("🧩", "Legacy", "Features that still route themselves.")
    # Guarded like any other slot. It was `public=True` because `main.py`'s
    # `nl_fallback` ran the intent router for an unlinked caller too -- a habit
    # of the shape this shim is here to retire, not a rule worth keeping: the
    # chain is for people the bot knows, and a stranger gets the core's last
    # word before reaching any of it. Roles *within* the slot are still each
    # intent's own business, via `intent_allowed` in `offer`.
    bot.message(at=LEGACY, when=TEXT)(shim.offer)
    return shim


# --- the bridge ----------------------------------------------------------------

async def _owned_by_a_legacy_router(message: Message) -> None:
    """The handler a bridged command carries, and never runs.

    A legacy `CommandSpec` records no handler at all -- the real one is on the
    feature's own `Router` -- so the bridge registers this in its place and
    `core_owns_message` declines any command carrying it. Loud rather than
    silent if those two ever disagree: a bridged command reaching the core's
    dispatcher would otherwise answer nothing.
    """
    raise RuntimeError(
        "A bridged legacy command reached the core's dispatcher. "
        "core_owns_message should have left it to its own router."
    )


def _declare(registry: Registry, loaded: list[LoadedFeature]) -> None:
    """Republish every legacy `Manifest` as a real declaration.

    `features/help` renders `FeatureRegistration`s and must not learn that legacy
    exists, so the manifest is translated here instead -- once, at boot. The
    bridge fills the *registry*, not the renderer, which is why moving that
    renderer into its feature left this untouched. The
    shape is the same one a migrated feature produces, which is what makes a
    feature's /help block read the same on both sides of its own migration.
    """
    for feature in loaded:
        if not feature.legacy:
            continue
        # Everything the manifest still holds. It used to carry a
        # `Manifest.min_role` as well, read nowhere in `src/` and with no
        # counterpart in a contract that guards declarations rather than whole
        # features; that field is gone.
        manifest = feature.manifest
        bot = registry.api_for(feature.name)
        # `name.capitalize()` is exactly what the old renderer built the heading
        # from; a title only becomes something a feature says about itself once
        # it declares one.
        bot.describe(manifest.emoji, manifest.name.capitalize(),
                     manifest.help_text)
        for spec in manifest.commands:
            _refuse_duplicate(registry, feature.name, spec.name)
            bot.command(spec.name, spec.description, usage=spec.usage,
                        public=spec.public,
                        role=_role(spec.min_role))(_owned_by_a_legacy_router)
        for intent in manifest.intents:
            # A note, not a `message`: the shim above already routes these, and
            # a second chain entry would offer the same text twice -- besides
            # needing a unique `at=` per intent. A note renders `  💬 {text}`,
            # which is the old `_intent_line` character for character.
            #
            # Never public, whatever the role: the old `_intent_visible` hid
            # every intent line from an unlinked caller, where `_command_visible`
            # showed a public command.
            bot.note(f"💬 {intent.description}", role=_role(intent.min_role))


def _role(min_role: Role) -> Role | None:
    """`min_role=STUDENT` refuses nobody, and the contract spells that as no
    role at all; anything else is the rank the guard asks for."""
    return None if min_role is Role.STUDENT else min_role


def _refuse_duplicate(registry: Registry, feature: str, name: str) -> None:
    """The one thing the bridge checks for itself.

    `registry.validate()` runs inside `load_features`, before these declarations
    exist, and re-running it over them would refuse the bridge rather than
    verify it: a bridged command's handler is a placeholder, and a legacy
    manifest may declare `/cancel`, which rule 9 forbids a *feature* to own --
    `directory` did while it was legacy. So the bridge validates the one thing it
    could get wrong on its own: a name a migrated feature already took, which
    `Registry.commands()` would otherwise resolve to whichever came last.
    """
    first = registry.commands().get(name)
    if first is not None:
        raise ContractError(
            f"Command '/{name}' is declared by '{first.feature}' and by the "
            f"legacy manifest of '{feature}'. A feature is either migrated or "
            f"legacy: drop whichever declaration it no longer needs."
        )


def core_owns_message(registry: Registry,
                      loaded: list[LoadedFeature]) -> Callable:
    """The filter for the core's message entry point: True when the registry (or
    the core itself) has something for this message, False when a legacy router
    still owns it."""
    # The one command the core would otherwise take out from under a legacy
    # router: `validate()` forbids a feature registering `cancel`, so it can
    # never appear in `registry.commands()`, and the core answers it itself.
    # `directory` owned one while it was legacy, and only its answer could
    # redraw the edit screen the sender was looking at.
    #
    # Nothing claims it any more, so this reads True and `/cancel` reaches the
    # core for every dialog -- registered or not. The line stays until phase F
    # deletes the file: a legacy manifest declaring `cancel` again would still
    # have to be left to its own router, and reading that from `loaded` rather
    # than hard-coding today's answer is what makes this file deletable in one
    # piece.
    claimed_cancel = any(spec.name == CANCEL_COMMAND
                         for feature in loaded if feature.legacy
                         for spec in feature.manifest.commands)

    async def owned(message: Message, raw_state: str | None = None) -> bool:
        # `pipeline`'s own parse, not a second one: this returning True is a
        # promise that `_run_command` will look up the same name, and two
        # readings that drifted apart would decline the update to a router
        # that no longer owns it. A caption counts, because a command in a
        # caption is as deliberate an address as one typed on its own.
        command = command_of(message)
        if command is not None:
            name, _ = command
            if name == CANCEL_COMMAND:
                return not claimed_cancel
            # A command the registry does not know is declined rather than
            # taken, because a legacy router may own one its manifest never
            # listed -- `/unas` is exactly that. What nothing owns still reaches
            # the fallback router, so an unknown command is answered either way.
            #
            # A command the registry knows only because `_declare` bridged it
            # is declined too. Being in the registry means /help can list it,
            # not that the core can run it: the handler is still on the
            # feature's own router, and the spec carries the placeholder above
            # in its place.
            spec = registry.commands().get(name)
            return spec is not None \
                and spec.handler is not _owned_by_a_legacy_router
        # An open state no registered dialog claims belongs to a legacy
        # router: `kb`'s `KbChat.active` is the last one.
        # This is the transitional stand-in for `StateFilter(None)`, and it is
        # what stops the chain answering a value that `on_value` is about to
        # save. A state a *registered* dialog owns is not declined: the pipeline
        # routes that one itself, and a dialog must not silence other features.
        #
        # `raw_state` is aiogram's own, defaulted the way `StateFilter` defaults
        # it: a message with no sender -- which only a group can produce --
        # resolves no FSM context, so `data` carries no state at all.
        #
        # Note what this takes that `nl_fallback` did not: it had `F.text`, so
        # a photo or a document went straight to the sub-routers, and here the
        # core takes those too. Safe only because the one non-command
        # `@router.message` left in `features/` is state-gated
        # (`features/kb/handlers.py`) and so is already declined by the line
        # below -- a legacy router that wanted a bare upload would starve.
        # Nothing may add one; migrate it instead.
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
