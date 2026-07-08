from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webull_auto_trading.config import Settings
from webull_auto_trading.webull import WebullError, WebullTradingClient


@dataclass(slots=True)
class AccountSnapshot:
    configured: bool
    account_id: str
    balance: dict[str, Any] | None = None
    positions: list[dict[str, Any]] | None = None
    open_orders: list[dict[str, Any]] | None = None
    error: str | None = None


@dataclass(slots=True)
class LiveOrdersSnapshot:
    configured: bool
    account_id: str
    open_orders: list[dict[str, Any]]
    order_history: list[dict[str, Any]]
    error: str | None = None


class WebullReadService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = WebullTradingClient(settings)

    async def snapshot(self, account_id: str | None = None) -> AccountSnapshot:
        resolved_account_id = account_id or self.settings.webull_account_id
        if not self.client.configured:
            return AccountSnapshot(False, resolved_account_id, error="Webull credentials missing")
        if not resolved_account_id:
            return AccountSnapshot(True, "", error="WEBULL_ACCOUNT_ID is not configured")
        try:
            balance = await self.client.account_balance(resolved_account_id)
            positions = await self.client.positions(resolved_account_id)
            open_orders = await self.client.open_orders(resolved_account_id)
        except WebullError as exc:
            return AccountSnapshot(True, resolved_account_id, error=exc.message)
        return AccountSnapshot(
            configured=True,
            account_id=resolved_account_id,
            balance=balance,
            positions=positions,
            open_orders=open_orders,
        )

    async def live_orders(self, account_id: str | None = None) -> LiveOrdersSnapshot:
        resolved_account_id = account_id or self.settings.webull_account_id
        if not self.client.configured:
            return LiveOrdersSnapshot(
                configured=False,
                account_id=resolved_account_id,
                open_orders=[],
                order_history=[],
                error="Webull credentials missing",
            )
        if not resolved_account_id:
            return LiveOrdersSnapshot(
                configured=True,
                account_id="",
                open_orders=[],
                order_history=[],
                error="WEBULL_ACCOUNT_ID is not configured",
            )
        errors: list[str] = []
        try:
            open_orders = await self.client.open_orders(resolved_account_id)
        except WebullError as exc:
            open_orders = []
            errors.append(exc.message)
        try:
            order_history = await self.client.order_history(resolved_account_id)
        except WebullError as exc:
            order_history = []
            errors.append(exc.message)
        return LiveOrdersSnapshot(
            configured=True,
            account_id=resolved_account_id,
            open_orders=open_orders,
            order_history=order_history,
            error="; ".join(errors) or None,
        )
