"""A crashing handler must never be silent — it reports to the admins."""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.errors import (
    TELEGRAM_LIMIT,
    format_traceback,
    report_exception,
    summarize,
)
from jbcub_bot.main import build_dispatcher


class FakeBot:
    """Records both channels: `answer` goes through __call__, DMs through send_message."""

    def __init__(self):
        self.id = 1
        self.sent: list = []
        self.dms: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None

    async def send_message(self, chat_id, text, **kwargs):
        self.dms.append(SimpleNamespace(chat_id=chat_id, text=text))
        return None


def _caught(fn):
    """The exception `fn` raises, with a real traceback attached."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - that's the point
        return exc
    raise AssertionError("fn did not raise")


# --- format_traceback ---------------------------------------------------------

def test_format_traceback_keeps_the_type_and_the_frames():
    text = format_traceback(_caught(lambda: 1 / 0))
    assert "Traceback (most recent call last)" in text
    assert "ZeroDivisionError: division by zero" in text


def test_format_traceback_cuts_the_middle_and_keeps_both_ends():
    exc = _caught(lambda: (_ for _ in ()).throw(ValueError("x" * 9000)))
    text = format_traceback(exc)
    assert len(text) <= TELEGRAM_LIMIT
    assert text.startswith("Traceback (most recent call last)")  # the cause
    assert "…(middle cut)…" in text
    assert text.rstrip().endswith("x" * 20)  # the exception that reached us


def _chained():
    """What /sync produces: a phase label wrapping the real network error."""
    def wrap():
        try:
            raise ConnectionResetError("connection reset by peer")
        except ConnectionResetError as exc:
            raise RuntimeError("/sync failed reading the Cohorts tab") from exc
    return _caught(wrap)


def test_summarize_names_the_cause_before_the_wrapper():
    assert summarize(_chained()) == (
        "ConnectionResetError: connection reset by peer\n"
        "↳ raised: RuntimeError: /sync failed reading the Cohorts tab"
    )


async def test_report_never_clips_away_the_cause_of_a_deep_chain():
    # The summary is what survives no matter how deep the frames go — clipping
    # the traceback must not cost us the name of the actual failure.
    bot = FakeBot()
    await report_exception(bot, {1}, _chained(), context="x" * 500)
    text = bot.dms[0].text
    assert len(text) <= 4096  # Telegram's hard cap
    assert "ConnectionResetError: connection reset by peer" in text
    assert "RuntimeError: /sync failed reading the Cohorts tab" in text


# --- report_exception --------------------------------------------------------

async def test_report_dms_the_full_traceback_to_every_bootstrap_admin():
    bot = FakeBot()
    await report_exception(bot, {111, 222}, _caught(lambda: 1 / 0),
                           context="/sync while reading cohort sdt")
    assert {dm.chat_id for dm in bot.dms} == {111, 222}
    assert "/sync while reading cohort sdt" in bot.dms[0].text
    assert "ZeroDivisionError: division by zero" in bot.dms[0].text
    assert "Traceback (most recent call last)" in bot.dms[0].text


async def test_report_keeps_going_when_one_admin_never_opened_a_chat():
    bot = FakeBot()
    failures = []

    async def send_message(chat_id, text, **kwargs):
        if chat_id == 111:
            raise RuntimeError("chat not found")
        failures.append(chat_id)

    bot.send_message = send_message
    await report_exception(bot, [111, 222], _caught(lambda: 1 / 0), context="ctx")
    assert failures == [222]  # the reachable admin still got it


async def test_report_logs_even_with_no_admins_configured(caplog):
    with caplog.at_level(logging.ERROR):
        await report_exception(FakeBot(), set(), _caught(lambda: 1 / 0), context="ctx")
    assert "ZeroDivisionError" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text


# --- wiring: a crash inside a real handler, through a real dispatcher ---------

COHORT_SETTINGS = SimpleNamespace(
    google_service_account_file="sa.json", google_service_account_json="",
    rights_sheet_id="RIGHTS", cohorts_tab="Cohorts", rights_tab="Rights",
)


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _message(bot, tid, text):
    return Message(message_id=1, date=datetime.now(timezone.utc),
                   chat=Chat(id=tid, type="private"),
                   from_user=TgUser(id=tid, is_bot=False, first_name="t"),
                   text=text).as_(bot)


def _update(bot, tid, text):
    return Update(update_id=7, message=_message(bot, tid, text)).as_(bot)


def _callback_update(bot, tid, data):
    cb = CallbackQuery(id="1", chat_instance="1", data=data,
                       from_user=TgUser(id=tid, is_bot=False, first_name="t"),
                       message=_message(bot, tid, "profile")).as_(bot)
    return Update(update_id=8, callback_query=cb).as_(bot)


async def test_google_api_error_in_sync_reaches_the_admin_instead_of_hanging(monkeypatch):
    # The production symptom: a Google/network error matched no except clause in
    # /sync, so it escaped the handler and the admin was left with no reply.
    def boom(sheet_id, sa, range_="A:Z"):
        raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", boom)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings",
                        lambda: COHORT_SETTINGS)

    dp = build_dispatcher(_factory(), bootstrap_ids={777})
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, 777, "/sync"), dispatcher=dp)

    assert len(bot.dms) == 1, "the bootstrap admin got no crash report"
    assert "ConnectionResetError: connection reset by peer" in bot.dms[0].text
    assert "Traceback (most recent call last)" in bot.dms[0].text
    assert "Cohorts tab" in bot.dms[0].text  # which phase died
    # ...and the admin who typed /sync is not left staring at silence.
    replies = "\n".join(m.text for m in bot.sent)
    assert "ConnectionResetError: connection reset by peer" in replies
    assert "Cohorts tab" in replies


async def test_crashing_callback_handler_stops_the_spinner_and_reports(monkeypatch):
    # An unanswered callback query leaves the button spinning in the client, so
    # the safety net has to answer the callback, not just report the crash.
    def boom():
        raise RuntimeError("settings blew up")

    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", boom)
    factory = _factory()
    admin = factory()
    admin.add(User(last_name="A", first_name="Ann", telegram_id=777, role=Role.ADMIN))
    # An unlinked target, so cb_issue_link gets past its own checks and reaches
    # the exploding get_settings — the crash this test is about.
    admin.add(User(last_name="Ivan", matriculation="30000001", role=Role.STUDENT))
    admin.commit()
    admin.close()

    dp = build_dispatcher(factory, bootstrap_ids={555})
    bot = FakeBot()
    await dp.feed_update(bot, _callback_update(bot, 777, "dir:link:30000001"),
                         dispatcher=dp)

    assert [dm.chat_id for dm in bot.dms] == [555]
    assert "RuntimeError: settings blew up" in bot.dms[0].text
    assert any(type(m).__name__ == "AnswerCallbackQuery" for m in bot.sent), \
        "the callback was never answered — the button keeps spinning"
