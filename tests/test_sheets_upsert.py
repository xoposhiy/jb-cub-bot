from jbcub_bot.core import sheets
from jbcub_bot.core.models import User


def test_upsert_inserts_new(session):
    sheets.upsert_users(session, [
        {"matriculation": "1", "first_name": "Ivan", "last_name": "Ivanov",
         "handle_sheet": "ivan", "primary_cohort": "2024"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.first_name == "Ivan"
    assert u.last_name == "Ivanov"
    assert u.primary_cohort == "2024"


def test_upsert_preserves_bot_owned_fields(session):
    session.add(User(matriculation="1", last_name="Old", telegram_id=777,
                     status_line="hi", handle_observed="ivan_obs",
                     visibility={"gmail": "nobody"}))
    session.commit()
    sheets.upsert_users(session, [
        {"matriculation": "1", "last_name": "New", "handle_sheet": "ivan_sheet"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.last_name == "New"          # sheet-owned updated
    assert u.handle_sheet == "ivan_sheet"
    assert u.telegram_id == 777          # bot-owned preserved
    assert u.status_line == "hi"
    assert u.handle_observed == "ivan_obs"
    assert u.visibility == {"gmail": "nobody"}


def test_upsert_converts_role_string(session):
    from jbcub_bot.core.models import Role
    sheets.upsert_users(session, [
        {"matriculation": "1", "last_name": "Boss", "role": "Admin"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.role is Role.ADMIN


def test_upsert_blank_role_keeps_default(session):
    from jbcub_bot.core.models import Role
    sheets.upsert_users(session, [
        {"matriculation": "1", "last_name": "Stud", "role": ""},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.role is Role.STUDENT


def test_reconcile_reports_drift_unmatched_duplicates(session):
    session.add(User(matriculation="1", last_name="Ivan",
                     handle_observed="ivan_new"))
    session.commit()
    records = [
        {"matriculation": "1", "handle_sheet": "ivan_old"},   # drift
        {"matriculation": "2", "handle_sheet": "x"},          # unmatched
        {"matriculation": "2", "handle_sheet": "x"},          # duplicate key
    ]
    report = sheets.reconcile(session, records)
    assert "1" in report.drift
    assert "2" in report.unmatched
    assert "2" in report.duplicates
