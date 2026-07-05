from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from webull_auto_trading.config import Settings

PROD_HTTP_HOST = "api.webull.com"


class WebullError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WebullTradingClient:
    """Small live-only boundary around the official Webull Python SDK."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None

    @property
    def configured(self) -> bool:
        return self.settings.production_configured

    def _trade_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:
            raise WebullError(
                "WEBULL_SDK_MISSING", "Install the webull extra in the Conda env"
            ) from exc
        if not self.configured:
            raise WebullError(
                "CREDENTIALS_MISSING", "Webull production credentials are not configured"
            )
        api_client = ApiClient(
            self.settings.webull_prod_app_key,
            self.settings.webull_prod_app_secret,
            self.settings.webull_region,
            connect_timeout=5,
            timeout=10,
            token_check_duration_seconds=self.settings.webull_prod_token_wait_seconds,
            token_check_interval_seconds=5,
        )
        api_client.add_endpoint(self.settings.webull_region, PROD_HTTP_HOST)
        token_directory = self.settings.webull_token_dir / "live"
        token_directory.mkdir(parents=True, exist_ok=True)
        api_client.set_token_dir(str(token_directory))
        api_client.set_stream_logger(stream=io.StringIO(), log_level=logging.CRITICAL)
        self._client = TradeClient(api_client)
        return self._client

    async def list_accounts(self) -> list[dict[str, Any]]:
        response = await self._sdk_response(self._trade_client().account_v2.get_account_list)
        return self._require_success(response)

    async def account_balance(self, account_id: str) -> dict[str, Any]:
        response = await self._sdk_response(
            self._trade_client().account_v2.get_account_balance, account_id
        )
        return self._require_success(response)

    async def positions(self, account_id: str) -> list[dict[str, Any]]:
        response = await self._sdk_response(
            self._trade_client().account_v2.get_account_position, account_id
        )
        return self._require_success(response)

    async def open_orders(self, account_id: str) -> list[dict[str, Any]]:
        response = await self._sdk_response(
            self._trade_client().order_v3.get_order_open, account_id, 100
        )
        return self._require_success(response)

    async def preview_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]:
        response = await self._sdk_response(
            self._trade_client().order_v3.preview_order, account_id, [order]
        )
        return self._require_success(response)

    async def place_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]:
        response = await self._sdk_response(
            self._trade_client().order_v3.place_order, account_id, [order]
        )
        return self._require_success(response)

    async def replace_order(self, account_id: str, change: dict[str, Any]) -> dict[str, Any]:
        response = await self._sdk_response(
            self._trade_client().order_v3.replace_order, account_id, [change]
        )
        return self._require_success(response)

    async def cancel_order(self, account_id: str, client_order_id: str) -> dict[str, Any]:
        response = await self._sdk_response(
            self._trade_client().order_v3.cancel_order, account_id, client_order_id
        )
        return self._require_success(response)

    async def _sdk_response(self, operation, *args):
        try:
            return await asyncio.to_thread(operation, *args)
        except WebullError:
            raise
        except Exception as exc:
            raise WebullError(type(exc).__name__, self.safe_error_message(exc)) from exc

    def safe_error_message(self, exc: Exception) -> str:
        message = str(exc) or type(exc).__name__
        for value in (self.settings.webull_prod_app_key, self.settings.webull_prod_app_secret):
            if value:
                message = message.replace(value, "<redacted>")
        return message[:500]

    @staticmethod
    def _require_success(response: Any) -> Any:
        if response.status_code == 200:
            return response.json()
        try:
            payload = response.json()
        except Exception:
            payload = {}
        raise WebullError(
            str(payload.get("error_code", f"HTTP_{response.status_code}")),
            str(payload.get("message", "Webull request failed")),
        )
