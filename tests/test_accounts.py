import pytest

from jbcub_bot.features.directory import accounts
from jbcub_bot.features.directory.accounts import Verdict


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


# --- the existence check ---------------------------------------------------

def _answers(status, body=""):
    async def fetch(url):
        _answers.url = url
        return status, body
    return fetch


def _fails():
    async def fetch(url):
        raise accounts.FetchFailed("connection reset")
    return fetch


async def test_github_200_means_the_account_exists():
    fetch = _answers(200, '{"login": "alice"}')
    assert await accounts.verify("github", "alice", fetch=fetch) is Verdict.EXISTS
    assert _answers.url == "https://api.github.com/users/alice"


async def test_github_404_means_no_such_account():
    assert await accounts.verify("github", "nope", fetch=_answers(404)) \
        is Verdict.MISSING


async def test_github_rate_limit_is_unknown_not_missing():
    # 60 anonymous requests an hour per IP: a shared IP running out of them
    # must not look like "this user doesn't exist".
    assert await accounts.verify("github", "alice", fetch=_answers(403)) \
        is Verdict.UNKNOWN


async def test_a_server_error_is_unknown():
    assert await accounts.verify("github", "alice", fetch=_answers(500)) \
        is Verdict.UNKNOWN


async def test_an_unreachable_service_is_unknown():
    assert await accounts.verify("github", "alice", fetch=_fails()) \
        is Verdict.UNKNOWN


async def test_codeforces_ok_status_means_the_account_exists():
    fetch = _answers(200, '{"status":"OK","result":[{"handle":"alice"}]}')
    assert await accounts.verify("codeforces", "alice", fetch=fetch) \
        is Verdict.EXISTS
    assert _answers.url == \
        "https://codeforces.com/api/user.info?handles=alice"


async def test_codeforces_failed_body_means_missing_despite_the_400():
    # Codeforces answers 400 for a handle it doesn't know, so the body decides.
    fetch = _answers(400, '{"status":"FAILED","comment":"handles: User with '
                          'handle nope not found"}')
    assert await accounts.verify("codeforces", "nope", fetch=fetch) \
        is Verdict.MISSING


async def test_codeforces_unparseable_body_is_unknown():
    fetch = _answers(200, "<html>maintenance</html>")
    assert await accounts.verify("codeforces", "alice", fetch=fetch) \
        is Verdict.UNKNOWN


async def test_a_field_with_nothing_to_check_verifies_trivially():
    async def never_called(url):
        raise AssertionError("status_line needs no network call")

    assert await accounts.verify("status_line", "hi", fetch=never_called) \
        is Verdict.EXISTS
