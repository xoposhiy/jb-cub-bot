"""What is left of the intent machinery: the `Intent` dataclass, and the one
walk that still offers text to the intents `kb` declares.

The walk used to be `IntentRouter.dispatch` and these assertions used to be
pointed at it. They are pointed at `core/legacy.py`'s `LegacyIntents.offer`
instead, because that is the copy production runs: `IntentRouter` had stopped
being called by anything the day the pipeline landed, so a fix made here would
have been made to dead code. Both files go in phase F.

Ordered dispatch as a *contract* -- decline passes the turn on, `None` takes,
nothing taking it is unhandled -- is `tests/test_pipeline.py`'s now, asked of
the real chain. What is asked here is only that the shim's inner walk obeys the
same rules, since it is a chain slot that has to route on its own.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from jbcub_bot.core.contract import Registry
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.legacy import LegacyIntents
from jbcub_bot.core.loader import LoadedFeature, Manifest
from jbcub_bot.core.models import Role, User


def _shim(*intents: Intent) -> LegacyIntents:
    """The shim hosting `intents`, adopted the way `main.py` adopts them."""
    registry = Registry()
    shim = LegacyIntents(registry)
    shim.adopt([LoadedFeature(
        name="relic", module=object(), router=SimpleNamespace(),
        manifest=Manifest(name="relic", intents=list(intents),
                          help_text="A relic."),
    )])
    return shim


def _message(text: str):
    """`offer` reads `message.text`; nothing else about the message matters."""
    return SimpleNamespace(text=text, date=datetime.now(timezone.utc))


# --- the dataclass ------------------------------------------------------------

def test_intent_has_metadata_defaults():
    i = Intent("x", r".+", handler=None)
    assert i.description == ""
    assert i.min_role is Role.STUDENT


# --- the walk -----------------------------------------------------------------

async def test_offer_invokes_a_matching_handler():
    calls = []

    async def h(message, principal, session):
        calls.append((message.text, principal))

    principal = User(last_name="P", role=Role.STUDENT)
    message = _message("hi")
    handled = await _shim(Intent("greet", r"hi", handler=h)).offer(
        message, principal, "S")
    assert handled is True
    assert calls == [("hi", principal)]


async def test_offer_with_nothing_matching_returns_false():
    handled = await _shim().offer(_message("whatever"), None, "S")
    assert handled is False


async def test_offer_skips_an_intent_above_the_principals_role():
    calls = []

    async def h(message, principal, session):
        calls.append(True)

    shim = _shim(Intent("admin-only", r".+", handler=h, min_role=Role.ADMIN))
    handled = await shim.offer(_message("anything"),
                               User(last_name="S", role=Role.STUDENT), "S")
    assert handled is False
    assert calls == []


async def test_offer_runs_a_student_intent_for_an_unlinked_caller():
    calls = []

    async def h(message, principal, session):
        calls.append(principal)

    shim = _shim(Intent("search", r".+", handler=h, min_role=Role.STUDENT))
    handled = await shim.offer(_message("Ivan"), None, "S")
    assert handled is True
    assert calls == [None]


async def test_offer_runs_an_admin_intent_for_an_admin():
    calls = []

    async def h(message, principal, session):
        calls.append(True)

    shim = _shim(Intent("admin-only", r".+", handler=h, min_role=Role.ADMIN))
    handled = await shim.offer(_message("x"),
                               User(last_name="A", role=Role.ADMIN), "S")
    assert handled is True
    assert calls == [True]


async def test_a_declining_handler_passes_the_turn_on():
    calls = []

    async def declines(message, principal, session):
        calls.append("first")
        return False

    async def accepts(message, principal, session):
        calls.append("second")
        return True

    shim = _shim(Intent("first", r".+", handler=declines),
                 Intent("second", r".+", handler=accepts))
    handled = await shim.offer(_message("hi"), None, "S")
    assert handled is True
    assert calls == ["first", "second"]


async def test_every_intent_declining_leaves_the_slot_unhandled():
    """`False` back is what lets the core's own last word answer instead."""
    async def declines(message, principal, session):
        return False

    shim = _shim(Intent("only", r".+", handler=declines))
    assert await shim.offer(_message("hi"), None, "S") is False


async def test_a_handler_returning_none_still_counts_as_handled():
    calls = []

    async def silent(message, principal, session):
        calls.append("first")

    async def never(message, principal, session):
        calls.append("second")

    shim = _shim(Intent("first", r".+", handler=silent),
                 Intent("second", r".+", handler=never))
    handled = await shim.offer(_message("hi"), None, "S")
    assert handled is True
    assert calls == ["first"]


async def test_the_pattern_is_matched_case_insensitively_and_anywhere():
    """What `IntentRouter.matches` was for, asked of the walk that survived."""
    calls = []

    async def h(message, principal, session):
        calls.append(message.text)

    shim = _shim(Intent("greet", r"hello", handler=h))
    assert await shim.offer(_message("well HELLO there"), None, "S") is True
    assert await shim.offer(_message("nope"), None, "S") is False
    assert calls == ["well HELLO there"]
