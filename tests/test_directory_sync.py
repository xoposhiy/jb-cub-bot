from types import SimpleNamespace
from unittest.mock import AsyncMock

from sdt_bot.features.directory.handlers import cmd_sync
from sdt_bot.core.models import Role, User


def _settings():
    return SimpleNamespace(
        google_service_account_file="sa.json",
        rights_sheet_id="RIGHTS",
        cohorts_tab="Cohorts",
        rights_tab="Rights",
        rights_mapping="rights.yaml",
        mapping_dir="mapping",
    )


async def test_sync_denied_for_non_admin(session, monkeypatch):
    called = []
    monkeypatch.setattr("sdt_bot.features.directory.handlers.fetch_rows",
                        lambda *a, **k: called.append(1) or [])
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(name="S", role=Role.STUDENT), session=session)
    msg.answer.assert_awaited_with("Admins only.")
    assert called == []  # no fetch, no writes


async def test_sync_aborts_and_writes_nothing_on_cohort_parse_error(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "Mapping"],
                    ["2024", "AAA", "cohort-2024.yaml"],
                    ["2099", "BBB", "cohort-2024.yaml"]]
        if sheet_id == "AAA":
            return [["Matriculation Number", "Full Name", "Telegram", "Gmail",
                     "GitHub", "Codeforces", "Cohort"],
                    ["30000001", "Ivan", "ivan", "", "", "", "2024"]]
        if sheet_id == "BBB":
            return [["Full Name"], ["NoMatricColumn"]]  # missing mapped cols -> MappingError
        return []
    monkeypatch.setattr("sdt_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("sdt_bot.features.directory.handlers.get_settings", _settings)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(name="A", role=Role.ADMIN), session=session)
    assert "aborted" in msg.answer.await_args.args[0].lower()
    assert session.query(User).count() == 0  # write phase never reached


async def test_sync_happy_path(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "Mapping"], ["2024", "AAA", "cohort-2024.yaml"]]
        if sheet_id == "AAA":
            return [["Matriculation Number", "Full Name", "Telegram", "Gmail",
                     "GitHub", "Codeforces", "Cohort"],
                    ["30000001", "Ivan Ivanov", "ivan", "", "", "", "2024"]]
        if range_ == "Rights!A:Z":
            return [["Matriculation Number", "Full Name", "Role", "Telegram"],
                    ["30000001", "Ivan Ivanov", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("sdt_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("sdt_bot.features.directory.handlers.get_settings", _settings)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(name="A", role=Role.ADMIN), session=session)
    u = session.query(User).filter_by(matriculation="30000001").one()
    assert u.role is Role.ADMIN
    assert u.primary_cohort == "2024"
    assert "Sync done." in msg.answer.await_args.args[0]
