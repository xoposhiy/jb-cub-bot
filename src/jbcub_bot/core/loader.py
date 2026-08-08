"""Turning the `features/` package into a loaded, validated registry.

A feature is a package under `features/` exporting `register(bot)`. The loader
imports every sub-package, hands each one a `BotApi` of its own, and calls it.
It knows nothing about what any feature does -- discovery is the whole job --
but two things belong here and nowhere else: a package the loader cannot read
crashes the boot instead of disappearing, and the chain every feature declared
is logged once its order is resolved, so reading the deploy log beats opening
five files.

`Manifest` is the pre-contract shape and lives here only until phase F retires
it; `core/registry.py`, `core/legacy.py`'s bridge and every unmigrated feature
still import it from here.
"""
import importlib
import logging
import pkgutil
from dataclasses import dataclass, field

from aiogram import Router

from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.contract import ContractError, Registry
from jbcub_bot.core.models import Role

logger = logging.getLogger(__name__)


@dataclass
class Manifest:
    name: str
    commands: list[CommandSpec] = field(default_factory=list)
    intents: list = field(default_factory=list)
    min_role: Role = Role.STUDENT
    help_text: str = ""
    emoji: str = "📒"


@dataclass
class LoadedFeature:
    name: str
    module: object
    # Legacy only, and always both or neither: a migrated feature exports no
    # router and no manifest, and phase F deletes this pair along with the
    # branch that fills it.
    router: Router | None = None
    manifest: Manifest | None = None

    @property
    def legacy(self) -> bool:
        """Still on the old shape, so task 6 includes its router and shims its
        intents rather than finding it in the registry."""
        return self.router is not None


def load_features(package, registry: Registry) -> list[LoadedFeature]:
    """Import every sub-package and let the migrated ones register themselves.

    `validate()` runs once, after the last feature: a collision between the
    first feature and the last is still a collision, and checking per feature
    would only ever see half of one.
    """
    loaded: list[LoadedFeature] = []
    for info in pkgutil.iter_modules(package.__path__):
        loaded.append(_load_one(package, info.name, registry))
    registry.validate()
    _log_chain(registry)
    return loaded


def _load_one(package, name: str, registry: Registry) -> LoadedFeature:
    module = importlib.import_module(f"{package.__name__}.{name}")
    register = getattr(module, "register", None)
    if register is not None:
        # `api_for` creates the feature's slot, so a `register` that declares
        # nothing at all still exists -- and still fails `validate()` for never
        # having called `describe`.
        register(registry.api_for(name))
        return LoadedFeature(name=name, module=module)
    router = getattr(module, "router", None)
    manifest = getattr(module, "manifest", None)
    if router is not None and manifest is not None:
        # The one place the old shape is still tolerated. Phases B to E migrate
        # the features one at a time, so both shapes coexist in `features/`.
        return LoadedFeature(name=name, module=module, router=router,
                             manifest=manifest)
    # A ContractError like any other boot refusal: whoever catches "a feature is
    # wrong, do not start polling" should need to know one exception, and half a
    # legacy pair is as unhonourable a declaration as two /ping commands.
    found = " and ".join(kind for kind, value in (("router", router),
                                                  ("manifest", manifest))
                         if value is not None)
    raise ContractError(
        f"Feature package '{package.__name__}.{name}' exports no "
        f"register(bot); it exports {found or 'nothing the loader can read'}. "
        f"Add `def register(bot): ...` to its __init__.py -- skipping it in "
        f"silence, which the loader used to do, is the worst possible way for "
        f"a contributed feature to fail."
    )


def _log_chain(registry: Registry) -> None:
    """One line per chain entry, in the order the core will offer a message
    around, which is what makes a misplaced `at=` obvious from the deploy log."""
    for spec in registry.chain():
        # getattr rather than `__name__`: a handler that is a callable object
        # must not turn a startup log line into a startup crash.
        handler = getattr(spec.handler, "__name__", repr(spec.handler))
        logger.info("chain at=%s: %s.%s", spec.at, spec.feature, handler)
