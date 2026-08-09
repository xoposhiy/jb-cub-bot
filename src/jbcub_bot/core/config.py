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
    # Knowledge base search. The API key turns the feature on; an empty base
    # URL means OpenAI's own host. Point it at any OpenAI-compatible endpoint
    # (a LiteLLM proxy, say) and name the model that endpoint routes.
    kb_llm_api_key: str = ""
    kb_llm_base_url: str = ""
    kb_llm_model: str = "gpt-5.6-luna"
    # Lowest setting this endpoint accepts: it rejects the "minimal" step below
    # this one. Also why the agent uses the Responses API -- with reasoning on
    # at all, chat completions here refuses function tools. Empty omits the
    # parameter, for a gateway whose model has no such notion.
    kb_llm_reasoning_effort: str = "low"
    kb_repo: str = "xoposhiy/cub-kb"
    kb_ttl_seconds: int = 3600
    # A brake on the agent as a whole, not on any one asker -- see
    # kb.handlers._budget_spent. Set far above what normal use reaches, so
    # tripping it is itself the signal something is off.
    kb_rate_limit: int = 100
    kb_rate_window_seconds: int = 3600
    # Optional, and only about quota: unauthenticated GitHub API calls are
    # rationed per IP, and a host shares one outbound address between tenants.
    # Any token buys a bucket of our own; a public repo needs no permissions.
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
