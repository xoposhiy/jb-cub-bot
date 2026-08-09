"""What the loader makes of a `features/` package: call `register`, validate the
whole lot once, log the resolved chain, and refuse to boot on a package it
cannot read.
"""
import logging

import pytest

import tests.fixtures_features.command_clash as clash_pkg
import tests.fixtures_features.mixed_shapes as mixed_pkg
import tests.fixtures_features.no_describe as no_describe_pkg
import tests.fixtures_features.no_shape as no_shape_pkg
from jbcub_bot.core.contract import ContractError, Registry
from jbcub_bot.core.loader import load_features
from jbcub_bot.core.pipeline import AGENT, LOOKUP

LOADER = "jbcub_bot.core.loader"


def _logged(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records
            if record.name == LOADER]


# --- what a loaded feature is --------------------------------------------------

def test_a_feature_registers_through_its_own_api():
    registry = Registry()
    loaded = load_features(mixed_pkg, registry)
    alpha = next(feature for feature in loaded if feature.name == "alpha")
    assert alpha.module is mixed_pkg.alpha
    # Declared under the package name, with nothing for the feature to repeat.
    assert registry.commands()["alpha"].feature == "alpha"
    assert registry.features()[0].description.title == "Alpha"


def test_discovery_order_is_alphabetical():
    loaded = load_features(mixed_pkg, Registry())
    assert [feature.name for feature in loaded] == ["alpha", "zulu"]


# --- the startup log ----------------------------------------------------------

def test_the_resolved_chain_is_logged_in_at_order(caplog):
    with caplog.at_level(logging.INFO, logger=LOADER):
        load_features(mixed_pkg, Registry())
    # zulu is discovered last and runs first: the log follows `at`, not
    # discovery, because that is the order a reader of the deploy log needs.
    assert _logged(caplog) == [
        f"chain at={LOOKUP}: zulu.find_name",
        f"chain at={AGENT}: alpha.read_anything",
    ]


# --- what crashes the boot ----------------------------------------------------

def test_a_package_with_no_register_crashes_and_names_itself():
    with pytest.raises(ContractError) as raised:
        load_features(no_shape_pkg, Registry())
    message = str(raised.value)
    assert "mystery" in message
    assert "register(bot)" in message


def test_a_register_that_forgets_describe_crashes_and_names_the_feature():
    with pytest.raises(ContractError) as raised:
        load_features(no_describe_pkg, Registry())
    assert "shy" in str(raised.value)


def test_a_collision_between_the_first_and_last_feature_still_crashes():
    registry = Registry()
    with pytest.raises(ContractError) as raised:
        load_features(clash_pkg, registry)
    message = str(raised.value)
    assert "/ping" in message
    assert "aardvark" in message and "zebra" in message
    # All three registered before anything was checked, so validate() ran once
    # at the end rather than after each feature.
    assert [feature.name for feature in registry.features()] == \
        ["aardvark", "middle", "zebra"]


def test_a_refused_boot_logs_no_chain(caplog):
    with caplog.at_level(logging.INFO, logger=LOADER):
        with pytest.raises(ContractError):
            load_features(clash_pkg, Registry())
    assert _logged(caplog) == []
