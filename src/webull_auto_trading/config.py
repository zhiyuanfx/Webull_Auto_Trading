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

    @property
    def production_configured(self) -> bool:
        return bool(self.webull_prod_app_key and self.webull_prod_app_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()

