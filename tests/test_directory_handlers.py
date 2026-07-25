import jbcub_bot.features.directory as directory
from jbcub_bot.features.directory.handlers import name_search_intent, set_status
from jbcub_bot.core.models import Role, User


def test_manifest_exposes_contract():
    assert directory.manifest.name == "directory"
    names = {c.name for c in directory.manifest.commands}
    assert {"me", "cohort", "sync", "start"} <= names
    sync = next(c for c in directory.manifest.commands if c.name == "sync")
    assert sync.min_role is Role.ADMIN
    assert directory.manifest.min_role is Role.STUDENT
    assert any(i.name == "directory.search" for i in directory.manifest.intents)
    assert directory.router is not None


def test_search_intent_matches_plain_text():
    import re
    assert re.search(name_search_intent.pattern, "Ivan", re.IGNORECASE)


def test_set_status_updates_user(session):
    u = User(last_name="Ivan", telegram_id=1)
    session.add(u)
    session.commit()
    set_status(session, u, "looking for a teammate")
    session.refresh(u)
    assert u.status_line == "looking for a teammate"
