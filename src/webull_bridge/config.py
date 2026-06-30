from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    webull_region: str = "us"
    webull_prod_app_key: str = ""
    webull_prod_app_secret: str = ""
    webull_token_dir: Path = Path(".runtime/webull_tokens")
    webull_prod_token_wait_seconds: int = Field(default=300, ge=30, le=300)

    bridge_db_path: Path = Field(
        default=Path("data/webull_bridge.db"),
        validation_alias=AliasChoices("WEBULL_BRIDGE_DB_PATH", "STRATEGY_DESK_DB_PATH"),
    )
    bridge_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("WEBULL_BRIDGE_HOST", "STRATEGY_DESK_HOST"),
    )
    bridge_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("WEBULL_BRIDGE_PORT", "STRATEGY_DESK_PORT"),
    )
    bridge_log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("WEBULL_BRIDGE_LOG_LEVEL", "STRATEGY_DESK_LOG_LEVEL"),
    )
    bridge_execution_enabled: bool = False
    bridge_default_route_id: str = "tv"
    bridge_default_route_name: str = "TradingView"
    bridge_default_secret: str = ""
    webull_account_id: str = ""

    @property
    def production_configured(self) -> bool:
        return bool(self.webull_prod_app_key and self.webull_prod_app_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
