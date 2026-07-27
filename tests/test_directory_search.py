from jbcub_bot.core.models import User
from jbcub_bot.features.directory.search import list_cohort, rank_users


def _seed(session):
    session.add_all([
        User(first_name="Iaroslav", last_name="Belozerov", handle_sheet="yarik",
             primary_cohort="2024"),
        User(first_name="Igor", last_name="Chsheglov", handle_observed="igor",
             primary_cohort="2024"),
        User(first_name="Anna", last_name="Smith", handle_sheet="asmith",
             primary_cohort="2021"),
    ])
    session.commit()


def _names(ranked):
    return [user.full_name for _, user in ranked]


def test_finds_a_latin_name_from_cyrillic(session):
    _seed(session)
    assert _names(rank_users(session, "Ярослав")) == ["Iaroslav Belozerov"]


def test_finds_the_same_person_from_another_transliteration(session):
    _seed(session)
    assert _names(rank_users(session, "Yaroslav")) == ["Iaroslav Belozerov"]


def test_finds_by_last_name(session):
    _seed(session)
    assert _names(rank_users(session, "Щеглов")) == ["Igor Chsheglov"]


def test_finds_by_handle(session):
    _seed(session)
    assert _names(rank_users(session, "asmith")) == ["Anna Smith"]


def test_best_match_comes_first(session):
    _seed(session)
    ranked = rank_users(session, "Anna")
    assert ranked[0][1].full_name == "Anna Smith"
    assert ranked[0][0] >= ranked[-1][0]


def test_small_talk_matches_nobody(session):
    _seed(session)
    assert rank_users(session, "как дела") == []


def test_a_two_letter_query_matches_nobody(session):
    _seed(session)
    assert rank_users(session, "An") == []


def test_list_cohort_by_primary(session):
    _seed(session)
    names = {u.full_name for u in list_cohort(session, "2024")}
    assert names == {"Iaroslav Belozerov", "Igor Chsheglov"}
