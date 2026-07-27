# Fuzzy Name Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find a person whatever script, diacritics or transliteration the searcher used, and stay silent when the text is not a name so the intent chain can continue.

**Architecture:** A pure `matching` module reduces every word to two derived forms — `fold` (no diacritics, no case, no punctuation) and `skeleton` (a coarse code where all spellings of one name collapse together). `search.rank_users` scores the whole roster in Python and cuts at a threshold. The search intent returns `False` below that threshold instead of answering, and `nl_fallback` in `main.py` owns the last word.

**Tech Stack:** Python 3.12, `unicodedata` + `re` + `difflib` from the standard library, SQLAlchemy 2.0, aiogram 3, pytest.

**Design:** `docs/superpowers/specs/2026-07-28-fuzzy-name-search-design.md`

## Global Constraints

- **No new dependencies.** `difflib.SequenceMatcher`, not `rapidfuzz`; a hand-written table, not `unidecode`.
- **Thresholds are named constants** in `matching.py`: `ACCEPT = 0.80`, `LEAD = 0.05`, `SPREAD = 0.15`, `MIN_QUERY_LEN = 3`. Nothing else may hardcode these numbers.
- **`matching.py` imports nothing from aiogram or sqlalchemy** — it is string→string and must stay testable without a database.
- **Rule order inside `skeleton` is part of the contract.** Rules live in the two module-level tuples `GLIDES` and `RULES`; do not inline them or reorder them.
- Run tests with `uv run pytest`. All existing tests must keep passing.
- Commit messages follow the repo's style: `feat: …`, `refactor: …`, `test: …`, lowercase, imperative.
- User-facing bot copy is English (`No one found.`, `Several people match:`), matching the rest of the bot. The Cyrillic in this plan appears only in test data.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/jbcub_bot/features/directory/matching.py` | **New.** Pure string matching: `fold`, `latinize`, `skeleton`, `word_score`, `score`, thresholds. |
| `src/jbcub_bot/features/directory/search.py` | Roster-level ranking: `name_tokens`, `rank_users`. `search_users` is deleted. `list_cohort` unchanged. |
| `src/jbcub_bot/core/intents.py` | `dispatch` walks every matching intent and stops at the first that does not return `False`. |
| `src/jbcub_bot/features/directory/handlers.py` | `name_search` ranks, applies the leader rule, returns `bool`. |
| `src/jbcub_bot/main.py` | `nl_fallback` answers when no intent handled the message. |
| `tests/test_matching.py` | **New.** Tables: folds, skeletons, should-match / should-not-match. |
| `tests/test_directory_search.py` | Ranking, threshold, Cyrillic queries against a Latin roster. |
| `tests/test_intents.py` | The `bool` contract. |
| `tests/test_search_integration.py` | **New.** Real dispatcher: profile, tie list, declined text. |
| `AGENTS.md` | Two new conventions. |

---

### Task 1: The two derived forms — `fold` and `skeleton`

This is the whole trick of the feature. `fold` removes decoration; `skeleton` removes the differences that transliteration invents.

**Files:**
- Create: `src/jbcub_bot/features/directory/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fold(text: str) -> str`, `latinize(text: str) -> str`, `skeleton(text: str) -> str`, and the constants `ACCEPT = 0.80`, `LEAD = 0.05`, `SPREAD = 0.15`, `MIN_QUERY_LEN = 3`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matching.py`:

```python
import pytest

from jbcub_bot.features.directory.matching import fold, skeleton


@pytest.mark.parametrize("text,expected", [
    ("José", "jose"), ("Jose'", "jose"), ("JOSE", "jose"),
    ("Hüseyn", "huseyn"), ("Пётр", "петр"), ("Петр", "петр"),
    ("Андрей", "андреи"), ("  Ivan  ", "ivan"), ("Ben-Othman", "benothman"),
])
def test_fold_strips_decoration(text, expected):
    assert fold(text) == expected


@pytest.mark.parametrize("spellings,expected", [
    (["Ярослав", "Iaroslav", "Yaroslav", "Jaroslav"], "AROSLAV"),
    (["Пётр", "Петр", "Petr", "Pyotr", "Piotr"], "PETR"),
    (["Алексей", "Alexey", "Aleksei"], "ALEKSEI"),
    (["Щеглов", "Scheglov", "Shcheglov"], "SEGLOV"),
    (["Хусейн", "Huseyn", "Khuseyn"], "HUSEIN"),
    (["Ефременко", "Efremenko"], "EFREMENKO"),
    (["Цветков", "Tsvetkov"], "ZVETKOV"),
])
def test_one_skeleton_per_name(spellings, expected):
    assert {skeleton(s) for s in spellings} == {expected}


def test_skeleton_keeps_a_latin_name_intact():
    # Word-initial jo survives; collapsing it would leave "ose".
    assert skeleton("Jose") == "JOSE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_matching.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jbcub_bot.features.directory.matching'`

- [ ] **Step 3: Write the implementation**

Create `src/jbcub_bot/features/directory/matching.py`:

```python
"""Script-agnostic name matching.

Roster names are stored in Latin script, but people search in Cyrillic, with
diacritics, and in whichever transliteration they happen to remember. So a
word is never compared as typed: it is compared through two derived forms.

`fold` strips everything that is decoration -- diacritics, case, punctuation.
`skeleton` goes further and collapses the ambiguity transliteration itself
creates, so that every spelling of one name reduces to the same code:
Ярослав, Iaroslav and Yaroslav all become AROSLAV.

A skeleton is deliberately coarse, so it never decides a match alone -- it is
one signal among several in `word_score`, and a penalised one.
"""

import re
import unicodedata

# Below this a match is not reported at all: the search intent declines and the
# turn passes to whatever comes next. Measured over the roster, real matches
# score 0.84 and up while non-names stay at 0.76 and below.
ACCEPT = 0.80
# A leader at least this far ahead of the runner-up is shown as a profile.
LEAD = 0.05
# Everyone scoring within this of the leader is listed next to them.
SPREAD = 0.15
# No metric says anything useful about a one- or two-letter query.
MIN_QUERY_LEN = 3

CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "i", "є": "e", "ґ": "g", "ў": "u",
}

# A leading glide is part of the name; a leading yo/ye is not -- collapsing it
# would eat the first letter of Latin names like Jose, so those two rules only
# fire after another character.
GLIDES = (
    (r"ya|ia|ja", "a"),
    (r"yu|iu|ju", "u"),
    (r"(?<=.)(?:yo|io|jo)", "e"),
    (r"(?<=.)(?:ye|ie|je)", "e"),
)

# Ordered: each rule may consume letters the next one would have matched, so
# the sequence is part of the contract. Uppercase output marks a finished
# phoneme -- later rules only ever match lowercase input.
RULES = (
    (r"shch|sch|sh", "S"),
    (r"tch|ch", "C"),
    (r"zh|j", "J"),
    (r"kh|h", "H"),
    (r"ph|f", "F"),
    (r"ts|tz|z", "Z"),
    (r"x", "KS"),
    (r"ck|q|k", "K"),
    (r"w|v", "V"),
    (r"c(?=[eiy])", "S"),
    (r"c", "K"),
    (r"y", "I"),
)


def fold(text: str) -> str:
    """Lowercase, diacritic-free, punctuation-free form of `text`.

    NFKD splits a decorated letter into a plain one plus its marks, so
    dropping the marks turns José into jose and ё into е for free.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped if c.isalnum())


def latinize(text: str) -> str:
    return "".join(CYRILLIC.get(c, c) for c in fold(text))


def skeleton(text: str) -> str:
    """Coarse spelling-independent code for `text`."""
    word = latinize(text)
    for pattern, replacement in GLIDES:
        word = re.sub(pattern, replacement, word)
    for pattern, replacement in RULES:
        word = re.sub(pattern, replacement, word)
    return re.sub(r"(.)\1+", r"\1", word.upper())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_matching.py -q`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/matching.py tests/test_matching.py
git commit -m "feat: fold and skeleton, one code per spelling of a name"
```

---

### Task 2: Scoring a query against a person

**Files:**
- Modify: `src/jbcub_bot/features/directory/matching.py` (append)
- Test: `tests/test_matching.py` (append)

**Interfaces:**
- Consumes: `fold`, `skeleton`, `MIN_QUERY_LEN`, `ACCEPT` from Task 1.
- Produces: `word_score(query: str, name: str) -> float` and `score(query: str, tokens: Sequence[str]) -> float`, both in `0.0..1.0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matching.py` (and extend its import line to
`from jbcub_bot.features.directory.matching import ACCEPT, fold, score, skeleton, word_score`):

```python
@pytest.mark.parametrize("query,tokens", [
    ("Ярослав", ["Iaroslav", "Belozerov"]),
    ("Yaroslav", ["Iaroslav", "Belozerov"]),
    ("Белозеров", ["Iaroslav", "Belozerov"]),
    ("Ярослав Белозеров", ["Iaroslav", "Belozerov"]),
    ("Belozerov Iaroslav", ["Iaroslav", "Belozerov"]),
    ("Хусейн", ["Huseyn", "Huseynov"]),
    ("Щеглов", ["Igor", "Chsheglov"]),
    ("Кокеридзе", ["Nika", "Kokheridze"]),
    ("Апхазава", ["David", "Apkhazava"]),
    ("Бен Отман", ["Mohamed", "Aziz", "Ben", "Othman"]),
    ("ben othman", ["Mohamed", "Aziz", "Ben", "Othman"]),
    ("Ярослава", ["Iaroslav", "Belozerov"]),
])
def test_a_name_is_found(query, tokens):
    assert score(query, tokens) >= ACCEPT


@pytest.mark.parametrize("query,tokens", [
    ("Иванов", ["Ivan", "Osipenko"]),
    ("привет", ["Pavel", "Egorov"]),
    ("спасибо", ["Pavel", "Egorov"]),
    ("как дела", ["Jessica", "Nasser"]),
    ("кто такой Ярослав", ["Iaroslav", "Belozerov"]),
    ("Petr", ["Mert", "Beren"]),
    ("Zzzzzz", ["Mohamed", "Aziz"]),
])
def test_not_a_name_stays_below_the_threshold(query, tokens):
    assert score(query, tokens) < ACCEPT


def test_query_longer_than_the_name_scores_zero():
    assert score("Ivan Ivanov Ivanovich", ["Ivan", "Ivanov"]) == 0.0


def test_empty_query_scores_zero():
    assert score("   ", ["Ivan"]) == 0.0


def test_word_score_ignores_an_empty_token():
    assert word_score("Ivan", "") == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_matching.py -q`
Expected: FAIL — `ImportError: cannot import name 'score'`

- [ ] **Step 3: Write the implementation**

Add to the imports at the top of `matching.py`:

```python
from collections.abc import Sequence
from difflib import SequenceMatcher
```

Append to `matching.py`:

```python
def word_score(query: str, name: str) -> float:
    """How well one query word matches one word of a name, in 0..1."""
    folded_query, folded_name = fold(query), fold(name)
    if not folded_query or not folded_name:
        return 0.0
    if len(folded_query) >= MIN_QUERY_LEN and folded_name.startswith(folded_query):
        return 1.0
    best = SequenceMatcher(None, folded_query, folded_name).ratio()
    coded_query, coded_name = skeleton(query), skeleton(name)
    if coded_query and coded_name:
        if (len(coded_query) >= MIN_QUERY_LEN
                and coded_name.startswith(coded_query)):
            coded = 0.95
        else:
            # Penalised: a skeleton throws away real information, so it is the
            # signal most likely to agree about two different people.
            coded = SequenceMatcher(None, coded_query, coded_name).ratio() * 0.95
        best = max(best, coded)
    return best


def score(query: str, tokens: Sequence[str]) -> float:
    """How well `query` matches a person whose name words are `tokens`.

    Every word of the query must find its own word of the name: each one
    greedily claims the best token still free, and the mean is the result. A
    query with more words than the name scores 0, which is what keeps a
    sentence containing a name from being treated as a search for it.
    """
    words = [w for w in query.split() if fold(w)]
    if not words:
        return 0.0
    free = [t for t in tokens if t]
    total = 0.0
    for word in words:
        if not free:
            return 0.0
        best, index = max((word_score(word, t), i) for i, t in enumerate(free))
        total += best
        free.pop(index)
    return total / len(words)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_matching.py -q`
Expected: PASS, 39 tests.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/matching.py tests/test_matching.py
git commit -m "feat: score a query against a person, word by word"
```

---

### Task 3: Rank the roster

**Files:**
- Modify: `src/jbcub_bot/features/directory/search.py:1-16` (replace `search_users`; leave `list_cohort` alone)
- Test: `tests/test_directory_search.py`

**Interfaces:**
- Consumes: `matching.score`, `matching.fold`, `matching.ACCEPT`, `matching.MIN_QUERY_LEN`.
- Produces: `name_tokens(user: User) -> list[str]` and `rank_users(session, query: str) -> list[tuple[float, User]]`, sorted best first. `search_users` no longer exists — Task 5 updates its only caller.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `tests/test_directory_search.py`:

```python
from jbcub_bot.core.models import User
from jbcub_bot.features.directory.search import list_cohort, rank_users


def _seed(session):
    session.add_all([
        User(first_name="Iaroslav", last_name="Belozerov", handle_sheet="yarik",
             primary_cohort="2024"),
        User(first_name="Igor", last_name="Chsheglov", handle_observed="igor",
             primary_cohort="2024"),
        User(first_name="Anna", last_name="Smith", handle_sheet="asmith",
             primary_cohort="2021"),
    ])
    session.commit()


def _names(ranked):
    return [user.full_name for _, user in ranked]


def test_finds_a_latin_name_from_cyrillic(session):
    _seed(session)
    assert _names(rank_users(session, "Ярослав")) == ["Iaroslav Belozerov"]


def test_finds_the_same_person_from_another_transliteration(session):
    _seed(session)
    assert _names(rank_users(session, "Yaroslav")) == ["Iaroslav Belozerov"]


def test_finds_by_last_name(session):
    _seed(session)
    assert _names(rank_users(session, "Щеглов")) == ["Igor Chsheglov"]


def test_finds_by_handle(session):
    _seed(session)
    assert _names(rank_users(session, "asmith")) == ["Anna Smith"]


def test_best_match_comes_first(session):
    _seed(session)
    ranked = rank_users(session, "Anna")
    assert ranked[0][1].full_name == "Anna Smith"
    assert ranked[0][0] >= ranked[-1][0]


def test_small_talk_matches_nobody(session):
    _seed(session)
    assert rank_users(session, "как дела") == []


def test_a_two_letter_query_matches_nobody(session):
    _seed(session)
    assert rank_users(session, "An") == []


def test_list_cohort_by_primary(session):
    _seed(session)
    names = {u.full_name for u in list_cohort(session, "2024")}
    assert names == {"Iaroslav Belozerov", "Igor Chsheglov"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_directory_search.py -q`
Expected: FAIL — `ImportError: cannot import name 'rank_users'`

- [ ] **Step 3: Write the implementation**

Replace lines 1–16 of `src/jbcub_bot/features/directory/search.py` (everything above `list_cohort`) with:

```python
from sqlalchemy import select

from jbcub_bot.core.models import User
from jbcub_bot.features.directory import matching


def name_tokens(user: User) -> list[str]:
    """Every word a search could reasonably be aiming at."""
    words = f"{user.first_name or ''} {user.last_name or ''}".split()
    return words + [h for h in (user.handle_sheet, user.handle_observed) if h]


def rank_users(session, query: str) -> list[tuple[float, User]]:
    """Everyone matching `query` well enough, best first.

    The whole roster is scored in Python. It is a few dozen rows, and no SQL
    dialect can compare a Cyrillic query against a Latin name anyway.
    """
    if len(matching.fold(query)) < matching.MIN_QUERY_LEN:
        return []
    hits = [(score, user)
            for user in session.scalars(select(User)).all()
            if (score := matching.score(query, name_tokens(user)))
            >= matching.ACCEPT]
    hits.sort(key=lambda hit: (-hit[0], hit[1].full_name))
    return hits
```

Note the `or_` import from sqlalchemy is no longer used — the new import line is `from sqlalchemy import select`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_directory_search.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/search.py tests/test_directory_search.py
git commit -m "refactor: rank the roster by score instead of four ILIKEs"
```

---

### Task 4: An intent may decline

**Files:**
- Modify: `src/jbcub_bot/core/intents.py:37-42`
- Test: `tests/test_intents.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `IntentRouter.dispatch` now tries every intent whose pattern matches, in registration order, and stops at the first handler that does not return `False`. A handler returning `None` still counts as handled.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_intents.py`:

```python
async def test_a_declining_handler_passes_the_turn_on():
    calls = []

    async def declines(message, principal, session):
        calls.append("first")
        return False

    async def accepts(message, principal, session):
        calls.append("second")
        return True

    r = IntentRouter()
    r.register(Intent("first", r".+", handler=declines))
    r.register(Intent("second", r".+", handler=accepts))
    handled = await r.dispatch("hi", message="M", principal=None, session="S")
    assert handled is True
    assert calls == ["first", "second"]


async def test_all_intents_declining_is_unhandled():
    async def declines(message, principal, session):
        return False

    r = IntentRouter()
    r.register(Intent("only", r".+", handler=declines))
    handled = await r.dispatch("hi", message="M", principal=None, session="S")
    assert handled is False


async def test_a_handler_returning_none_still_counts_as_handled():
    calls = []

    async def silent(message, principal, session):
        calls.append("first")

    async def never(message, principal, session):
        calls.append("second")

    r = IntentRouter()
    r.register(Intent("first", r".+", handler=silent))
    r.register(Intent("second", r".+", handler=never))
    handled = await r.dispatch("hi", message="M", principal=None, session="S")
    assert handled is True
    assert calls == ["first"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_intents.py -q`
Expected: FAIL — `test_a_declining_handler_passes_the_turn_on` asserts `calls == ["first", "second"]` but gets `["first"]`, because today's `dispatch` returns after the first match.

- [ ] **Step 3: Write the implementation**

Replace `IntentRouter.dispatch` in `src/jbcub_bot/core/intents.py`:

```python
    async def dispatch(self, text, message, principal, session) -> bool:
        """Offer `text` to each matching intent until one takes it.

        A handler returning False declines -- it must not have answered -- and
        the turn goes to the next intent. Anything else (including None, so a
        handler that forgets to return cannot go silently unhandled) ends the
        walk.
        """
        for intent in self._intents:
            if not re.search(intent.pattern, text, re.IGNORECASE):
                continue
            if not intent_allowed(principal, intent):
                continue
            if await intent.handler(message, principal, session) is not False:
                return True
        return False
```

`matches()` stays as it is: `/help` and the existing tests use it to ask which intent a text would reach first.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_intents.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/intents.py tests/test_intents.py
git commit -m "feat: let an intent decline and pass the turn on"
```

---

### Task 5: Wire it up — leader rule, declining, the last word

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py:24` (import) and `:77-99` (`name_search`)
- Modify: `src/jbcub_bot/main.py:64-70` (`nl_fallback`)
- Modify: `AGENTS.md`
- Test: `tests/test_search_integration.py` (create)

**Interfaces:**
- Consumes: `rank_users` (Task 3), `matching.LEAD` / `matching.SPREAD` (Task 1), the `bool` dispatch contract (Task 4).
- Produces: `name_search(message, principal, session) -> bool`; `main.NOTHING_MATCHED` (the string `"No one found."`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_search_integration.py`:

```python
"""End-to-end coverage for name search: real dispatcher, real ranking.

Scoring itself is covered in test_matching.py. What needs proving here is the
wiring -- that a clear winner opens a profile, that a tie opens a list, and
that text which is not a name gets the fallback instead of a wrong person.
"""

from datetime import datetime, timezone

from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.main import NOTHING_MATCHED, build_dispatcher


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory):
    setup = factory()
    setup.add_all([
        User(last_name="Ivanov", first_name="Ivan", telegram_id=222,
             role=Role.STUDENT, primary_cohort="2024", matriculation="30001111"),
        User(last_name="Belozerov", first_name="Iaroslav",
             role=Role.STUDENT, primary_cohort="2024", matriculation="30002222"),
        User(last_name="Redko", first_name="Mikhail",
             role=Role.STUDENT, primary_cohort="2024", matriculation="30003333"),
        User(last_name="Efremenko", first_name="Mikhail",
             role=Role.STUDENT, primary_cohort="2024", matriculation="30004444"),
    ])
    setup.commit()
    setup.close()


def _message_update(fake_bot, text: str, telegram_id=222, update_id=1) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


async def _say(text: str) -> FakeBot:
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()
    await dp.feed_update(fake_bot, _message_update(fake_bot, text), dispatcher=dp)
    return fake_bot


async def test_a_clear_winner_opens_a_profile():
    fake_bot = await _say("Ярослав")
    assert "Iaroslav Belozerov" in fake_bot.sent[0].text
    assert "Several people match" not in fake_bot.sent[0].text


async def test_a_tie_lists_everyone_close():
    fake_bot = await _say("Михаил")
    text = fake_bot.sent[0].text
    assert text.startswith("Several people match:")
    assert "Mikhail Redko" in text
    assert "Mikhail Efremenko" in text
    assert "Iaroslav Belozerov" not in text


async def test_text_that_is_not_a_name_gets_the_fallback():
    fake_bot = await _say("как дела")
    assert fake_bot.sent[0].text == NOTHING_MATCHED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_search_integration.py -q`
Expected: FAIL — `ImportError: cannot import name 'NOTHING_MATCHED' from 'jbcub_bot.main'`

- [ ] **Step 3: Rewrite `name_search`**

In `handlers.py`, change the import on line 24 to:

```python
from jbcub_bot.features.directory import matching
from jbcub_bot.features.directory.search import list_cohort, rank_users
```

Replace `name_search` (lines 77–91) with:

```python
async def name_search(message: Message, principal: User, session) -> bool:
    """Answer with a profile or a shortlist; return False when unsure.

    Returning False leaves the message unanswered on purpose: the intent
    router moves on, and whatever ends the chain gets to reply.
    """
    if principal is None:
        # Answering here rather than declining: an unlinked user gets told what
        # to do instead of a puzzling "No one found."
        await message.answer("You are not linked yet. Contact an admin.")
        return True
    ranked = rank_users(session, (message.text or "").strip())
    if not ranked:
        return False
    best, target = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best - runner_up >= matching.LEAD:
        kb = admin_keyboard(target) if principal.role is Role.ADMIN else None
        await message.answer(render_profile(principal, target), reply_markup=kb)
        return True
    close = [user for score, user in ranked if best - score <= matching.SPREAD]
    lines = [f"- {user.full_name}" for user in close[:20]]
    await message.answer("Several people match:\n" + "\n".join(lines))
    return True
```

- [ ] **Step 4: Give the chain a last word**

In `main.py`, add above `build_dispatcher`:

```python
# What the bot says when no intent took the message. Search is the only intent
# today, so this is its "not found"; when the chain grows it becomes the
# generic "I didn't understand that".
NOTHING_MATCHED = "No one found."
```

and replace the body of `nl_fallback` (lines 68–70) with:

```python
    @dp.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        handled = await _intent_router.dispatch(message.text, message,
                                                principal, session)
        if not handled:
            await message.answer(NOTHING_MATCHED)
```

Also update the comment above the handler: the `.+` intent no longer swallows every message by matching it, but it still runs before any sub-router, so `StateFilter(None)` stays load-bearing.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. `tests/test_directory_handlers.py` and `tests/test_help_integration.py` must stay green; if `test_search_intent_matches_plain_text` fails, the intent's `pattern` was changed — it must remain `r".+"`, since declining now happens by return value, not by pattern.

- [ ] **Step 6: Document the two new conventions**

In `AGENTS.md`, under "Conventions that aren't obvious", add:

```markdown
- **An intent handler returns `bool`.** `False` means "not mine" — the router
  offers the message to the next intent, so a declining handler must not have
  answered. Anything else (including `None`) ends the walk. Below its
  threshold the name search declines; `nl_fallback` in `main.py` owns the
  reply when nothing took the message.
- **Name matching lives in `features/directory/matching.py`** and is pure
  string work — no aiogram, no sqlalchemy. Every roster name is Latin while
  queries arrive in Cyrillic, so comparison happens on `fold` (no diacritics,
  no case, no punctuation) and `skeleton` (one code per name, whatever the
  transliteration). Thresholds are the constants at the top of that module;
  the rule tuples `GLIDES` and `RULES` are order-dependent, and
  `tests/test_matching.py` is the table that keeps a new rule from fixing one
  name and breaking three.
```

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/handlers.py src/jbcub_bot/main.py \
        tests/test_search_integration.py AGENTS.md
git commit -m "feat: fuzzy name search that declines when it is unsure"
```

---

## Manual verification

With a populated `.env`, `uv run python -m jbcub_bot`, then in Telegram:

| send | expect |
|---|---|
| `Ярослав` | the profile of the roster's Iaroslav |
| `Yaroslav`, `Iaroslav` | the same profile |
| a last name in Cyrillic | that person's profile |
| a first name two people share | `Several people match:` and both |
| `как дела` | `No one found.` |
| `/edit`, tap a field, send a value | the value is saved — the FSM path still wins over search |
