import pytest
from jbcub_bot.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    s = Settings(_env_file=None)  # ignore the developer's real .env
    assert s.bot_token == "123:abc"
    assert s.link_secret == "s3cret"
    assert s.database_url == "sqlite:///jbcub_bot.db"  # default
    assert s.link_ttl_seconds == 86400  # default
    assert s.cohorts_tab == "Cohorts"  # default
    assert s.rights_tab == "Rights"  # default
    assert s.gradebook_tab == "Gradebook"  # default


def test_bootstrap_admin_id_set_parsed(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_IDS", "111, 222 ,333")
    s = Settings(_env_file=None)  # ignore the developer's real .env
    assert s.bootstrap_admin_id_set == {111, 222, 333}


def test_bootstrap_admin_id_set_empty_by_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_IDS", raising=False)
    assert Settings(_env_file=None).bootstrap_admin_id_set == set()


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("LINK_SECRET", raising=False)
    monkeypatch.delenv("RIGHTS_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)  # ignore the developer's real .env


def test_log_chat_id_is_empty_by_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.delenv("LOG_CHAT_ID", raising=False)
    assert Settings(_env_file=None).log_chat_id == ""


def test_service_account_fields_default_to_empty(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    s = Settings(_env_file=None)  # ignore the developer's real .env
    assert s.google_service_account_file == ""
    assert s.google_service_account_json == ""


def _base_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    for name in ("KB_LLM_BASE_URL", "KB_LLM_API_KEY", "KB_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_kb_settings_default_to_unconfigured(monkeypatch):
    _base_env(monkeypatch)
    s = Settings(_env_file=None)  # ignore the developer's real .env
    assert s.kb_llm_api_key == ""
    assert s.kb_llm_base_url == ""  # empty means OpenAI's own host
    assert s.kb_llm_model == "gpt-5.6-luna"
    assert s.kb_repo == "xoposhiy/cub-kb"
    assert s.kb_ttl_seconds == 3600
    assert s.kb_github_token == ""  # optional: quota only, never access
    assert s.kb_configured is False


def test_kb_configured_needs_only_the_api_key(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("KB_LLM_API_KEY", "sk-openai")
    assert Settings(_env_file=None).kb_configured is True


def test_kb_endpoint_and_model_are_overridable(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("KB_LLM_API_KEY", "sk-litellm")
    monkeypatch.setenv("KB_LLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("KB_LLM_MODEL", "kb-agent")
    s = Settings(_env_file=None)
    assert s.kb_llm_base_url == "http://localhost:4000"
    assert s.kb_llm_model == "kb-agent"
