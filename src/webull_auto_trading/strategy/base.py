from __future__ import annotations

from abc import ABC, abstractmethod

from webull_auto_trading.domain import QuoteState, StrategyInstance
from webull_auto_trading.order_manager import PaperOrderBook


class Strategy(ABC):
    @abstractmethod
    def on_quote(
        self,
        instance: StrategyInstance,
        quote: QuoteState,
        order_book: PaperOrderBook,
    ) -> list[str]:
        """Evaluate one quote and return human-readable activity messages."""
