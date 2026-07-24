from jbcub_bot.core.intents import Intent, IntentRouter


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
    handled = await r.dispatch("hi", message="M", principal="P", session="S")
    assert handled is True
    assert calls == [("M", "P")]


async def test_dispatch_no_match_returns_false():
    r = IntentRouter()
    handled = await r.dispatch("whatever", message="M", principal=None, session="S")
    assert handled is False
