import logging
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jbcub_bot.features.directory import handlers, sync_diagnostics
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


def _seed_previous_grade(session, user: User, value: str) -> int:
    session.add(user)
    session.flush()
    grade = Grade(
        user_id=user.id,
        cohort="2024",
        term="Fall 2023",
        category="Mandatory",
        label="Legacy course",
        value=value,
        position=2,
    )
    session.add(grade)
    session.commit()
    return grade.id


NO_COMMIT_FAILURE = "❌ Sync failed before any roster changes were committed."
PARTIAL_COMPLETION_NOTE = (
    "The processed cohorts above remain updated; "
    "the remaining sources were not completed."
)
COMMITTED_REPORT_FAILURE_NOTICE = (
    "⚠️ Sync changes were committed, but the completed sync report "
    "could not be sent."
)


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
    assert (
        "2023 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in texts[4]
    assert (
        "2024 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in texts[4]
    assert (
        "2025 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in texts[4]
    assert "Rights: 1 staff record" in texts[4]
    progress.edit_text.assert_awaited_with(
        "🔄 Sync started. Processing 3 cohorts…"
    )


async def test_cohort_problems_share_one_grouped_message_and_source_button(
    session,
    monkeypatch,
):
    unknown = [
        [f"Unknown{index}", "Student", "50%"]
        for index in range(10)
    ]

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Known", "Student", "known"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(*unknown)
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    cohort_call = message.answer.await_args_list[1]
    text = cohort_call.args[0]
    assert text.count("Gradebook rows without a roster match (10)") == 1
    assert text.count("These Gradebook rows were not imported.") == 1
    for index in range(10):
        assert f"Unknown{index} Student" in text
    button = cohort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open 2024 spreadsheet"
    assert button.url == "https://docs.google.com/spreadsheets/d/AAA"


async def test_current_roster_row_without_matriculation_prevents_false_success(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("", "Awaiting", "Number", "awaiting"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Awaiting", "Number", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    texts = [call.args[0] for call in message.answer.await_args_list]
    cohort_report = next(text for text in texts if text.endswith(
        "Awaiting Number — missing matriculation number"
    ))
    assert cohort_report.startswith("⚠️ 2024 processed")
    assert "Current roster rows cannot receive grades (1)" in cohort_report
    assert "Gradebook rows without a roster match" not in cohort_report
    assert texts[-1].startswith("⚠️ Sync completed with warnings")


async def test_rights_problems_share_the_final_message_and_source_button(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Known", "Student", "known"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Known", "Student", "91%"])
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["", "Boss", "Alice", "Admin", "boss"],
                ["", "Boss", "Alice", "Admin", "boss"],
            ]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    final_call = message.answer.await_args_list[-1]
    assert final_call.args[0].count("Duplicate Rights handles (1)") == 1
    button = final_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open Rights spreadsheet"
    assert button.url == "https://docs.google.com/spreadsheets/d/RIGHTS"


async def test_oversized_final_report_is_one_complete_document_with_rights_button(
    session,
    monkeypatch,
):
    monkeypatch.setattr(sync_diagnostics, "MAX_REPORT_TEXT", 300)
    handles = [f"staff{index:02d}" for index in range(12)]

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Known", "Student", "known"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Known", "Student", "91%"])
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                *[
                    ["", f"Staff{index}", "Alex", "Admin", handle]
                    for index, handle in enumerate(handles)
                    for _ in range(2)
                ],
            ]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 2
    assert message.answer_document.await_count == 1
    final_call = message.answer_document.await_args
    document = final_call.args[0]
    complete_text = document.data.decode("utf-8")
    assert document.filename == "sync-final.txt"
    assert complete_text.startswith("⚠️ Sync completed with warnings")
    assert all(f"• {handle} — 2 rows" in complete_text for handle in handles)
    assert final_call.kwargs["caption"].startswith(
        "⚠️ Sync completed with warnings"
    )
    assert len(final_call.kwargs["caption"]) < 1024
    button = final_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open Rights spreadsheet"
    assert button.url == "https://docs.google.com/spreadsheets/d/RIGHTS"


async def test_oversized_cohort_report_is_one_document_message(
    session,
    monkeypatch,
):
    monkeypatch.setattr(sync_diagnostics, "MAX_REPORT_TEXT", 300)

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Known", "Student", "known"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(*[
                [f"Unknown{index}", "Student", "50%"]
                for index in range(20)
            ])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    assert message.answer_document.await_count == 1
    document_call = message.answer_document.await_args
    assert document_call.kwargs["caption"].startswith("⚠️ 2024 processed")
    assert document_call.kwargs["reply_markup"] is not None
    assert message.answer.await_count == 2


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
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    message.answer.assert_awaited_once_with(
        "❌ Sync aborted while reading Google service-account credentials.\n\n"
        "No roster changes were made.\n\n"
        "No Google service-account credentials configured: set either "
        "GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE.\n\n"
        "Fix: Configure valid Google service-account credentials."
    )
    progress.edit_text.assert_not_awaited()


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
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 1
    abort_call = progress.edit_text.await_args
    assert abort_call.args[0].startswith(
        "❌ Sync aborted while reading cohort 2099.\n\n"
        "No roster changes were made.\n\n"
    )
    assert "column 'Matriculation Num.'" in abort_call.args[0]
    assert abort_call.args[0].endswith(
        "Fix: Correct the cohort Link, headers, or first roster row."
    )
    button = abort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open cohort 2099"
    assert button.url == "https://docs.google.com/spreadsheets/d/BBB"
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
    assert (
        "2024 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in (
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
    message, progress = _sync_message()
    with pytest.raises(RuntimeError) as err:
        await cmd_sync(message, principal=User(last_name="A", role=Role.ADMIN),
                       session=session)
    assert str(err.value) == "/sync failed reading cohort 2024 (sheet AAA)"
    assert isinstance(err.value.__cause__, ConnectionResetError)
    progress.edit_text.assert_awaited_with(NO_COMMIT_FAILURE)
    assert message.answer.await_count == 1
    assert "Traceback" not in progress.edit_text.await_args.args[0]
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
    message, progress = _sync_message()
    with pytest.raises(RuntimeError) as err:
        await cmd_sync(message, principal=User(last_name="A", role=Role.ADMIN),
                       session=session)
    assert str(err.value) == "/sync failed reading the Cohorts tab"
    assert isinstance(err.value.__cause__, TimeoutError)
    progress.edit_text.assert_awaited_once_with(NO_COMMIT_FAILURE)
    assert message.answer.await_count == 1


async def test_no_commit_edit_failure_keeps_the_original_read_error(
    session,
    monkeypatch,
    caplog,
):
    read_error = ConnectionResetError("cohort index read failed")

    def fail_read(sheet_id, sa, range_="A:Z"):
        raise read_error

    monkeypatch.setattr(handlers, "fetch_rows", fail_read)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()
    progress.edit_text.side_effect = OSError("failure edit failed")

    with caplog.at_level(logging.ERROR, logger=handlers.__name__):
        with pytest.raises(RuntimeError) as err:
            await cmd_sync(
                message,
                principal=User(last_name="A", role=Role.ADMIN),
                session=session,
            )

    assert str(err.value) == "/sync failed reading the Cohorts tab"
    assert err.value.__cause__ is read_error
    assert "failure edit failed" in caplog.text
    progress.edit_text.assert_awaited_once_with(NO_COMMIT_FAILURE)
    assert message.answer.await_count == 1


async def test_sync_labels_and_reraises_an_unexpected_rights_read_error(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if range_ == "Rights!A:Z":
            raise ConnectionResetError("rights read failed")
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed reading the Rights tab"
    assert isinstance(err.value.__cause__, ConnectionResetError)
    assert str(err.value.__cause__) == "rights read failed"
    progress.edit_text.assert_awaited_with(NO_COMMIT_FAILURE)
    assert message.answer.await_count == 1
    assert session.query(User).count() == 0


async def test_sync_labels_a_first_cohort_write_failure_without_claiming_changes(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    def fail_roster_write(session_arg, records, key="matriculation"):
        raise OSError("database unavailable")

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    monkeypatch.setattr(handlers.sheets, "upsert_users", fail_roster_write)
    message, progress = _sync_message()

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed writing cohort 2024"
    assert isinstance(err.value.__cause__, OSError)
    assert str(err.value.__cause__) == "database unavailable"
    progress.edit_text.assert_awaited_with(NO_COMMIT_FAILURE)
    assert message.answer.await_count == 1
    assert session.query(User).count() == 0


async def test_committed_cohort_send_failure_reports_partial_state_then_reraises(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()
    send_error = ConnectionResetError("cohort report send failed")
    message.answer.side_effect = [progress, send_error, None]

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed reporting cohort 2024"
    assert err.value.__cause__ is send_error
    user = session.query(User).filter_by(matriculation="30000001").one()
    assert user.primary_cohort == "2024"
    assert session.query(Grade).filter_by(user_id=user.id, cohort="2024").count() == 1
    assert message.answer.await_count == 3
    partial_text = message.answer.await_args_list[-1].args[0]
    assert partial_text.splitlines()[0] == "⚠️ Sync partially completed"
    assert (
        "2024 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in partial_text
    assert "Rights: not updated; previous data kept" in partial_text
    assert partial_text.endswith(PARTIAL_COMPLETION_NOTE)


async def test_completed_sync_text_report_failure_sends_notice_then_reraises(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["", "Boss", "Alice", "Admin", "boss"],
            ]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()
    send_error = ConnectionResetError("completed report send failed")
    message.answer.side_effect = [progress, None, send_error, None]

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed reporting the completed sync"
    assert err.value.__cause__ is send_error
    assert message.answer.await_count == 4
    assert message.answer.await_args_list[-1].args == (
        COMMITTED_REPORT_FAILURE_NOTICE,
    )
    assert message.answer_document.await_count == 0
    student = session.query(User).filter_by(matriculation="30000001").one()
    assert student.primary_cohort == "2024"
    assert session.query(Grade).filter_by(user_id=student.id).count() == 1
    boss = session.query(User).filter_by(handle_sheet="boss").one()
    assert boss.role is Role.ADMIN


async def test_completed_sync_document_and_notice_failures_keep_primary_error(
    session,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(sync_diagnostics, "MAX_REPORT_TEXT", 300)
    handles = [f"staff{index:02d}" for index in range(12)]

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                *[
                    ["", f"Staff{index}", "Alex", "Admin", handle]
                    for index, handle in enumerate(handles)
                    for _ in range(2)
                ],
            ]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()
    send_error = ConnectionResetError("completed document send failed")
    notice_error = OSError("committed-state notice failed")
    message.answer.side_effect = [progress, None, notice_error]
    message.answer_document.side_effect = send_error

    with caplog.at_level(logging.ERROR, logger=handlers.__name__):
        with pytest.raises(RuntimeError) as err:
            await cmd_sync(
                message,
                principal=User(last_name="A", role=Role.ADMIN),
                session=session,
            )

    assert str(err.value) == "/sync failed reporting the completed sync"
    assert err.value.__cause__ is send_error
    assert message.answer_document.await_count == 1
    assert message.answer.await_count == 3
    assert message.answer.await_args_list[-1].args == (
        COMMITTED_REPORT_FAILURE_NOTICE,
    )
    assert "committed-state notice failed" in caplog.text
    student = session.query(User).filter_by(matriculation="30000001").one()
    assert student.primary_cohort == "2024"
    assert session.query(Grade).filter_by(user_id=student.id).count() == 1
    assert session.query(User).filter_by(handle_sheet=handles[-1]).count() == 1


async def test_later_cohort_write_failure_sends_partial_warning_then_reraises(
    session,
    monkeypatch,
):
    original_upsert = handlers.sheets.upsert_users

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [
                COHORTS_HEADER,
                _cohorts_row("2024", "AAA"),
                _cohorts_row("2025", "BBB"),
            ]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if sheet_id == "BBB" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000002", "Petrov", "Petr", "petr"),
            ]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    def fail_second_cohort(session_arg, records, key="matriculation"):
        if records and records[0].get("primary_cohort") == "2025":
            raise OSError("second cohort write failed")
        return original_upsert(session_arg, records, key=key)

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    monkeypatch.setattr(handlers.sheets, "upsert_users", fail_second_cohort)
    message, progress = _sync_message()

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed writing cohort 2025"
    assert isinstance(err.value.__cause__, OSError)
    assert str(err.value.__cause__) == "second cohort write failed"
    assert message.answer.await_count == 3
    assert message.answer_document.await_count == 0
    assert message.answer.await_args_list[1].args[0].startswith("✅ 2024 processed")
    final_text = message.answer.await_args_list[2].args[0]
    assert final_text.splitlines()[0] == "⚠️ Sync partially completed"
    assert (
        "2024 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in final_text
    assert final_text.endswith(PARTIAL_COMPLETION_NOTE)
    assert "Traceback" not in final_text
    assert all(
        call.args[0] != NO_COMMIT_FAILURE
        for call in progress.edit_text.await_args_list
    )
    assert session.query(User).filter_by(matriculation="30000001").count() == 1
    assert session.query(User).filter_by(matriculation="30000002").count() == 0


async def test_partial_warning_send_failure_keeps_the_original_write_error(
    session,
    monkeypatch,
    caplog,
):
    original_upsert = handlers.sheets.upsert_users

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [
                COHORTS_HEADER,
                _cohorts_row("2024", "AAA"),
                _cohorts_row("2025", "BBB"),
            ]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if sheet_id == "BBB" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000002", "Petrov", "Petr", "petr"),
            ]
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    write_error = OSError("second cohort write failed")

    def fail_second_cohort(session_arg, records, key="matriculation"):
        if records and records[0].get("primary_cohort") == "2025":
            raise write_error
        return original_upsert(session_arg, records, key=key)

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    monkeypatch.setattr(handlers.sheets, "upsert_users", fail_second_cohort)
    message, progress = _sync_message()
    message.answer.side_effect = [
        progress,
        None,
        ConnectionResetError("warning summary send failed"),
    ]

    with caplog.at_level(logging.ERROR, logger=handlers.__name__):
        with pytest.raises(RuntimeError) as err:
            await cmd_sync(
                message,
                principal=User(last_name="A", role=Role.ADMIN),
                session=session,
            )

    assert str(err.value) == "/sync failed writing cohort 2025"
    assert err.value.__cause__ is write_error
    assert "warning summary send failed" in caplog.text
    assert message.answer.await_count == 3
    assert session.query(User).filter_by(matriculation="30000001").count() == 1
    assert session.query(User).filter_by(matriculation="30000002").count() == 0


async def test_rights_write_failure_sends_partial_warning_then_reraises(
    session,
    monkeypatch,
):
    original_upsert = handlers.sheets.upsert_users

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["", "Boss", "Alice", "Admin", "boss"],
            ]
        return []

    def fail_rights_write(session_arg, records, key="matriculation"):
        if key == "handle_sheet":
            raise OSError("rights write failed")
        return original_upsert(session_arg, records, key=key)

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    monkeypatch.setattr(handlers.sheets, "upsert_users", fail_rights_write)
    message, _ = _sync_message()

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed in the write phase"
    assert isinstance(err.value.__cause__, OSError)
    assert str(err.value.__cause__) == "rights write failed"
    assert message.answer.await_count == 3
    assert message.answer_document.await_count == 0
    final_text = message.answer.await_args_list[-1].args[0]
    assert final_text.splitlines()[0] == "⚠️ Sync partially completed"
    assert "Rights: not updated; previous data kept" in final_text
    assert "Rights: 1 staff record" not in final_text
    assert final_text.endswith(PARTIAL_COMPLETION_NOTE)
    assert "Traceback" not in final_text
    assert session.query(User).filter_by(matriculation="30000001").count() == 1
    assert session.query(User).filter_by(handle_sheet="boss").count() == 0


async def test_rights_write_failure_without_a_cohort_commit_edits_the_start(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER]
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["", "Boss", "Alice", "Admin", "boss"],
            ]
        return []

    def fail_rights_write(session_arg, records, key="matriculation"):
        raise OSError("rights write failed before a cohort commit")

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    monkeypatch.setattr(handlers.sheets, "upsert_users", fail_rights_write)
    message, progress = _sync_message()

    with pytest.raises(RuntimeError) as err:
        await cmd_sync(
            message,
            principal=User(last_name="A", role=Role.ADMIN),
            session=session,
        )

    assert str(err.value) == "/sync failed in the write phase"
    assert isinstance(err.value.__cause__, OSError)
    assert str(err.value.__cause__) == "rights write failed before a cohort commit"
    progress.edit_text.assert_awaited_with(NO_COMMIT_FAILURE)
    assert message.answer.await_count == 1
    assert session.query(User).count() == 0


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
    assert "2 historical rows below the roster separator were ignored" in report
    assert session.query(User).filter_by(matriculation="30000009").count() == 0


async def test_sync_without_a_roster_separator_reports_no_ignored_rows(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    cohort_report = message.answer.await_args_list[1].args[0]
    assert "historical row" not in cohort_report


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
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 1
    abort_call = progress.edit_text.await_args
    assert abort_call.args[0] == (
        "❌ Sync aborted while reading cohort 2024.\n\n"
        "No roster changes were made.\n\n"
        "The sheet yielded no roster rows, which would mark the whole cohort "
        "departed.\n\n"
        "Fix: Correct the cohort Link, headers, or first roster row."
    )
    button = abort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open cohort 2024"
    assert button.url == "https://docs.google.com/spreadsheets/d/AAA"
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
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 1
    abort_call = progress.edit_text.await_args
    assert abort_call.args[0].startswith(
        "❌ Sync aborted while reading Cohorts tab.\n\n"
        "No roster changes were made.\n\n"
    )
    assert "last_nmae" in abort_call.args[0]
    assert abort_call.args[0].endswith(
        "Fix: Correct the Cohorts tab headers or field mapping."
    )
    button = abort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open Cohorts tab"
    assert button.url == "https://docs.google.com/spreadsheets/d/RIGHTS"
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
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 1
    abort_call = progress.edit_text.await_args
    assert abort_call.args[0].startswith(
        "❌ Sync aborted while reading Rights tab.\n\n"
        "No roster changes were made.\n\n"
    )
    assert "handle_sheet" in abort_call.args[0]
    assert abort_call.args[0].endswith(
        "Fix: Correct the Rights tab headers and role values."
    )
    button = abort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open Rights tab"
    assert button.url == "https://docs.google.com/spreadsheets/d/RIGHTS"
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
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 1
    abort_call = progress.edit_text.await_args
    assert abort_call.args[0] == (
        "❌ Sync aborted while reading Rights tab.\n\n"
        "No roster changes were made.\n\n"
        "Invalid role 'superuser'.\n\n"
        "Fix: Correct the Rights tab headers and role values."
    )
    button = abort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open Rights tab"
    assert button.url == "https://docs.google.com/spreadsheets/d/RIGHTS"
    assert session.query(User).count() == 0


async def test_broken_gradebook_does_not_rollback_roster(session, monkeypatch):
    previous_grade_id = _seed_previous_grade(
        session,
        User(
            matriculation="30000001",
            last_name="Ivanov",
            first_name="Ivan",
            primary_cohort="2024",
        ),
        "previous read-failure grade",
    )

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
    cohort_text = message.answer.await_args_list[1].args[0]
    final_text = message.answer.await_args_list[-1].args[0]
    assert "Gradebook: not updated; previous data kept" in cohort_text
    assert "Gradebook was not updated (1)" in cohort_text
    assert "ConnectionResetError" in cohort_text or "boom" in cohort_text
    assert final_text.startswith("⚠️ Sync completed with warnings")
    assert "grades not updated, previous data kept" in final_text
    retained = session.get(Grade, previous_grade_id)
    assert retained is not None
    assert retained.value == "previous read-failure grade"


async def test_gradebook_mutation_failure_rolls_back_grades_but_keeps_roster_commit(
    session,
    monkeypatch,
):
    previous_grade_id = _seed_previous_grade(
        session,
        User(
            matriculation="30000001",
            last_name="Legacy",
            first_name="Name",
            primary_cohort="2024",
        ),
        "previous committed grade",
    )

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Ivanov", "Ivan", "ivan"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["30000001", "Ivanov", "Ivan", "Admin", "ivan"],
            ]
        return []

    original_add = session.add
    observed_new_grade_values = []

    def fail_after_grade_delete(instance):
        if isinstance(instance, Grade):
            assert session.query(Grade).filter_by(cohort="2024").count() == 0
            observed_new_grade_values.append(instance.value)
            raise OSError("grade mutation failed after cohort delete")
        return original_add(instance)

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    monkeypatch.setattr(session, "add", fail_after_grade_delete)
    message, _ = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="A", role=Role.ADMIN),
        session=session,
    )

    assert observed_new_grade_values == ["91%"]
    retained = session.get(Grade, previous_grade_id)
    assert retained is not None
    assert retained.value == "previous committed grade"
    assert session.query(Grade).filter_by(cohort="2024").count() == 1
    user = session.query(User).filter_by(matriculation="30000001").one()
    assert user.last_name == "Ivanov"
    assert user.first_name == "Ivan"
    assert user.handle_sheet == "ivan"
    cohort_text = message.answer.await_args_list[1].args[0]
    assert "grade mutation failed after cohort delete" in cohort_text


async def test_empty_gradebook_error_keeps_warning_and_source_button(
    session,
    monkeypatch,
):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            raise TimeoutError()
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, _ = _sync_message()

    await cmd_sync(
        message, principal=User(last_name="A", role=Role.ADMIN), session=session
    )

    assert message.answer.await_count == 3
    assert message.answer_document.await_count == 0
    cohort_call = message.answer.await_args_list[1]
    assert cohort_call.args[0].startswith("⚠️ 2024 processed")
    assert "Gradebook was not updated (1)" in cohort_call.args[0]
    assert "TimeoutError" in cohort_call.args[0]
    button = cohort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open 2024 spreadsheet"
    assert button.url == "https://docs.google.com/spreadsheets/d/AAA"
    final = message.answer.await_args_list[2].args[0]
    assert final.startswith("⚠️ Sync completed with warnings")
    assert "2024 — 1 roster student; grades not updated, previous data kept" in final


async def test_gradebook_failure_does_not_stop_next_cohort(session, monkeypatch):
    previous_grade_id = _seed_previous_grade(
        session,
        User(
            matriculation="30000001",
            last_name="Ivanov",
            first_name="Ivan",
            primary_cohort="2024",
        ),
        "previous parse-failure grade",
    )

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
    assert (
        "2025 — 1 roster student; "
        "1 of 1 current roster student found in Gradebook"
    ) in said[-1]
    retained = session.get(Grade, previous_grade_id)
    assert retained is not None
    assert retained.value == "previous parse-failure grade"


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
