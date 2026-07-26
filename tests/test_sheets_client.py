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
