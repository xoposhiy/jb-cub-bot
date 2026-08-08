"""The pre-contract shapes `core/loader.py` still carries.

What the loader *does* -- call `register`, validate once, log the resolved chain,
refuse a package of neither shape -- is `tests/test_load_features.py`. This file
covers only the two dataclasses that describe a feature which has not migrated
yet: `Manifest`, which every unmigrated `features/<name>/__init__.py` still
builds, and `LoadedFeature`, which is how phase B tells the two shapes apart.
Both go in phase F, and so does this file.
"""
from aiogram import Router

from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.loader import LoadedFeature, Manifest
from jbcub_bot.core.models import Role


def test_manifest_defaults():
    m = Manifest(name="x")
    assert m.commands == []
    assert m.intents == []
    assert m.min_role is Role.STUDENT
    assert m.emoji == "📒"


def test_a_manifest_carries_the_commands_and_intents_the_shim_reads():
    ping = CommandSpec("ping", "Ping.", Role.STUDENT)
    m = Manifest(name="x", commands=[ping], intents=["an intent object"])
    assert m.commands == [ping]
    assert m.intents == ["an intent object"]


def test_a_feature_with_a_router_is_legacy():
    router = Router()
    feature = LoadedFeature(name="relic", module=object(), router=router,
                            manifest=Manifest(name="relic"))
    assert feature.legacy is True


def test_a_migrated_feature_is_not():
    # No router and no manifest is what `register(bot)` leaves behind, and it is
    # what stops phase B including a router that no longer exists.
    feature = LoadedFeature(name="alpha", module=object())
    assert feature.legacy is False
    assert (feature.router, feature.manifest) == (None, None)
