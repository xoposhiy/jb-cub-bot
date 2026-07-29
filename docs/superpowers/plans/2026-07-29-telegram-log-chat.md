# Telegram Log Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send every crash and every unanswered user request to one private staff chat, with who asked, what they typed, and what the bot replied.

**Architecture:** A new `core/oplog.py` owns the destination — an `OpsLog` that
sends to `LOG_CHAT_ID` and falls back to the bootstrap admins' DMs — plus the
formatter for a "nothing matched" entry. `core/errors.py` keeps traceback
formatting and hands delivery to `OpsLog`. `build_dispatcher` builds one per
update through a closure and calls it from three places.

**Tech Stack:** Python 3.12, aiogram 3, pydantic-settings, pytest (async mode
already configured — tests are plain `async def`, no decorator).

**Spec:** `docs/superpowers/specs/2026-07-29-telegram-log-chat-design.md`

## Global Constraints

- Entries are sent as **plain text, no `parse_mode`** — a query containing `_`
  or `<` must not break the message.
- `OpsLog.send` **never raises**: it runs on the failure path.
- A query is clipped to **500** characters (`oplog.MISS_LIMIT`).
- `LOG_CHAT_ID` is a **`str`**, default `""` — channel ids look like `-100…`,
  and empty means the feature is off.
- Run tests with `uv run pytest`.
- Not logged: an unknown command, an access refusal.

---

### Task 1: `core/oplog.py` — destination and miss formatting

**Files:**
- Create: `src/jbcub_bot/core/oplog.py`
- Modify: `src/jbcub_bot/core/config.py:22` (add the field after `gradebook_tab`)
- Modify: `.env.example` (append)
- Test: `tests/test_oplog.py` (create), `tests/test_config.py` (append one test)

**Interfaces:**
- Consumes: `jbcub_bot.core.models.User` (uses `.full_name`, `.role`).
- Produces:
  - `OpsLog(bot, chat_id: str = "", admin_ids: Iterable[int] | None = None)`
    with `async def send(self, text: str) -> None`
  - `format_miss(query: str, answer: str, principal=None, tg_user=None, impersonator=None) -> str`
  - `MISS_LIMIT = 500`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oplog.py`:

```python
"""Where an operational report goes, and what a "nothing matched" entry says."""
from types import SimpleNamespace

from jbcub_bot.core.models import Role, User
from jbcub_bot.core.oplog import MISS_LIMIT, OpsLog, format_miss


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
```

Append to `tests/test_config.py`:

```python
def test_log_chat_id_is_empty_by_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.delenv("LOG_CHAT_ID", raising=False)
    assert Settings(_env_file=None).log_chat_id == ""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_oplog.py tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jbcub_bot.core.oplog'`,
and `AttributeError: 'Settings' object has no attribute 'log_chat_id'`.

- [ ] **Step 3: Add the setting**

In `src/jbcub_bot/core/config.py`, after `gradebook_tab`:

```python
    # Chat that receives crash reports and unanswered requests. A channel id
    # looks like -100…, so this is a str; empty means report to the bootstrap
    # admins' DMs instead.
    log_chat_id: str = ""
```

Append to `.env.example`:

```
# Private staff chat for crash reports and unanswered requests. Add the bot to
# it first. Empty means those go to BOOTSTRAP_ADMIN_IDS in DM instead.
LOG_CHAT_ID=
```

- [ ] **Step 4: Write `core/oplog.py`**

```python
"""Where an operational report goes, and what an unanswered request looks like.

A crash or a dead end is invisible in Telegram: the person who typed it just
gets nothing useful. Both go to one private staff chat -- and if that chat is
unset, or the bot was removed from it, to the bootstrap admins' DMs, which work
even on an empty database.
"""
import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# A query is user text, and someone will paste an essay into the search box.
MISS_LIMIT = 500


def clip(text: str, limit: int = MISS_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


class OpsLog:
    """Delivers a report, and never lets the delivery become the failure."""

    def __init__(self, bot, chat_id: str = "",
                 admin_ids: Iterable[int] | None = None):
        self.bot = bot
        self.chat_id = str(chat_id or "").strip()
        self.admin_ids = sorted(admin_ids or ())

    async def send(self, text: str) -> None:
        if self.bot is None:  # a handler called directly, with no bot to send through
            return
        if self.chat_id and await self._try(self.chat_id, text):
            return
        for admin_id in self.admin_ids:
            await self._try(admin_id, text)

    async def _try(self, chat_id, text: str) -> bool:
        """True if it landed. Plain text: an entry quotes whatever a user typed."""
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
            return True
        except Exception:  # noqa: BLE001 - a bad destination must not hide the report
            logger.exception("Could not deliver an ops report to %s", chat_id)
            return False


def describe_sender(principal, tg_user) -> str:
    """Who asked, from both sides: the roster row and Telegram itself."""
    parts: list[str] = []
    if principal is not None:
        parts.append(principal.full_name or "(no name)")
    if tg_user is not None:
        if tg_user.username:
            parts.append(f"@{tg_user.username}")
        parts.append(str(tg_user.id))
    if principal is not None:
        parts.append(principal.role.value)
    return " · ".join(parts) or "unknown"


def format_miss(query: str, answer: str, principal=None, tg_user=None,
                impersonator=None) -> str:
    """One entry for a request the bot could not serve.

    While /as is on, `principal` is the target and the human who typed this is
    the impersonator, so the credit goes to them and the target gets its own
    line.
    """
    actor = impersonator if impersonator is not None else principal
    lines = ["🔍 Nothing matched", f"from: {describe_sender(actor, tg_user)}"]
    if impersonator is not None:
        target = principal.full_name if principal is not None else "(nobody)"
        lines.append(f"as: {target}")
    lines.append(f"query: «{clip(query)}»")
    lines.append(f"answer: «{clip(answer)}»")
    return "\n".join(lines)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_oplog.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/core/oplog.py src/jbcub_bot/core/config.py .env.example \
        tests/test_oplog.py tests/test_config.py
git commit -m "feat: an ops log chat, with the admins' DMs as its fallback"
```

---

### Task 2: crash reports go through `OpsLog`

**Files:**
- Modify: `src/jbcub_bot/core/errors.py:55-73` (`report_exception`)
- Modify: `src/jbcub_bot/main.py:14,58,104-113` (import, signature, errors handler, `run`)
- Test: `tests/test_errors.py` (update 4 call sites, add 1 test)

**Interfaces:**
- Consumes: `OpsLog` from Task 1.
- Produces:
  - `report_exception(oplog, exc: BaseException, context: str) -> None` — the
    `bot`/`admin_ids` pair is gone; delivery is the `OpsLog`'s job.
  - `build_dispatcher(session_factory, bootstrap_ids=None, log_chat_id="")`

- [ ] **Step 1: Update the existing tests to the new signature, and add one**

In `tests/test_errors.py`, change the import on line 15-20 to also bring in
`OpsLog`:

```python
from jbcub_bot.core.oplog import OpsLog
```

Then wrap the bot at all four `report_exception` call sites — the assertions on
`bot.dms` stay exactly as they are, because with no chat id an `OpsLog` DMs the
admins:

```python
await report_exception(OpsLog(bot, "", {1}), _chained(), context="x" * 500)
await report_exception(OpsLog(bot, "", {111, 222}), _caught(lambda: 1 / 0),
                       context="/sync while reading cohort sdt")
await report_exception(OpsLog(bot, "", [111, 222]), _caught(lambda: 1 / 0),
                       context="ctx")
await report_exception(OpsLog(FakeBot(), "", set()), _caught(lambda: 1 / 0),
                       context="ctx")
```

Append this test:

```python
async def test_a_crash_goes_to_the_log_chat_instead_of_the_admins(monkeypatch):
    def boom():
        raise RuntimeError("settings blew up")

    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", boom)
    factory = _factory()
    admin = factory()
    admin.add(User(last_name="A", first_name="Ann", telegram_id=777, role=Role.ADMIN))
    admin.add(User(last_name="Ivan", matriculation="30000001", role=Role.STUDENT))
    admin.commit()
    admin.close()

    dp = build_dispatcher(factory, bootstrap_ids={555}, log_chat_id="-1001234")
    bot = FakeBot()
    await dp.feed_update(bot, _callback_update(bot, 777, "dir:link:30000001"),
                         dispatcher=dp)

    assert [dm.chat_id for dm in bot.dms] == ["-1001234"], \
        "the report should go to the log chat, not to the admin's DM"
    assert "RuntimeError: settings blew up" in bot.dms[0].text
```

- [ ] **Step 2: Run to verify the new test fails**

Run: `uv run pytest tests/test_errors.py -q`
Expected: FAIL — `build_dispatcher() got an unexpected keyword argument
'log_chat_id'`, and `TypeError` on the reworked `report_exception` calls.

- [ ] **Step 3: Rewrite `report_exception`**

Replace lines 55-73 of `src/jbcub_bot/core/errors.py` with:

```python
async def report_exception(oplog, exc: BaseException, context: str) -> None:
    """Log `exc` and send its traceback wherever `oplog` points.

    Never raises: this runs on the failure path, and a bad destination must not
    mask the original error. Delivery -- including the fallback to the bootstrap
    admins -- belongs to `core.oplog`; this function only formats.
    """
    logger.error("%s — %s: %s", context, type(exc).__name__, exc, exc_info=exc)
    if oplog is None:  # a handler called directly, with nothing to send through
        return
    header = f"⚠️ {context[:200]}\n\n{summarize(exc)}\n\n"
    await oplog.send(header + format_traceback(exc, limit=TELEGRAM_LIMIT - len(header)))
```

Also drop the now-unused `from collections.abc import Iterable` at line 10, and
update the module docstring's second paragraph to say the report goes to the log
chat, falling back to the bootstrap admins.

- [ ] **Step 4: Wire it in `main.py`**

Change the import on line 14 and add the `oplog` module:

```python
from jbcub_bot.core import oplog as oplog_mod
from jbcub_bot.core.errors import report_exception, summarize
```

Change the signature on line 58 and add the factory right after the middleware
registration:

```python
def build_dispatcher(session_factory, bootstrap_ids: set | None = None,
                     log_chat_id: str = "") -> Dispatcher:
```

```python
    def ops_log(bot):
        """One per update: the Bot instance only exists per-update."""
        return oplog_mod.OpsLog(bot, log_chat_id, bootstrap_ids or ())
```

In the errors handler, replace the `report_exception` call:

```python
        await report_exception(ops_log(bot), exc,
                              context=describe_update(event.update))
```

In `run()`, pass the setting:

```python
    dp = build_dispatcher(get_session, settings.bootstrap_admin_id_set,
                          settings.log_chat_id)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_errors.py -q`
Expected: PASS (all of them — the four rewritten calls and the new one).

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/core/errors.py src/jbcub_bot/main.py tests/test_errors.py
git commit -m "feat: crash reports go to the log chat"
```

---

### Task 3: log the dead ends

**Files:**
- Modify: `src/jbcub_bot/main.py:77-100` (`nl_fallback`, `nothing_understood`)
- Modify: `AGENTS.md` (one bullet under "Conventions that aren't obvious")
- Test: `tests/test_fallback.py`

**Interfaces:**
- Consumes: `ops_log(bot)` and `oplog_mod.format_miss` from Tasks 1-2.
- Produces: nothing new — this is the last task.

- [ ] **Step 1: Write the failing tests**

In `tests/test_fallback.py`, replace `FakeBot.send_message` so log entries are
recorded apart from replies (`message.answer` goes through `__call__`, so
nothing else uses `send_message`):

```python
class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []
        self.logged: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None

    async def send_message(self, chat_id, text, **kwargs):
        self.logged.append(SimpleNamespace(chat_id=chat_id, text=text))
        return None
```

Add `LOG_CHAT = "-1009999"` next to it, and append these tests:

```python
async def test_an_unmatched_query_is_logged_with_the_answer_it_got():
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="Иванов Пётр"), dispatcher=dp)
    assert len(bot.logged) == 1
    entry = bot.logged[0]
    assert entry.chat_id == LOG_CHAT
    assert "Иванов Пётр" in entry.text
    assert "No one found." in entry.text
    assert "777" in entry.text  # who asked


async def test_a_photo_is_logged_by_its_content_type():
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    await dp.feed_update(bot, _update(bot, photo=photo), dispatcher=dp)
    assert len(bot.logged) == 1
    # Lowercase: `content_type` is a ContentType enum, and interpolating it
    # directly would read "ContentType.PHOTO".
    assert "«photo»" in bot.logged[0].text


async def test_an_unknown_command_is_not_logged():
    # The bot answered correctly -- that is not a gap in what it can do.
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="/nosuchthing"), dispatcher=dp)
    assert bot.logged == []


async def test_a_query_that_found_someone_is_not_logged():
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="Ivan"), dispatcher=dp)
    assert bot.logged == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_fallback.py -q`
Expected: FAIL — the three "is logged" assertions see `bot.logged == []`.

- [ ] **Step 3: Log the misses**

In `src/jbcub_bot/main.py`, replace `nl_fallback` (lines 77-82) with:

```python
    @dp.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session, bot: Bot,
                          impersonator=None):
        handled = await _intent_router.dispatch(message.text, message,
                                                principal, session)
        if not handled:
            await message.answer(NOTHING_MATCHED)
            await ops_log(bot).send(oplog_mod.format_miss(
                query=message.text, answer=NOTHING_MATCHED,
                principal=principal, tg_user=message.from_user,
                impersonator=impersonator,
            ))
```

and `nothing_understood` (lines 90-100) with:

```python
    @fallback.message()
    async def nothing_understood(message: Message, bot: Bot, principal=None,
                                 impersonator=None):
        command = (message.text or "").split()[0] if message.text else ""
        if command.startswith("/"):
            # The bot answered correctly, so this is not a gap worth logging.
            await message.answer(
                f"I don't know {command}. /help lists what I can do."
            )
            return
        answer = "I only read text. /help lists what I can do."
        await message.answer(answer)
        # `.value`, not the enum: aiogram's ContentType is a (str, Enum), so
        # interpolating it would write "ContentType.PHOTO" into the entry.
        await ops_log(bot).send(oplog_mod.format_miss(
            query=message.content_type.value, answer=answer,
            principal=principal, tg_user=message.from_user,
            impersonator=impersonator,
        ))
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions anywhere.

- [ ] **Step 5: Document the convention**

Add to `AGENTS.md`, under "Conventions that aren't obvious", right after the
"Don't swallow unexpected exceptions" bullet:

```markdown
- **Operational reports go through `core/oplog.py`.** `OpsLog` sends to
  `LOG_CHAT_ID` and falls back to `BOOTSTRAP_ADMIN_IDS` in DM when that chat is
  unset or refuses the message, so a report never depends on one destination
  working. `core/errors.py` only formats; `build_dispatcher.ops_log(bot)` builds
  the destination per update, since a `Bot` exists only then. Three call sites
  use it: the `dp.errors` handler, and the two dead ends in `main.py` — a text
  query no intent took, and a non-text message. An unknown command and an
  access refusal are deliberately *not* logged: the bot answered correctly.
  Entries are plain text with no `parse_mode`, because a query containing `_`
  would otherwise break the message.
```

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/main.py tests/test_fallback.py AGENTS.md
git commit -m "feat: log requests that ended with no answer"
```

---

## Manual verification

After Task 3, against the dev bot:

1. Create a private group, add the bot, get its id (e.g. forward a message to
   `@getidsbot`), put it in `.env` as `LOG_CHAT_ID`.
2. `uv run python -m jbcub_bot`, then in Telegram send a name that matches
   nobody → the log chat gets a `🔍 Nothing matched` entry naming you, the
   query, and `No one found.`
3. Send a sticker → an entry with `query: «sticker»`.
4. Send `/nosuchthing` → the bot answers, and **nothing** appears in the chat.
5. Remove the bot from the log chat and repeat step 2 → the entry arrives in
   your DM instead, and the host log shows `Could not deliver an ops report`.
