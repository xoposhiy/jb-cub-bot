from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.search import list_cohort, list_cohort_names, rank_users


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


# --- departed students ----------------------------------------------------

def _depart(session, last_name):
    user = session.query(User).filter_by(last_name=last_name).one()
    user.departed_at = "2026-07-28"
    session.commit()


def test_search_skips_a_departed_person(session):
    # Their row keeps the data the roster had when they left; leaving them
    # findable means answering questions about someone who is gone.
    _seed(session)
    _depart(session, "Smith")
    assert rank_users(session, "asmith") == []


def test_search_finds_a_departed_person_when_the_viewer_may_see_them(session):
    _seed(session)
    _depart(session, "Smith")
    assert _names(rank_users(session, "asmith", include_departed=True)) == \
        ["Anna Smith"]


def test_list_cohort_skips_a_departed_member(session):
    _seed(session)
    _depart(session, "Chsheglov")
    assert [u.full_name for u in list_cohort(session, "2024")] == \
        ["Iaroslav Belozerov"]


def test_list_cohort_includes_a_departed_member_when_asked(session):
    _seed(session)
    _depart(session, "Chsheglov")
    names = {u.full_name
             for u in list_cohort(session, "2024", include_departed=True)}
    assert names == {"Iaroslav Belozerov", "Igor Chsheglov"}


def test_list_cohort_names_newest_first_and_only_where_someone_is_current(session):
    session.add_all([
        User(first_name="A", last_name="One", primary_cohort="2023"),
        User(first_name="B", last_name="Two", primary_cohort="2024"),
        User(first_name="C", last_name="Three", primary_cohort="2024"),
        User(first_name="D", last_name="Gone", primary_cohort="2019",
             departed_at="2026-07-28"),
        User(first_name="E", last_name="Staff", role=Role.ADMIN),
    ])
    session.commit()
    assert list_cohort_names(session) == ["2024", "2023"]
