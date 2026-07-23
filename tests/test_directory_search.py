from sdt_bot.features.directory.search import list_cohort, search_users
from sdt_bot.core.models import User


def _seed(session):
    session.add_all([
        User(name="Ivan Ivanov", handle_sheet="ivanov", primary_cohort="2024"),
        User(name="Petr Petrov", handle_observed="petrov", primary_cohort="2024"),
        User(name="Anna Smith", handle_sheet="asmith", primary_cohort="2021"),
    ])
    session.commit()


def test_search_by_name_substring(session):
    _seed(session)
    results = search_users(session, "ivan")
    assert {u.name for u in results} == {"Ivan Ivanov"}


def test_search_by_handle(session):
    _seed(session)
    assert search_users(session, "petrov")[0].name == "Petr Petrov"


def test_list_cohort_by_primary(session):
    _seed(session)
    names = {u.name for u in list_cohort(session, "2024")}
    assert names == {"Ivan Ivanov", "Petr Petrov"}
