import tests.fixtures_features as fixtures_pkg
from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.loader import Manifest, discover_features
from jbcub_bot.core.models import Role


def test_manifest_defaults():
    m = Manifest(name="x")
    assert m.commands == []
    assert m.intents == []
    assert m.min_role is Role.STUDENT
    assert m.emoji == "📒"


def test_discover_reads_router_and_manifest():
    features = discover_features(fixtures_pkg)
    names = {f.manifest.name for f in features}
    assert "dummy" in names
    dummy = next(f for f in features if f.manifest.name == "dummy")
    assert dummy.manifest.commands == [CommandSpec("ping", "Ping.", Role.STUDENT)]
    assert dummy.router is not None
