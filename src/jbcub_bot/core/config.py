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
    # Knowledge base search. All three of base URL, key and model must be set
    # for the feature to work, and none has a default: an unset base URL would
    # send staff questions to the OpenAI client's own default host, and behind a
    # proxy a model name is an alias of that one deployment, so there is nothing
    # honest to guess.
    kb_base_url: str = ""
    kb_api_key: str = ""
    kb_model: str = ""
    kb_repo: str = "xoposhiy/cub-kb"
    kb_ttl_seconds: int = 3600

    @property
    def kb_configured(self) -> bool:
        return bool(self.kb_base_url and self.kb_api_key and self.kb_model)

    @property
    def bootstrap_admin_id_set(self) -> set[int]:
        return {int(x) for x in self.bootstrap_admin_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
