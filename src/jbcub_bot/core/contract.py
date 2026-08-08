"""Everything a feature is allowed to declare, and everything the core knows
about it.

A feature's `register(bot)` gets a `BotApi` and describes itself through it; the
`Registry` behind that api is the single place the core looks to route, refuse
or list anything. Declaring is deliberately separate from wiring -- nothing here
touches aiogram routing -- which is what lets `validate()` reject a whole class
of mistakes (two commands of one name, two handlers at one position, a typo in
an injected parameter) at boot, where they are loud, rather than in production.
"""
import inspect
from dataclasses import dataclass, field
from typing import Callable

from aiogram.types import Message

from jbcub_bot.core.dialogs import DialogHandle, state_name
from jbcub_bot.core.models import Role, User


# --- predicates ---------------------------------------------------------------
# Plain `(Message) -> bool`, not aiogram filters, so a feature can write its own
# and test it by calling it. TEXT deliberately does not exclude a leading "/":
# a command never reaches the chain, the core dispatches it first, so
# `~F.text.startswith("/")` has no reason to reappear inside a predicate.

def TEXT(message: Message) -> bool:
    return message.text is not None


def PHOTO(message: Message) -> bool:
    return bool(message.photo)


def DOCUMENT(message: Message) -> bool:
    return message.document is not None


def ANY(message: Message) -> bool:
    return True


# --- the calling convention ----------------------------------------------------

# The core dispatches to feature handlers itself, so it cannot lean on aiogram's
# dependency injection; this is the replacement. A tuple rather than a set so
# the error message in `validate()` lists them in a stable, readable order.
INJECTABLES = ("principal", "session", "bot", "impersonator", "dialog", "arg",
               "oplog")


def _declared_injections(fn) -> list[str]:
    """The names a handler expects the core to fill in.

    The event is always the first positional parameter, whatever it is called,
    so everything after it is an injection request. `inspect.signature` follows
    `__wrapped__`, so a decorated handler is read by its real signature.
    """
    params = [
        p for p in inspect.signature(fn).parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                      inspect.Parameter.KEYWORD_ONLY)
    ]
    return [p.name for p in params[1:]]


async def call_handler(fn, event, **available):
    """Call a feature handler: the event positionally, the rest by name.

    Only what the handler declares is passed, so a handler that has no use for
    `session` does not have to name it -- which is what keeps handler bodies
    written against aiogram's injection working unchanged. A declared name that
    is not on offer for this kind of event is left out too; `validate()` has
    already refused the ones that are simply typos.
    """
    kwargs = {name: available[name] for name in _declared_injections(fn)
              if name in available}
    return await fn(event, **kwargs)


# --- what a declaration is -----------------------------------------------------

@dataclass(frozen=True)
class Guard:
    """The whole of access control: see `core/guards.py` for what it means."""
    public: bool = False
    role: Role | None = None


@dataclass(frozen=True)
class CommandSpec:
    feature: str
    name: str
    description: str
    usage: str
    guard: Guard
    listed: bool
    handler: Callable


@dataclass(frozen=True)
class MessageSpec:
    feature: str
    at: int
    when: Callable[[Message], bool]
    description: str
    guard: Guard
    handler: Callable


@dataclass(frozen=True)
class ButtonSpec:
    feature: str
    key: str
    guard: Guard
    handler: Callable


@dataclass(frozen=True)
class DialogSpec:
    feature: str
    name: str
    on_text: Callable
    on_cancel: Callable | None
    guard: Guard


@dataclass(frozen=True)
class NoteSpec:
    # A callable takes the principal and returns the line: kb's note changes on
    # /kb_reload, so a static string would lie.
    feature: str
    text: str | Callable[[User | None], str]
    guard: Guard


@dataclass(frozen=True)
class Description:
    emoji: str
    title: str
    summary: str


@dataclass
class FeatureRegistration:
    """One feature's whole declaration. `name` is its package name, so there is
    nothing to keep in sync."""
    name: str
    description: Description | None = None
    commands: list[CommandSpec] = field(default_factory=list)
    messages: list[MessageSpec] = field(default_factory=list)
    buttons: list[ButtonSpec] = field(default_factory=list)
    dialogs: list[DialogSpec] = field(default_factory=list)
    notes: list[NoteSpec] = field(default_factory=list)


class ContractError(Exception):
    """A feature declared something the core cannot honour. Raised at startup,
    before polling, because loud at boot beats discovered in production."""


# --- the surface a feature sees -------------------------------------------------

class BotApi:
    """What `register(bot)` receives: one instance per feature, appending to the
    shared registry under the feature's own name."""

    def __init__(self, feature: str, registry: "Registry"):
        self.feature = feature
        self.registry = registry
        self._own = registry._registration(feature)

    def describe(self, emoji: str, title: str, summary: str) -> None:
        """The feature's heading in /help. Required -- a feature without one has
        no heading, so its absence is a startup error rather than a blank line."""
        self._own.description = Description(emoji, title, summary)

    def command(self, name: str, description: str, *,
                usage: str = "", public: bool = False,
                role: Role | None = None, listed: bool = True) -> Callable:
        def decorator(fn):
            self._own.commands.append(CommandSpec(
                feature=self.feature, name=name, description=description,
                usage=usage, guard=Guard(public, role), listed=listed,
                handler=fn,
            ))
            return fn

        return decorator

    def message(self, *, at: int, when: Callable[[Message], bool] = TEXT,
                description: str = "", public: bool = False,
                role: Role | None = None) -> Callable:
        def decorator(fn):
            self._own.messages.append(MessageSpec(
                feature=self.feature, at=at, when=when, description=description,
                guard=Guard(public, role), handler=fn,
            ))
            return fn

        return decorator

    def button(self, key: str, *, public: bool = False,
               role: Role | None = None) -> Callable:
        def decorator(fn):
            self._own.buttons.append(ButtonSpec(
                feature=self.feature, key=key, guard=Guard(public, role),
                handler=fn,
            ))
            return fn

        return decorator

    def dialog(self, name: str, *, on_text: Callable,
               on_cancel: Callable | None = None, public: bool = False,
               role: Role | None = None) -> DialogHandle:
        """A plain call, not a decorator -- a dialog has two handlers, so there
        is nothing single to decorate. The handle is what a feature keeps in
        order to start the dialog later; the spec behind it is how the core
        routes into it, and is nobody else's business."""
        self._own.dialogs.append(DialogSpec(
            feature=self.feature, name=name, on_text=on_text,
            on_cancel=on_cancel, guard=Guard(public, role),
        ))
        return DialogHandle(self.feature, name)

    def note(self, text: str | Callable[[User | None], str], *,
             public: bool = False, role: Role | None = None) -> None:
        self._own.notes.append(NoteSpec(
            feature=self.feature, text=text, guard=Guard(public, role),
        ))

    def features(self) -> list[FeatureRegistration]:
        """Every loaded feature. `help` uses this to assemble itself."""
        return self.registry.features()


class Registry:
    """Every feature's declaration, in discovery order."""

    def __init__(self):
        self._features: dict[str, FeatureRegistration] = {}

    def api_for(self, feature: str) -> BotApi:
        return BotApi(feature, self)

    def _registration(self, feature: str) -> FeatureRegistration:
        """The feature's own slot, created on first ask -- so a feature that
        declares nothing at all is still known, and still fails `validate()`
        for having no `describe`."""
        if feature not in self._features:
            self._features[feature] = FeatureRegistration(name=feature)
        return self._features[feature]

    def features(self) -> list[FeatureRegistration]:
        return list(self._features.values())

    def commands(self) -> dict[str, CommandSpec]:
        return {spec.name: spec
                for reg in self._features.values() for spec in reg.commands}

    def chain(self) -> list[MessageSpec]:
        specs = [spec for reg in self._features.values() for spec in reg.messages]
        return sorted(specs, key=lambda spec: spec.at)

    def buttons(self) -> list[ButtonSpec]:
        return [spec for reg in self._features.values() for spec in reg.buttons]

    def dialogs(self) -> dict[str, DialogSpec]:
        # Keyed by the state name a running dialog reports, so `owner()` looks
        # a spec up directly. That derivation lives in `core/dialogs` and only
        # there -- it is also why a feature writes no StatesGroup and why two
        # features may both call their dialog "edit".
        return {state_name(spec.feature, spec.name): spec
                for reg in self._features.values() for spec in reg.dialogs}

    def validate(self) -> None:
        """Refuse anything the core could not honour, before polling starts."""
        commands: dict[str, str] = {}
        buttons: dict[str, str] = {}
        positions: dict[int, str] = {}
        for reg in self._features.values():
            if reg.description is None:
                raise ContractError(
                    f"Feature '{reg.name}' never called describe(). Add "
                    f"bot.describe(emoji, title, summary) to its register()."
                )
            for spec in reg.commands:
                where = f"Command '/{spec.name}'"
                _check_guard(reg.name, where, spec.guard)
                _check_parameters(reg.name, where, spec.handler)
                if not spec.description:
                    raise ContractError(
                        f"{where} in '{reg.name}' has an empty description. "
                        f"Unlisted is not undocumented -- describe it."
                    )
                _claim(commands, spec.name, reg.name, where, "Rename one.")
            for spec in reg.messages:
                where = f"Position at={spec.at}"
                _check_guard(reg.name, where, spec.guard)
                _check_parameters(reg.name, where, spec.handler)
                _claim(positions, spec.at, reg.name, where,
                       "Give one of them another at=.")
            for spec in reg.buttons:
                where = f"Button key '{spec.key}'"
                _check_guard(reg.name, where, spec.guard)
                _check_parameters(reg.name, where, spec.handler)
                _claim(buttons, spec.key, reg.name, where, "Rename one.")
            # Dialog names are scoped to their feature -- the state name is
            # "<feature>:<name>" -- so two features may both call one "edit"
            # and only a collision within a feature is a mistake.
            dialogs: set[str] = set()
            for spec in reg.dialogs:
                where = f"Dialog '{spec.name}'"
                _check_guard(reg.name, where, spec.guard)
                _check_parameters(reg.name, f"{where} (on_text)", spec.on_text)
                if spec.on_cancel is not None:
                    _check_parameters(reg.name, f"{where} (on_cancel)",
                                      spec.on_cancel)
                if spec.name in dialogs:
                    raise ContractError(
                        f"{where} is declared twice in '{reg.name}'. "
                        f"Rename one."
                    )
                dialogs.add(spec.name)
            for spec in reg.notes:
                _check_guard(reg.name, "Note", spec.guard)


def _claim(seen: dict, key, feature: str, what: str, fix: str) -> None:
    """Record `feature` as the owner of `key`, or name whoever got there first."""
    first = seen.get(key)
    if first is not None:
        raise ContractError(
            f"{what} is declared by both '{first}' and '{feature}'. {fix}"
        )
    seen[key] = feature


def _check_guard(feature: str, where: str, guard: Guard) -> None:
    if guard.public and guard.role is not None:
        raise ContractError(
            f"{where} in '{feature}' declares public=True together with "
            f"role=Role.{guard.role.name}. Drop one: public means no principal "
            f"is needed, role means a principal of that rank is."
        )


def _check_parameters(feature: str, where: str, handler: Callable) -> None:
    """A typo must not silently mean 'never injected'."""
    for name in _declared_injections(handler):
        if name not in INJECTABLES:
            raise ContractError(
                f"{where} in '{feature}' has a handler declaring parameter "
                f"'{name}', which the core cannot inject. It can inject: "
                f"{', '.join(INJECTABLES)}."
            )
