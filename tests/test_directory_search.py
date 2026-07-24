from jbcub_bot.features.directory.search import list_cohort, search_users
from jbcub_bot.core.models import User


def _seed(session):
    session.add_all([
        User(first_name="Ivan", last_name="Ivanov", handle_sheet="ivanov",
             primary_cohort="2024"),
        User(first_name="Petr", last_name="Petrov", handle_observed="petrov",
             primary_cohort="2024"),
        User(first_name="Anna", last_name="Smith", handle_sheet="asmith",
             primary_cohort="2021"),
    ])
    session.commit()


def test_search_by_first_name_substring(session):
    _seed(session)
    results = search_users(session, "ivan")
    assert {u.full_name for u in results} == {"Ivan Ivanov"}


def test_search_by_last_name_substring(session):
    _seed(session)
    results = search_users(session, "smith")
    assert {u.full_name for u in results} == {"Anna Smith"}


def test_search_by_handle(session):
    _seed(session)
    assert search_users(session, "petrov")[0].full_name == "Petr Petrov"


def test_list_cohort_by_primary(session):
    _seed(session)
    names = {u.full_name for u in list_cohort(session, "2024")}
    assert names == {"Ivan Ivanov", "Petr Petrov"}
