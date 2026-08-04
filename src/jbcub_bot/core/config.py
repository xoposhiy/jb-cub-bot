from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    link_secret: str
    rights_sheet_id: str  # spreadsheet holding the Cohorts and Rights tabs
    # Exactly one of these is supplied. Inline JSON is for hosts that can only
    # pass secrets as env vars (Railway); the file path is for local dev.
    google_service_account_file: str = ""
    google_service_account_json: str = ""
    database_url: str = "sqlite:///jbcub_bot.db"
    link_ttl_seconds: int = 86400
    # comma-separated Telegram ids that are always treated as Admin (bootstrap).
    bootstrap_admin_ids: str = ""
    cohorts_tab: str = "Cohorts"
    rights_tab: str = "Rights"
    gradebook_tab: str = "Gradebook"
    # Chat that receives crash reports and unanswered requests. A channel id
    # looks like -100…, so this is a str; empty means report to the bootstrap
    # admins' DMs instead.
    log_chat_id: str = ""
    # Knowledge base search. The API key is what turns the feature on; the rest
    # defaults to plain OpenAI. Point the base URL at any OpenAI-compatible
    # endpoint (a LiteLLM proxy, say) to route elsewhere, and name the model
    # that endpoint routes — empty means OpenAI's own host and small model.
    kb_llm_api_key: str = ""
    kb_llm_base_url: str = ""
    kb_llm_model: str = "gpt-5.6-luna"
    # OpenAI's small models refuse function tools on chat completions unless
    # reasoning is off, and this agent is nothing but function tools. Empty
    # omits the parameter for a gateway whose model does not understand it.
    kb_llm_reasoning_effort: str = "none"
    kb_repo: str = "xoposhiy/cub-kb"
    kb_ttl_seconds: int = 3600
    # Optional, and only about quota: GitHub's REST API allows 60 calls an hour
    # per IP unauthenticated, and a host shares one outbound address between
    # tenants. Any token raises that to 5000 in a bucket of our own; a public
    # knowledge base needs no permissions on it.
    kb_github_token: str = ""

    @property
    def kb_configured(self) -> bool:
        return bool(self.kb_llm_api_key)

    @property
    def bootstrap_admin_id_set(self) -> set[int]:
        return {int(x) for x in self.bootstrap_admin_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
