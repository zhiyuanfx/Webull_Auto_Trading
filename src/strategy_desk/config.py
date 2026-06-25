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
    webull_uat_app_key: str = ""
    webull_uat_app_secret: str = ""
    webull_live_enabled: bool = False
    webull_token_dir: Path = Path(".runtime/webull_tokens")
    webull_uat_token_wait_seconds: int = Field(default=15, ge=5, le=300)
    webull_prod_token_wait_seconds: int = Field(default=300, ge=30, le=300)

    strategy_desk_db_path: Path = Path("data/strategy_desk.db")
    strategy_desk_host: str = "127.0.0.1"
    strategy_desk_port: int = Field(default=8000, ge=1, le=65535)
    strategy_desk_log_level: str = "INFO"
    strategy_root: Path = Path("strategies")
    recording_root: Path = Path("recordings")

    @property
    def uat_configured(self) -> bool:
        return bool(self.webull_uat_app_key and self.webull_uat_app_secret)

    @property
    def production_configured(self) -> bool:
        return bool(self.webull_prod_app_key and self.webull_prod_app_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
