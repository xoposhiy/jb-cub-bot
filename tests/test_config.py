import pytest
from sdt_bot.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    s = Settings()
    assert s.bot_token == "123:abc"
    assert s.link_secret == "s3cret"
    assert s.database_url == "sqlite:///sdt_bot.db"  # default
    assert s.link_ttl_seconds == 86400  # default
    assert s.cohorts_tab == "Cohorts"  # default
    assert s.rights_tab == "Rights"  # default


def test_bootstrap_admin_id_set_parsed(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_IDS", "111, 222 ,333")
    s = Settings()
    assert s.bootstrap_admin_id_set == {111, 222, 333}


def test_bootstrap_admin_id_set_empty_by_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_IDS", raising=False)
    assert Settings().bootstrap_admin_id_set == set()


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("LINK_SECRET", raising=False)
    monkeypatch.delenv("RIGHTS_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    with pytest.raises(Exception):
        Settings()
