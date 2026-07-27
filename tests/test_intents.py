from jbcub_bot.core.intents import Intent, IntentRouter
from jbcub_bot.core.models import Role, User


def test_matches_first_registered():
    r = IntentRouter()
    r.register(Intent("a", r"hello", handler=None))
    r.register(Intent("b", r"hel", handler=None))
    assert r.matches("hello there").name == "a"
    assert r.matches("nope") is None


async def test_dispatch_invokes_handler():
    calls = []

    async def h(message, principal, session):
        calls.append((message, principal))

    r = IntentRouter()
    r.register(Intent("greet", r"hi", handler=h))
    principal = User(last_name="P", role=Role.STUDENT)
    handled = await r.dispatch("hi", message="M", principal=principal, session="S")
    assert handled is True
    assert calls == [("M", principal)]


async def test_dispatch_no_match_returns_false():
    r = IntentRouter()
    handled = await r.dispatch("whatever", message="M", principal=None, session="S")
    assert handled is False


def test_intent_has_metadata_defaults():
    i = Intent("x", r".+", handler=None)
    assert i.description == ""
    assert i.min_role is Role.STUDENT


async def test_dispatch_skips_intent_above_principal_role():
    calls = []

    async def h(message, principal, session):
        calls.append(True)

    r = IntentRouter()
    r.register(Intent("admin-only", r".+", handler=h, min_role=Role.ADMIN))
    handled = await r.dispatch(
        "anything", message="M",
        principal=User(last_name="S", role=Role.STUDENT), session="S",
    )
    assert handled is False
    assert calls == []


async def test_dispatch_runs_student_intent_for_unlinked():
    calls = []

    async def h(message, principal, session):
        calls.append(principal)

    r = IntentRouter()
    r.register(Intent("search", r".+", handler=h, min_role=Role.STUDENT))
    handled = await r.dispatch("Ivan", message="M", principal=None, session="S")
    assert handled is True
    assert calls == [None]


async def test_dispatch_runs_admin_intent_for_admin():
    calls = []

    async def h(message, principal, session):
        calls.append(True)

    r = IntentRouter()
    r.register(Intent("admin-only", r".+", handler=h, min_role=Role.ADMIN))
    handled = await r.dispatch(
        "x", message="M",
        principal=User(last_name="A", role=Role.ADMIN), session="S",
    )
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
