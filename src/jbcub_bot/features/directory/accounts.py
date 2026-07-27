"""Reading a GitHub or Codeforces account out of whatever the user sent.

Normalization is pure and lives apart from the existence check so the parsing
rules can be tested without a network, and so a handler can reject a typo
before spending a request on it. A normalizer raises `ValueError` whose message
is shown to the user verbatim.
"""

import re

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([^/?#\s]+)", re.IGNORECASE)
_CODEFORCES_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?codeforces\.com/(?:profile/)?([^/?#\s]+)",
    re.IGNORECASE)

# GitHub's own rule: alphanumerics and single inner hyphens, 39 characters max.
_GITHUB_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_CODEFORCES_RE = re.compile(r"^[A-Za-z0-9_.-]{3,24}$")

STATUS_MAX_LEN = 120

_GITHUB_HELP = ("That doesn't look like a GitHub username. Send something "
                "like alice or github.com/alice.")
_CODEFORCES_HELP = ("That doesn't look like a Codeforces handle. Send "
                    "something like alice or codeforces.com/profile/alice.")


def _unwrap(value: str, url_re: re.Pattern) -> str:
    handle = value.strip()
    match = url_re.search(handle)
    if match:
        handle = match.group(1)
    return handle.lstrip("@").strip()


def normalize_github(text: str) -> str:
    handle = _unwrap(text, _GITHUB_URL_RE)
    if not _GITHUB_RE.match(handle):
        raise ValueError(_GITHUB_HELP)
    return handle


def normalize_codeforces(text: str) -> str:
    handle = _unwrap(text, _CODEFORCES_URL_RE)
    if not _CODEFORCES_RE.match(handle):
        raise ValueError(_CODEFORCES_HELP)
    return handle


def normalize_status(text: str) -> str:
    """One line: the status shares a line with a label in three screens."""
    status = " ".join(text.split())
    if not status:
        raise ValueError("Send some text, or tap Clear to remove your status.")
    if len(status) > STATUS_MAX_LEN:
        raise ValueError(
            f"Too long — {STATUS_MAX_LEN} characters max, you sent {len(status)}."
        )
    return status


NORMALIZERS = {
    "status_line": normalize_status,
    "github": normalize_github,
    "codeforces": normalize_codeforces,
}


def normalize(field: str, text: str) -> str:
    """Canonical value for `field`, or ValueError with a message for the user."""
    return NORMALIZERS[field](text)
