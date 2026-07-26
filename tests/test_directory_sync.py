from types import SimpleNamespace
from unittest.mock import AsyncMock

from jbcub_bot.features.directory.handlers import cmd_sync
from jbcub_bot.core.models import Role, User

# Headers matching mapping/sdt-2025-2028.yaml (all mapped columns must be present).
COHORT_HEADER = ["Matriculation Num.", "Last name", "First name", "Telegram",
                 "Email", "CUB Email", "GitHub", "Codeforces", "Birthday date",
                 "Citizenship", "Comment"]


def _cohort_row(matr, last, first, tg):
    return [matr, last, first, tg, "", "", "", "", "", "", ""]


# Headers matching mapping/rights.yaml.
RIGHTS_HEADER = ["Matriculation Number", "Last name", "First name", "Role",
                 "Telegram"]


def _settings():
    return SimpleNamespace(
        google_service_account_file="sa.json",
        google_service_account_json="",
        rights_sheet_id="RIGHTS",
        cohorts_tab="Cohorts",
        rights_tab="Rights",
        rights_mapping="rights.yaml",
        mapping_dir="mapping",
    )


async def test_sync_denied_for_non_admin(session, monkeypatch):
    called = []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows",
                        lambda *a, **k: called.append(1) or [])
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="S", role=Role.STUDENT), session=session)
    msg.answer.assert_awaited_with("Admins only.")
    assert called == []  # no fetch, no writes


async def test_sync_aborts_and_writes_nothing_on_cohort_parse_error(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "Mapping"],
                    ["2024", "AAA", "sdt-2025-2028.yaml"],
                    ["2099", "BBB", "sdt-2025-2028.yaml"]]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "BBB":
            return [["Last name"], ["NoMatricColumn"]]  # missing mapped cols -> MappingError
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    assert "aborted" in msg.answer.await_args.args[0].lower()
    assert session.query(User).count() == 0  # write phase never reached


async def test_sync_happy_path(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "Mapping"], ["2024", "AAA", "sdt-2025-2028.yaml"]]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER,
                    ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    u = session.query(User).filter_by(matriculation="30000001").one()
    assert u.role is Role.ADMIN
    assert u.primary_cohort == "2024"
    assert u.first_name == "Ivan"
    assert u.last_name == "Ivanov"
    assert "Sync done." in msg.answer.await_args.args[0]


async def test_sync_creates_searchable_admin_only_in_rights(session, monkeypatch):
    # An admin who appears only in the Rights tab (no cohort, no matriculation)
    # must still become a real, searchable User row, keyed by Telegram handle.
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "Mapping"], ["2024", "AAA", "sdt-2025-2028.yaml"]]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER,
                    ["", "Sidorov", "Sergey", "Admin", "sidorov"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    from jbcub_bot.features.directory.search import search_users
    results = search_users(session, "sidorov")
    assert len(results) == 1
    assert results[0].role is Role.ADMIN
    assert results[0].full_name == "Sergey Sidorov"


async def test_sync_aborts_and_writes_nothing_on_invalid_role(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "Mapping"], ["2024", "AAA", "sdt-2025-2028.yaml"]]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER,
                    ["30000001", "Ivanov", "Ivan", "superuser", "ivan"]]  # invalid role
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    assert "aborted" in msg.answer.await_args.args[0].lower()
    assert session.query(User).count() == 0
