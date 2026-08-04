"""Where an operational report goes, and what a "nothing matched" entry says."""
from types import SimpleNamespace

from jbcub_bot.core.models import Role, User
from jbcub_bot.core.oplog import (
    MISS_LIMIT,
    OpsLog,
    format_kb_question,
    format_miss,
)


class FakeBot:
    """Records send_message calls; `failing` chat ids raise instead."""

    def __init__(self, failing=()):
        self.sent: list = []
        self.failing = set(failing)

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.failing:
            raise RuntimeError("chat not found")
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text))
        return None


def _student(**kwargs):
    fields = dict(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  handle_observed="ivan_i", telegram_id=777)
    fields.update(kwargs)
    return User(**fields)


# --- where it goes ------------------------------------------------------------

async def test_a_configured_chat_gets_it_and_the_admins_do_not():
    bot = FakeBot()
    await OpsLog(bot, "-1001234", {111, 222}).send("hello")
    assert [m.chat_id for m in bot.sent] == ["-1001234"]


async def test_without_a_chat_every_admin_still_gets_a_dm():
    bot = FakeBot()
    await OpsLog(bot, "", {111, 222}).send("hello")
    assert {m.chat_id for m in bot.sent} == {111, 222}


async def test_a_chat_the_bot_cannot_post_to_falls_back_to_the_admins():
    # The bot was removed from the log chat. The report must not vanish with it.
    bot = FakeBot(failing={"-1001234"})
    await OpsLog(bot, "-1001234", {111}).send("hello")
    assert [m.chat_id for m in bot.sent] == [111]


async def test_nothing_configured_is_silent_but_does_not_raise():
    bot = FakeBot()
    await OpsLog(bot, "", set()).send("hello")
    assert bot.sent == []


async def test_a_blocked_admin_does_not_stop_the_others():
    bot = FakeBot(failing={111})
    await OpsLog(bot, "", [111, 222]).send("hello")
    assert [m.chat_id for m in bot.sent] == [222]


# --- what it says -------------------------------------------------------------

def test_a_miss_names_the_person_the_query_and_the_answer():
    text = format_miss(query="Иванов Пётр", answer="No one found.",
                       principal=_student(),
                       tg_user=SimpleNamespace(id=777, username="ivan_i"))
    assert "Ivan Ivanov" in text
    assert "@ivan_i" in text
    assert "777" in text
    assert "Student" in text
    assert "Иванов Пётр" in text
    assert "No one found." in text


def test_a_miss_from_someone_with_no_row_still_identifies_them():
    text = format_miss(query="hi", answer="No one found.", principal=None,
                       tg_user=SimpleNamespace(id=777, username=None))
    assert "777" in text


def test_an_impersonated_miss_names_the_admin_and_the_target():
    # `principal` is the target while /as is on, so the real human is the
    # impersonator -- crediting the query to the student would be a lie.
    text = format_miss(
        query="hi", answer="No one found.",
        principal=_student(),
        tg_user=SimpleNamespace(id=999, username="admin_a"),
        impersonator=_student(first_name="Ann", last_name="Adm",
                              role=Role.ADMIN, handle_observed="admin_a"),
    )
    assert "Ann Adm" in text
    assert "as: Ivan Ivanov" in text


def test_a_pasted_wall_of_text_is_clipped():
    text = format_miss(query="x" * 5000, answer="No one found.")
    assert len(text) < MISS_LIMIT + 300
    assert "…" in text


def test_a_knowledge_base_entry_names_the_asker_and_quotes_the_question():
    text = format_kb_question("how many retakes?", principal=_student(),
                              tg_user=SimpleNamespace(id=777,
                                                      username="ivan_i"))
    assert "Ivan Ivanov" in text
    assert "@ivan_i" in text
    assert "«how many retakes?»" in text


def test_a_pasted_wall_of_a_question_is_clipped_too():
    text = format_kb_question("x" * 5000)
    assert len(text) < MISS_LIMIT + 300
    assert "…" in text
