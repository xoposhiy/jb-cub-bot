"""Turning the `features/` package into a loaded, validated registry.

A feature is a package under `features/` exporting `register(bot)`. Discovery
is the whole job, but two things belong here and nowhere else: a package the
loader cannot read crashes the boot instead of disappearing, and the resolved
chain order is logged once, so the deploy log beats opening five files.
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

    `validate()` runs once, after the last one: checking per feature would only
    ever see half of a collision.
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
        # A ContractError like every other boot refusal, so "a feature is
        # wrong, do not start polling" is one exception to catch. Loud rather
        # than skipped in silence: this project takes student contributions,
        # and a feature that quietly disappears is the worst way to fail.
        raise ContractError(
            f"Feature package '{package.__name__}.{name}' exports no "
            f"register(bot). Add `def register(bot): ...` to its __init__.py."
        )
    # `api_for` creates the feature's slot, so a `register` that declares
    # nothing is still known -- and still fails `validate()` for it.
    register(registry.api_for(name))
    return LoadedFeature(name=name, module=module)


def _log_chain(registry: Registry) -> None:
    """One line per chain entry, in the resolved order, so a misplaced `at=` is
    obvious from the deploy log."""
    for spec in registry.chain():
        # getattr rather than `__name__`: a handler that is a callable object
        # must not turn a log line into a startup crash.
        handler = getattr(spec.handler, "__name__", repr(spec.handler))
        logger.info("chain at=%s: %s.%s", spec.at, spec.feature, handler)
