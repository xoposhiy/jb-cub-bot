import importlib
import pkgutil
from dataclasses import dataclass, field

from aiogram import Router

from sdt_bot.core.models import Role


@dataclass
class Manifest:
    name: str
    commands: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    min_role: Role = Role.STUDENT
    help_text: str = ""


@dataclass
class LoadedFeature:
    manifest: Manifest
    router: Router


def discover_features(package) -> list[LoadedFeature]:
    found: list[LoadedFeature] = []
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        manifest = getattr(module, "manifest", None)
        router = getattr(module, "router", None)
        if manifest is None or router is None:
            continue
        found.append(LoadedFeature(manifest=manifest, router=router))
    return found
