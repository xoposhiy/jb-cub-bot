import json

import pytest

from jbcub_bot.core import sheets_client

SA_INFO = {"type": "service_account", "project_id": "p"}


def test_build_credentials_prefers_inline_json(monkeypatch):
    captured = {}

    def fake_from_info(info, scopes=None):
        captured["info"] = info
        captured["scopes"] = scopes
        return "creds-from-json"

    monkeypatch.setattr(
        sheets_client.Credentials, "from_service_account_info", fake_from_info
    )
    monkeypatch.setattr(
        sheets_client.Credentials,
        "from_service_account_file",
        lambda *a, **k: pytest.fail("must not touch the filesystem when JSON is given"),
    )

    result = sheets_client.build_credentials("sa.json", json.dumps(SA_INFO))

    assert result == "creds-from-json"
    assert captured["info"] == SA_INFO
    assert captured["scopes"] == sheets_client._SCOPES


def test_build_credentials_raises_when_nothing_configured():
    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT_JSON") as exc_info:
        sheets_client.build_credentials("", "")

    assert "GOOGLE_SERVICE_ACCOUNT_FILE" in str(exc_info.value)


def test_build_credentials_falls_back_to_file(monkeypatch):
    captured = {}

    def fake_from_file(path, scopes=None):
        captured["path"] = path
        captured["scopes"] = scopes
        return "creds-from-file"

    monkeypatch.setattr(
        sheets_client.Credentials, "from_service_account_file", fake_from_file
    )

    result = sheets_client.build_credentials("sa.json", "")

    assert result == "creds-from-file"
    assert captured["path"] == "sa.json"
    assert captured["scopes"] == sheets_client._SCOPES


class _FakeRequest:
    def __init__(self, captured, values):
        self._captured = captured
        self._values = values

    def execute(self, num_retries=0):
        self._captured["num_retries"] = num_retries
        return {"values": self._values} if self._values is not None else {}


class _FakeValues:
    def __init__(self, captured, values):
        self._captured = captured
        self._values = values

    def get(self, spreadsheetId=None, range=None):
        self._captured["sheet_id"] = spreadsheetId
        self._captured["range"] = range
        return _FakeRequest(self._captured, self._values)


class _FakeService:
    def __init__(self, captured, values):
        self._captured = captured
        self._values = values

    def spreadsheets(self):
        return self

    def values(self):
        return _FakeValues(self._captured, self._values)


def _patch_build(monkeypatch, captured, values):
    monkeypatch.setattr(
        sheets_client, "build", lambda *a, **k: _FakeService(captured, values)
    )


def test_fetch_rows_retries_transient_failures(monkeypatch):
    captured = {}
    _patch_build(monkeypatch, captured, [["a"]])

    rows = sheets_client.fetch_rows("sheet-1", "creds", "Cohorts!A:Z")

    assert rows == [["a"]]
    assert captured["num_retries"] == 2
    assert captured["sheet_id"] == "sheet-1"
    assert captured["range"] == "Cohorts!A:Z"


def test_fetch_rows_returns_empty_list_for_an_empty_tab(monkeypatch):
    captured = {}
    _patch_build(monkeypatch, captured, None)

    assert sheets_client.fetch_rows("sheet-1", "creds") == []
    assert captured["range"] == "A:Z"
