"""Process-wide list of loaded feature manifests.

The `help` feature is auto-discovered like any other and cannot import its
siblings, so build_dispatcher publishes every loaded manifest here for it to
read at request time.
"""
from jbcub_bot.core.loader import Manifest

_MANIFESTS: list[Manifest] = []


def reset() -> None:
    _MANIFESTS.clear()


def register(manifest: Manifest) -> None:
    _MANIFESTS.append(manifest)


def all_manifests() -> list[Manifest]:
    return list(_MANIFESTS)
