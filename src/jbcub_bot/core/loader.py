"""Turning the `features/` package into a loaded, validated registry.

A feature is a package under `features/` exporting `register(bot)`. The loader
imports every sub-package, hands each one a `BotApi` of its own, and calls it.
It knows nothing about what any feature does -- discovery is the whole job --
but two things belong here and nowhere else: a package the loader cannot read
crashes the boot instead of disappearing, and the chain every feature declared
is logged once its order is resolved, so reading the deploy log beats opening
five files.
"""
import importlib
import logging
import pkgutil
from dataclasses import dataclass

from jbcub_bot.core.contract import ContractError, Registry

logger = logging.getLogger(__name__)


@dataclass
class LoadedFeature:
    name: str
    module: object


def load_features(package, registry: Registry) -> list[LoadedFeature]:
    """Import every sub-package and let each one register itself.

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
    if register is None:
        # A ContractError like any other boot refusal: whoever catches "a
        # feature is wrong, do not start polling" should need to know one
        # exception. Loud rather than skipped in silence, which the loader used
        # to do and which is the worst possible way for a contributed feature
        # to fail.
        raise ContractError(
            f"Feature package '{package.__name__}.{name}' exports no "
            f"register(bot). Add `def register(bot): ...` to its __init__.py. "
            f"A `router` and a `manifest` are not a feature any more -- that "
            f"shape went with `kb`'s migration, and nothing may bring it back."
        )
    # `api_for` creates the feature's slot, so a `register` that declares
    # nothing at all is still known -- and still fails `validate()` for never
    # having called `describe`.
    register(registry.api_for(name))
    return LoadedFeature(name=name, module=module)


def _log_chain(registry: Registry) -> None:
    """One line per chain entry, in the order the core will offer a message
    around, which is what makes a misplaced `at=` obvious from the deploy log."""
    for spec in registry.chain():
        # getattr rather than `__name__`: a handler that is a callable object
        # must not turn a startup log line into a startup crash.
        handler = getattr(spec.handler, "__name__", repr(spec.handler))
        logger.info("chain at=%s: %s.%s", spec.at, spec.feature, handler)
