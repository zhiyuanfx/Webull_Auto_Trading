from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from webull_auto_trading.domain import Bar, LiveAccountState, QuoteState, StrategyInstance
from webull_auto_trading.order_manager import PaperOrderBook


class Strategy(ABC):
    def subscription_requirements(
        self,
        instance: StrategyInstance,
    ) -> list[dict[str, Any]]:
        """Return this strategy's complete market-data subscription requirements."""
        return [{"code": instance.market_data_symbol or instance.symbol, "type": "quote"}]

    def on_series(
        self,
        instance: StrategyInstance,
        bars: list[Bar],
        order_book: PaperOrderBook,
    ) -> list[str]:
        """Consume complete replacement series state. The default is intentionally inert."""
        return []

    def on_account_snapshot(
        self,
        instance: StrategyInstance,
        account: LiveAccountState,
        order_book: PaperOrderBook,
    ) -> list[str]:
        """Consume a live account snapshot. Existing paper strategies need no account data."""
        return []

    def on_timer(
        self,
        instance: StrategyInstance,
        now: datetime,
        quote: QuoteState | None,
        order_book: PaperOrderBook,
    ) -> list[str]:
        """Evaluate time-based safety behavior independently of quote arrival."""
        return []

    def export_state(self, instance: StrategyInstance) -> dict[str, Any]:
        """Export durable control state. Market quotes and OHLC bars must not be returned."""
        return {}

    def import_state(self, instance: StrategyInstance, state: dict[str, Any]) -> None:
        """Restore durable control state. The default keeps existing strategies stateless."""
        return None

    @abstractmethod
    def on_quote(
        self,
        instance: StrategyInstance,
        quote: QuoteState,
        order_book: PaperOrderBook,
    ) -> list[str]:
        """Evaluate one quote and return human-readable activity messages."""
