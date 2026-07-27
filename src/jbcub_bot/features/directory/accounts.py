"""Reading a GitHub or Codeforces account out of whatever the user sent.

Normalization is pure and lives apart from the existence check so the parsing
rules can be tested without a network, and so a handler can reject a typo
before spending a request on it. A normalizer raises `ValueError` whose message
is shown to the user verbatim.
"""

import asyncio
import enum
import json
import re

import aiohttp

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


class Verdict(enum.Enum):
    EXISTS = "exists"
    MISSING = "missing"    # the service said there is no such user
    UNKNOWN = "unknown"    # the service didn't say


class FetchFailed(Exception):
    """The service could not be reached."""


HTTP_TIMEOUT = 5.0


async def _http_fetch(url: str) -> tuple[int, str]:
    """GET `url` with a deadline, as (status, body).

    aiohttp arrives with aiogram, so this adds no dependency. The deadline is
    the point: one event loop runs the whole bot, and a request that never
    answers would hold up every other update.
    """
    try:
        async with asyncio.timeout(HTTP_TIMEOUT):
            async with aiohttp.ClientSession() as http:
                async with http.get(url) as response:
                    return response.status, await response.text()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise FetchFailed(str(exc)) from exc


async def _verify_github(handle: str, fetch) -> Verdict:
    try:
        status, _ = await fetch(f"https://api.github.com/users/{handle}")
    except FetchFailed:
        return Verdict.UNKNOWN
    if status == 200:
        return Verdict.EXISTS
    if status == 404:
        return Verdict.MISSING
    return Verdict.UNKNOWN  # 403 rate limit, 5xx, anything unexpected


async def _verify_codeforces(handle: str, fetch) -> Verdict:
    try:
        _, body = await fetch(
            f"https://codeforces.com/api/user.info?handles={handle}")
    except FetchFailed:
        return Verdict.UNKNOWN
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Verdict.UNKNOWN  # an HTML error page, not the API
    status = payload.get("status") if isinstance(payload, dict) else None
    if status == "OK":
        return Verdict.EXISTS
    # FAILED also covers malformed requests, but the handle passed our own
    # format check before we got here, so "not found" is what's left.
    if status == "FAILED":
        return Verdict.MISSING
    return Verdict.UNKNOWN


_VERIFIERS = {"github": _verify_github, "codeforces": _verify_codeforces}


async def verify(field: str, handle: str, fetch=_http_fetch) -> Verdict:
    """Does the account exist? EXISTS for a field with nothing to check.

    `handle` has been through `normalize`, so it holds only characters that are
    safe in a URL path or query and needs no escaping.

    `fetch` is a parameter so tests never touch the network.
    """
    verifier = _VERIFIERS.get(field)
    if verifier is None:
        return Verdict.EXISTS
    return await verifier(handle, fetch)
