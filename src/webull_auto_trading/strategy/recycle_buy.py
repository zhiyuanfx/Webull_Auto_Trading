from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from webull_auto_trading.domain import (
    IntentType,
    OrderRole,
    OrderSide,
    QuoteState,
    StrategyInstance,
    new_id,
    utc_now,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.strategy.base import Strategy


@dataclass(slots=True)
class RecycleBuyParams:
    lots: float = 1.0
    stop_loss_distance_price: float = 5.0
    take_profit_distance_price: float = 5.0
    cooldown_seconds: int = 5

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecycleBuyParams:
        return cls(**data)

    def validate(self) -> None:
        if self.lots <= 0:
            raise ValueError("lots must be > 0")
        if self.stop_loss_distance_price <= 0:
            raise ValueError("stop_loss_distance_price must be > 0")
        if self.take_profit_distance_price <= 0:
            raise ValueError("take_profit_distance_price must be > 0")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")


@dataclass(slots=True)
class RecycleBuyState:
    active_cycle_id: str | None = None
    next_trade_time: datetime | None = None


class RecycleBuyStrategy(Strategy):
    def __init__(self) -> None:
        self.states: dict[str, RecycleBuyState] = {}

    def state_for(self, instance: StrategyInstance) -> RecycleBuyState:
        state = self.states.get(instance.id)
        if state is None:
            state = RecycleBuyState()
            self.states[instance.id] = state
        return state

    def on_quote(
        self,
        instance: StrategyInstance,
        quote: QuoteState,
        order_book: PaperOrderBook,
    ) -> list[str]:
        params = RecycleBuyParams.from_dict(instance.params)
        params.validate()
        state = self.state_for(instance)
        now = utc_now()
        messages: list[str] = []

        closed = order_book.manage_stops(quote)
        if closed:
            state.active_cycle_id = None
            state.next_trade_time = now + timedelta(seconds=params.cooldown_seconds)
            messages.append(IntentType.CLOSE_CYCLE.value)

        if order_book.open_for_instance(instance.id) or order_book.pending_for_instance(
            instance.id
        ):
            return messages
        if state.next_trade_time is not None and now < state.next_trade_time:
            return messages
        if quote.ask is None:
            return messages

        state.active_cycle_id = new_id("cyc")
        order_book.place_market_order(
            strategy_instance_id=instance.id,
            cycle_id=state.active_cycle_id,
            symbol=instance.symbol,
            side=OrderSide.BUY,
            role=OrderRole.MAIN,
            quantity=params.lots,
            fill_price=quote.ask,
            stop_loss=quote.ask - params.stop_loss_distance_price,
            take_profit=quote.ask + params.take_profit_distance_price,
        )
        messages.append(IntentType.PLACE_MARKET_ORDER.value)
        messages.append(IntentType.PAPER_FILL.value)
        return messages
