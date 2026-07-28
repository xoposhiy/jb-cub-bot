from types import SimpleNamespace
from unittest.mock import AsyncMock

import jbcub_bot.features.directory as directory
from jbcub_bot.features.directory.handlers import (
    cmd_cohort,
    name_search,
    name_search_intent,
    set_status,
)
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


# --- departed students are for admins only --------------------------------

def _seed_departed(session):
    session.add(User(first_name="Eve", last_name="Expelled", role=Role.STUDENT,
                     primary_cohort="2024", handle_sheet="eve",
                     matriculation="30000009", departed_at="2026-07-28"))
    session.commit()


def _viewer(role):
    return User(first_name="V", last_name="Viewer", role=role,
                primary_cohort="2024")


async def test_cohort_list_omits_a_departed_mate_for_a_student(session):
    _seed_departed(session)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_cohort(msg, principal=_viewer(Role.STUDENT), session=session)
    assert "Expelled" not in msg.answer.await_args.args[0]


async def test_cohort_list_omits_a_departed_mate_for_a_teacher(session):
    # Teachers see every field a student may hide, but not a person who left.
    _seed_departed(session)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_cohort(msg, principal=_viewer(Role.TEACHER), session=session)
    assert "Expelled" not in msg.answer.await_args.args[0]


async def test_cohort_list_shows_a_departed_mate_to_an_admin(session):
    _seed_departed(session)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_cohort(msg, principal=_viewer(Role.ADMIN), session=session)
    assert "Eve Expelled" in msg.answer.await_args.args[0]


async def test_search_by_handle_does_not_reach_a_departed_profile(session):
    # A handle is a direct lookup, so it would bypass any hiding that only
    # covered the /cohort listing.
    _seed_departed(session)
    msg = SimpleNamespace(text="eve", answer=AsyncMock())

    took_it = await name_search(msg, principal=_viewer(Role.STUDENT),
                                session=session)

    assert took_it is False  # declined, so the fallback answers
    msg.answer.assert_not_awaited()


async def test_search_reaches_a_departed_profile_for_an_admin(session):
    _seed_departed(session)
    msg = SimpleNamespace(text="eve", answer=AsyncMock())

    await name_search(msg, principal=_viewer(Role.ADMIN), session=session)

    assert "Eve Expelled" in msg.answer.await_args.args[0]
