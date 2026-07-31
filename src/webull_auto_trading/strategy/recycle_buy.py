from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from webull_auto_trading.domain import (
    IntentType,
    LiveAccountState,
    OrderRole,
    OrderSide,
    OrderStatus,
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
    account_observed_at: datetime | None = None
    previews_ready: bool = False
    reconciliation_ready: bool = False
    reconciliation_error: str = ""


class RecycleBuyStrategy(Strategy):
    def __init__(self) -> None:
        self.states: dict[str, RecycleBuyState] = {}

    def state_for(self, instance: StrategyInstance) -> RecycleBuyState:
        state = self.states.get(instance.id)
        if state is None:
            state = RecycleBuyState()
            self.states[instance.id] = state
        return state

    def on_account_snapshot(
        self,
        instance: StrategyInstance,
        account: LiveAccountState,
        order_book: PaperOrderBook,
    ) -> list[str]:
        del order_book
        state = self.state_for(instance)
        state.account_observed_at = account.observed_at
        state.previews_ready = account.previews_ready
        state.reconciliation_ready = account.reconciliation_ready
        state.reconciliation_error = account.reconciliation_error
        return []

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

        closed = order_book.manage_stops(
            quote,
            strategy_instance_id=instance.id,
        )
        if closed:
            state.active_cycle_id = None
            state.next_trade_time = now + timedelta(seconds=params.cooldown_seconds)
            messages.append(IntentType.CLOSE_CYCLE.value)

        active = [
            order
            for order in order_book.orders
            if order.strategy_instance_id == instance.id
            and order.status
            in {
                OrderStatus.PENDING,
                OrderStatus.OPENING,
                OrderStatus.FILLED,
                OrderStatus.OPEN,
                OrderStatus.CLOSING,
            }
        ]
        if active:
            state.active_cycle_id = active[0].cycle_id
            return messages
        state.active_cycle_id = None
        if state.next_trade_time is None:
            latest_closed = max(
                (
                    order
                    for order in order_book.orders
                    if order.strategy_instance_id == instance.id
                    and order.status == OrderStatus.CLOSED
                    and order.closed_at is not None
                    and not order.metadata.get("state_reset_terminal")
                ),
                key=lambda order: order.closed_at,
                default=None,
            )
            if latest_closed is not None and latest_closed.closed_at is not None:
                state.next_trade_time = latest_closed.closed_at + timedelta(
                    seconds=params.cooldown_seconds
                )
        if state.next_trade_time is not None and now < state.next_trade_time:
            return messages
        if instance.live_execution_enabled and not self._live_entry_ready(state, now):
            return messages
        if quote.ask is None:
            return messages

        state.active_cycle_id = new_id("cyc")
        order = order_book.place_market_order(
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
        order.metadata.update(
            {
                "stop_loss_distance_price": params.stop_loss_distance_price,
                "take_profit_distance_price": params.take_profit_distance_price,
            }
        )
        messages.append(IntentType.PLACE_MARKET_ORDER.value)
        messages.append(IntentType.PAPER_FILL.value)
        return messages

    def export_state(self, instance: StrategyInstance) -> dict[str, Any]:
        state = self.state_for(instance)
        return {
            "active_cycle_id": state.active_cycle_id,
            "next_trade_time": (
                state.next_trade_time.isoformat()
                if state.next_trade_time is not None
                else None
            ),
        }

    def import_state(self, instance: StrategyInstance, state: dict[str, Any]) -> None:
        next_trade_time = state.get("next_trade_time")
        parsed_next_trade_time = (
            datetime.fromisoformat(next_trade_time)
            if isinstance(next_trade_time, str) and next_trade_time
            else None
        )
        self.states[instance.id] = RecycleBuyState(
            active_cycle_id=(
                str(state["active_cycle_id"])
                if state.get("active_cycle_id")
                else None
            ),
            next_trade_time=parsed_next_trade_time,
        )

    def reset_state(self, instance: StrategyInstance) -> None:
        self.states[instance.id] = RecycleBuyState()

    @staticmethod
    def _live_entry_ready(state: RecycleBuyState, now: datetime) -> bool:
        observed_at = state.account_observed_at
        if observed_at is None or now - observed_at > timedelta(seconds=15):
            return False
        return (
            state.previews_ready
            and state.reconciliation_ready
            and not state.reconciliation_error
        )
