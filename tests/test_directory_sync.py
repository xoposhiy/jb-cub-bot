import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jbcub_bot.features.directory import handlers
from jbcub_bot.features.directory.handlers import cmd_sync
from jbcub_bot.core.models import Grade, Role, User

# The Cohorts tab: 'Cohort' and 'Link' describe the cohort, every other column
# names one of our fields and holds that field's column name in the cohort sheet.
COHORTS_HEADER = ["Cohort", "Link", "matriculation", "last_name", "first_name",
                  "handle_sheet", "gmail", "cubemail", "birthday", "citizenship",
                  "comment"]


def _cohorts_row(cohort, link):
    return [cohort, link, "Matriculation Num.", "Last name", "First name",
            "Telegram", "Email", "CUB Email", "Birthday date", "Citizenship",
            "Comment"]


# A cohort's own sheet, named as that row of the Cohorts tab says.
COHORT_HEADER = ["Matriculation Num.", "Last name", "First name", "Telegram",
                 "Email", "CUB Email", "GitHub", "Codeforces", "Birthday date",
                 "Citizenship", "Comment"]


def _cohort_row(matr, last, first, tg):
    return [matr, last, first, tg, "", "", "", "", "", "", ""]


# The Rights tab is ours to shape, so its columns are our field names already.
RIGHTS_HEADER = ["matriculation", "last_name", "first_name", "role",
                 "handle_sheet"]


def _settings():
    return SimpleNamespace(
        google_service_account_file="sa.json",
        google_service_account_json="",
        rights_sheet_id="RIGHTS",
        cohorts_tab="Cohorts",
        rights_tab="Rights",
        gradebook_tab="Gradebook",
    )


GRADEBOOK_TERM_ROW = ["", "", "Fall 2024"]
GRADEBOOK_CATEGORY_ROW = ["", "", "Mandatory"]
GRADEBOOK_LABEL_ROW = ["Last name", "First name", "Math"]


def _gradebook_rows(*data_rows):
    return [
        GRADEBOOK_TERM_ROW,
        GRADEBOOK_CATEGORY_ROW,
        GRADEBOOK_LABEL_ROW,
        *data_rows,
    ]


class ProgressMessage:
    def __init__(self):
        self.edit_text = AsyncMock()


def _sync_message():
    progress = ProgressMessage()
    message = SimpleNamespace(
        answer=AsyncMock(return_value=progress),
        answer_document=AsyncMock(),
    )
    return message, progress


async def test_healthy_three_cohort_sync_sends_start_cohorts_and_final_only(
    session,
    monkeypatch,
):
    cohort_ids = {"2023": "AAA", "2024": "BBB", "2025": "CCC"}

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [
                COHORTS_HEADER,
                *[
                    _cohorts_row(cohort, sheet_id)
                    for cohort, sheet_id in cohort_ids.items()
                ],
            ]
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["", "Boss", "Alice", "Admin", "boss"],
            ]
        for index, (cohort, cohort_id) in enumerate(cohort_ids.items(), start=1):
            if sheet_id == cohort_id and range_ == "A:Z":
                return [
                    COHORT_HEADER,
                    _cohort_row(
                        f"3000000{index}",
                        f"Student{index}",
                        "Alex",
                        f"alex{index}",
                    ),
                ]
            if sheet_id == cohort_id and range_ == "Gradebook!A:ZZ":
                return _gradebook_rows([
                    f"Student{index}",
                    "Alex",
                    "91%",
                ])
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 5
    assert message.answer_document.await_count == 0
    texts = [call.args[0] for call in message.answer.await_args_list]
    assert texts[0].startswith("🔄 Sync started")
    assert [text.splitlines()[0] for text in texts[1:4]] == [
        "✅ 2023 processed",
        "✅ 2024 processed",
        "✅ 2025 processed",
    ]
    assert texts[4].startswith("✅ Sync completed")
    assert "2023 — 1 roster student; 1 Gradebook row matched" in texts[4]
    assert "2024 — 1 roster student; 1 Gradebook row matched" in texts[4]
    assert "2025 — 1 roster student; 1 Gradebook row matched" in texts[4]
    assert "Rights: 1 staff record" in texts[4]
    progress.edit_text.assert_awaited_with(
        "🔄 Sync started. Processing 3 cohorts…"
    )


async def test_sync_denied_for_non_admin(session, monkeypatch):
    called = []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows",
                        lambda *a, **k: called.append(1) or [])
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="S", role=Role.STUDENT), session=session)
    msg.answer.assert_awaited_with("Admins only.")
    assert called == []  # no fetch, no writes


async def test_sync_aborts_on_credential_error_without_raising(session, monkeypatch):
    def raise_credential_error(*a):
        raise ValueError(
            "No Google service-account credentials configured: set either "
            "GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE."
        )
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        raise_credential_error)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    msg.answer.assert_awaited_once()
    assert msg.answer.await_args.args[0].startswith("Sync aborted (credentials):")


async def test_sync_aborts_and_writes_nothing_on_cohort_parse_error(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA"),
                    _cohorts_row("2099", "BBB")]
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
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
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
    assert "2024 — 1 roster student; 1 Gradebook row matched" in (
        msg.answer.await_args.args[0]
    )


async def test_sync_stores_the_cub_email(session, monkeypatch):
    # The sheets have named a `cubemail` column since before the field existed,
    # so /sync read it and dropped it on the floor for want of a User column.
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA":
            return [COHORT_HEADER,
                    ["30000001", "Ivanov", "Ivan", "ivan", "ivan@gmail.com",
                     "iivanov@constructor.university", "", "", "", "", ""]]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER,
                    ["30000001", "Ivanov", "Ivan", "Student", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    u = session.query(User).filter_by(matriculation="30000001").one()
    assert u.cubemail == "iivanov@constructor.university"
    assert u.gmail == "ivan@gmail.com"


async def test_sync_creates_searchable_admin_only_in_rights(session, monkeypatch):
    # An admin who appears only in the Rights tab (no cohort, no matriculation)
    # must still become a real, searchable User row, keyed by Telegram handle.
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
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

    from jbcub_bot.features.directory.search import rank_users
    results = [user for _, user in rank_users(session, "sidorov")]
    assert len(results) == 1
    assert results[0].role is Role.ADMIN
    assert results[0].full_name == "Sergey Sidorov"


async def test_sync_labels_the_phase_and_reraises_an_unexpected_sheet_error(
        session, monkeypatch):
    # A Google API / network failure is not something an admin can fix by editing
    # a sheet, so it must reach the error reporter with the phase attached rather
    # than be swallowed into a bare "aborted".
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        raise ConnectionResetError("connection reset by peer")
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    with pytest.raises(RuntimeError) as err:
        await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN),
                       session=session)
    assert "cohort 2024" in str(err.value)
    assert isinstance(err.value.__cause__, ConnectionResetError)
    assert session.query(User).count() == 0  # write phase never reached


async def test_sync_times_out_instead_of_freezing_on_a_stalled_sheet_read(
        session, monkeypatch):
    # fetch_rows is blocking: awaited inline, a stalled Google read freezes every
    # other update. The deadline turns a hang into a reportable error.
    def stalling_fetch(sheet_id, sa, range_="A:Z"):
        time.sleep(0.5)
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows",
                        stalling_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    monkeypatch.setattr(handlers, "SHEET_READ_TIMEOUT", 0.01)
    msg = SimpleNamespace(answer=AsyncMock())
    with pytest.raises(RuntimeError) as err:
        await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN),
                       session=session)
    assert isinstance(err.value.__cause__, TimeoutError)


async def test_sync_reports_the_rows_it_ignored_below_the_roster(session, monkeypatch):
    # Dropping the tail of expelled students is the point, but dropping it
    # silently reads as "all 61 imported". The count has to be said out loud.
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA":
            return [COHORT_HEADER,
                    _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
                    [],  # the break
                    _cohort_row("30000009", "Expelled", "Eve", "eve"),
                    _cohort_row("30000010", "Moved", "Max", "max")]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    said = [c.args[0] for c in msg.answer.await_args_list]
    report = next(m for m in said if m.startswith("⚠️ 2024 processed"))
    assert "Roster: 1 student" in report
    assert "3 historical rows below the roster separator were ignored" in report
    assert session.query(User).filter_by(matriculation="30000009").count() == 0


async def test_sync_marks_and_reports_the_students_the_roster_dropped(session, monkeypatch):
    # The rows below the roster are the expelled/transferred ones. Earlier syncs
    # imported them, so they linger with frozen data unless the sync says so.
    session.add(User(matriculation="30000009", last_name="Expelled",
                     first_name="Eve", primary_cohort="2024"))
    session.commit()

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    gone = session.query(User).filter_by(matriculation="30000009").one()
    assert gone.departed_at == date.today().isoformat()
    said = [c.args[0] for c in msg.answer.await_args_list]
    report = next(m for m in said if m.startswith("⚠️ 2024 processed"))
    assert "Newly marked as departed (1)" in report
    assert "Eve Expelled (30000009)" in report


async def test_sync_reports_nothing_marked_when_the_roster_is_unchanged(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    said = [c.args[0] for c in msg.answer.await_args_list]
    assert not any("Newly marked as departed" in m for m in said)
    assert session.query(User).filter_by(matriculation="30000001").one().departed_at \
        is None


async def test_sync_aborts_rather_than_mark_a_whole_cohort_departed(session, monkeypatch):
    # A blank first data row or a mis-set Cohorts link yields zero roster
    # records, which would read as "everyone left" and hide the cohort from
    # itself. Abort in the parse phase, before anything is written.
    session.add(User(matriculation="30000001", last_name="Ivanov",
                     first_name="Ivan", primary_cohort="2024"))
    session.commit()

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA":
            return [COHORT_HEADER, []]  # a blank row where the roster should start
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    last = msg.answer.await_args.args[0]
    assert last.startswith("Sync aborted (cohort 2024):")
    assert session.query(User).filter_by(matriculation="30000001").one().departed_at \
        is None


async def test_sync_aborts_on_a_misspelled_field_column_in_the_cohorts_tab(
        session, monkeypatch):
    # The mapping now lives in a hand-edited header, so a typo there is the
    # likeliest mistake -- and must be named, not silently dropped.
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [["Cohort", "Link", "matriculation", "last_nmae"],
                    ["2024", "AAA", "Matriculation Num.", "Last name"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    last = msg.answer.await_args.args[0]
    assert last.startswith("Sync aborted (Cohorts tab):")
    assert "last_nmae" in last
    assert session.query(User).count() == 0


async def test_sync_aborts_when_rights_has_no_handle_column(session, monkeypatch):
    # Rights rows are keyed on handle_sheet: without that column every row is
    # skipped and the sync would claim success having written nothing.
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if range_ == "Rights!A:Z":
            return [["last_name", "first_name", "role"], ["Ivanov", "Ivan", "Admin"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)
    last = msg.answer.await_args.args[0]
    assert last.startswith("Sync aborted (Rights tab):")
    assert "handle_sheet" in last
    assert session.query(User).count() == 0


async def test_sync_aborts_and_writes_nothing_on_invalid_role(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
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


async def test_broken_gradebook_does_not_rollback_roster(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            raise ConnectionResetError("boom")
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(
        message, principal=User(last_name="A", role=Role.ADMIN), session=session
    )
    user = session.query(User).filter_by(matriculation="30000001").one()
    assert user.primary_cohort == "2024"
    said = [call.args[0] for call in message.answer.await_args_list]
    report = next(text for text in said if text.startswith("⚠️ 2024 processed"))
    assert "Gradebook: not updated; previous data kept" in report
    assert "Gradebook was not updated (1)" in report
    assert "boom" in report
    assert said[-1].startswith("⚠️ Sync completed with warnings")
    assert "2024 — 1 roster student; grades not updated, previous data kept" in said[-1]


async def test_gradebook_failure_does_not_stop_next_cohort(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [
                COHORTS_HEADER,
                _cohorts_row("2024", "AAA"),
                _cohorts_row("2025", "BBB"),
            ]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return [["no", "header", "here"]]
        if sheet_id == "BBB" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000002", "Petrov", "Petr", "petr")]
        if sheet_id == "BBB" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Petrov", "Petr", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(
        message, principal=User(last_name="A", role=Role.ADMIN), session=session
    )
    petrov = session.query(User).filter_by(matriculation="30000002").one()
    assert session.query(Grade).filter_by(user_id=petrov.id).count() == 1
    said = [call.args[0] for call in message.answer.await_args_list]
    first_report = next(text for text in said if text.startswith("⚠️ 2024 processed"))
    assert "Gradebook: not updated; previous data kept" in first_report
    assert "Gradebook header row not found" in first_report
    assert any(text.startswith("✅ 2025 processed") for text in said)
    assert "2025 — 1 roster student; 1 Gradebook row matched" in said[-1]


async def test_sync_stores_cohort_and_rights_source_links(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["", "Sidorov", "Sergey", "Admin", "sidorov"]]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_sync(
        message, principal=User(last_name="A", role=Role.ADMIN), session=session
    )
    assert session.query(User).filter_by(last_name="Ivanov").one().source_link == "AAA"
    assert session.query(User).filter_by(last_name="Sidorov").one().source_link == "RIGHTS"
