import tests.fixtures_features as fixtures_pkg
from sdt_bot.core.loader import Manifest, discover_features
from sdt_bot.core.models import Role


def test_manifest_defaults():
    m = Manifest(name="x")
    assert m.commands == []
    assert m.intents == []
    assert m.min_role is Role.STUDENT


def test_discover_reads_router_and_manifest():
    features = discover_features(fixtures_pkg)
    names = {f.manifest.name for f in features}
    assert "dummy" in names
    dummy = next(f for f in features if f.manifest.name == "dummy")
    assert dummy.manifest.commands == ["ping"]
    assert dummy.router is not None
