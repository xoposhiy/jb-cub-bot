import jbcub_bot.features as features_pkg
from jbcub_bot.core import registry
from jbcub_bot.core.loader import Manifest, discover_features
from jbcub_bot.main import build_dispatcher


def test_reset_clears():
    registry.register(Manifest(name="a"))
    registry.reset()
    assert registry.all_manifests() == []


def test_register_and_all():
    registry.reset()
    m = Manifest(name="a")
    registry.register(m)
    assert registry.all_manifests() == [m]


def test_all_returns_copy():
    registry.reset()
    registry.register(Manifest(name="a"))
    snapshot = registry.all_manifests()
    snapshot.append(Manifest(name="b"))
    assert len(registry.all_manifests()) == 1


def _reset_routers():
    for feature in discover_features(features_pkg):
        feature.router._parent_router = None


def test_build_dispatcher_populates_registry():
    _reset_routers()
    build_dispatcher(session_factory=lambda: None)
    names = {m.name for m in registry.all_manifests()}
    assert {"directory", "impersonate", "help"} <= names


def test_build_dispatcher_is_idempotent():
    _reset_routers()
    build_dispatcher(session_factory=lambda: None)
    first = len(registry.all_manifests())
    _reset_routers()
    build_dispatcher(session_factory=lambda: None)
    assert len(registry.all_manifests()) == first
