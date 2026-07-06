from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    webull_region: str = "us"
    webull_prod_app_key: str = ""
    webull_prod_app_secret: str = ""
    webull_token_dir: Path = Path(".runtime/webull_tokens")
    webull_prod_token_wait_seconds: int = Field(default=300, ge=30, le=300)
    webull_account_id: str = ""
    runtime_db_path: Path = Path(".runtime/webull_auto_trading.sqlite3")
    strategies_config_path: Path = Path("config/strategies.yml")
    strategies_test_config_path: Path = Path("config/strategies.test.yml")
    strategies_live_config_path: Path = Path("config/strategies.live.yml")
    quote_max_staleness_seconds: int = Field(default=30, ge=1, le=3600)
    allow_delayed_quotes: bool = False
    insightsentry_api_key: str = ""
    insightsentry_rapidapi_key: str = ""
    insightsentry_rapidapi_host: str = "insightsentry.p.rapidapi.com"
    insightsentry_websocket_key: str = ""
    insightsentry_websocket_key_expiration: str = ""

    @property
    def production_configured(self) -> bool:
        return bool(self.webull_prod_app_key and self.webull_prod_app_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
