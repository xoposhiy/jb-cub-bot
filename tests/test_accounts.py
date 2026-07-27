import pytest

from jbcub_bot.features.directory import accounts


@pytest.mark.parametrize("typed", [
    "alice", " alice ", "@alice", "github.com/alice",
    "https://github.com/alice", "https://www.github.com/alice/",
    "https://github.com/alice?tab=repositories",
])
def test_github_accepts_a_username_or_any_link_to_it(typed):
    assert accounts.normalize("github", typed) == "alice"


@pytest.mark.parametrize("typed", [
    "", "   ", "-alice", "alice-", "ali--ce", "a" * 40, "alice bob",
    "alice/bob", "https://github.com/",
])
def test_github_refuses_what_cannot_be_a_username(typed):
    with pytest.raises(ValueError, match="GitHub"):
        accounts.normalize("github", typed)


@pytest.mark.parametrize("typed", [
    "alice", "@alice", "codeforces.com/profile/alice",
    "https://codeforces.com/profile/alice",
])
def test_codeforces_accepts_a_handle_or_a_profile_link(typed):
    assert accounts.normalize("codeforces", typed) == "alice"


def test_codeforces_keeps_the_characters_it_allows():
    assert accounts.normalize("codeforces", "al_ice.1-x") == "al_ice.1-x"


@pytest.mark.parametrize("typed", ["", "ab", "a" * 25, "ali ce", "ali/ce"])
def test_codeforces_refuses_what_cannot_be_a_handle(typed):
    with pytest.raises(ValueError, match="Codeforces"):
        accounts.normalize("codeforces", typed)


def test_status_collapses_whitespace_into_one_line():
    assert accounts.normalize("status_line", " looking\nfor  a \n team ") == \
        "looking for a team"


def test_status_refuses_an_empty_text():
    with pytest.raises(ValueError, match="Clear"):
        accounts.normalize("status_line", "   \n ")


def test_status_refuses_a_too_long_text_and_says_how_long_it_was():
    with pytest.raises(ValueError) as err:
        accounts.normalize("status_line", "x" * 154)
    assert "120" in str(err.value)
    assert "154" in str(err.value)


def test_status_at_the_limit_is_accepted():
    text = "x" * accounts.STATUS_MAX_LEN
    assert accounts.normalize("status_line", text) == text


def test_an_unknown_field_is_a_programming_error():
    with pytest.raises(KeyError):
        accounts.normalize("birthday", "whatever")
