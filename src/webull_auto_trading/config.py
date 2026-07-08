from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    _env_file_disabled: bool = PrivateAttr(default=False)

    webull_region: str = "us"
    webull_prod_app_key: str = ""
    webull_prod_app_secret: str = ""
    webull_token_dir: Path = Path(".runtime/webull_tokens")
    webull_prod_token_wait_seconds: int = Field(default=300, ge=30, le=300)
    webull_account_id: str = ""
    webull_account_default_alias: str = ""
    webull_account_stock_cash_id: str = ""
    webull_account_stock_margin_id: str = ""
    webull_account_futures_id: str = ""
    live_execution_master_enable: bool = False
    runtime_db_path: Path = Path(".runtime/webull_auto_trading.sqlite3")
    strategies_test_config_path: Path = Path("config/strategies.test.yml")
    strategies_live_config_path: Path = Path("config/strategies.live.yml")
    quote_max_staleness_seconds: int = Field(default=30, ge=1, le=3600)
    allow_delayed_quotes: bool = False
    insightsentry_stream_enabled: bool = True
    insightsentry_api_key: str = ""
    insightsentry_rapidapi_key: str = ""
    insightsentry_rapidapi_host: str = "insightsentry.p.rapidapi.com"
    insightsentry_websocket_key: str = ""
    insightsentry_websocket_key_expiration: str = ""

    def __init__(self, **data: Any) -> None:
        env_file_disabled = data.get("_env_file") is None and "_env_file" in data
        super().__init__(**data)
        self._env_file_disabled = env_file_disabled

    @property
    def production_configured(self) -> bool:
        return bool(self.webull_prod_app_key and self.webull_prod_app_secret)

    def webull_account_aliases(self) -> dict[str, str]:
        aliases = {
            "stock_cash": self.webull_account_stock_cash_id,
            "stock_margin": self.webull_account_stock_margin_id,
            "futures": self.webull_account_futures_id,
        }
        aliases.update(self._dynamic_webull_account_aliases())
        return {alias: account_id for alias, account_id in aliases.items() if account_id}

    def resolve_webull_account_alias(self, alias: str) -> str:
        aliases = self.webull_account_aliases()
        if alias:
            return aliases.get(alias, "")
        if self.webull_account_default_alias:
            return aliases.get(self.webull_account_default_alias, "") or self.webull_account_id
        return self.webull_account_id

    def _dynamic_webull_account_aliases(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if not self._env_file_disabled:
            try:
                from dotenv import dotenv_values
            except ImportError:
                dotenv_values = None
            if dotenv_values is not None:
                for key, value in dotenv_values(".env").items():
                    if value is not None:
                        values[key] = value
        values.update(os.environ)

        aliases: dict[str, str] = {}
        pattern = re.compile(r"^WEBULL_ACCOUNT_(?P<alias>[A-Z0-9_]+)_ID$")
        for key, value in values.items():
            if key == "WEBULL_ACCOUNT_ID":
                continue
            match = pattern.match(key)
            if not match or not value:
                continue
            alias = match.group("alias").lower()
            aliases[alias] = str(value)
        return aliases


@lru_cache
def get_settings() -> Settings:
    return Settings()
