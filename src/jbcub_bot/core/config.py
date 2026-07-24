from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    link_secret: str
    rights_sheet_id: str  # spreadsheet holding the Cohorts and Rights tabs
    google_service_account_file: str
    database_url: str = "sqlite:///jbcub_bot.db"
    mapping_dir: str = "mapping"
    link_ttl_seconds: int = 86400
    # comma-separated Telegram ids that are always treated as Admin (bootstrap).
    bootstrap_admin_ids: str = ""
    cohorts_tab: str = "Cohorts"
    rights_tab: str = "Rights"
    rights_mapping: str = "rights.yaml"

    @property
    def bootstrap_admin_id_set(self) -> set[int]:
        return {int(x) for x in self.bootstrap_admin_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
