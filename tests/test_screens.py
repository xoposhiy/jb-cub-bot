from jbcub_bot.features.directory.screens import EMPTY, short_value


def test_short_value_shows_a_dash_for_nothing():
    assert short_value(None) == EMPTY
    assert short_value("") == EMPTY


def test_short_value_keeps_a_short_value_verbatim():
    assert short_value("alice") == "alice"


def test_short_value_truncates_a_long_one_with_an_ellipsis():
    shortened = short_value("x" * 80)
    assert len(shortened) < 80
    assert shortened.endswith("…")
