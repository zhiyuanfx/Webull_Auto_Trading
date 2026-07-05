from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Literal

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
from webull_auto_trading.risk import daily_loss_limit_hit
from webull_auto_trading.strategy.base import Strategy

EntryType = Literal["both", "buy_stop", "sell_stop", "none"]


@dataclass(slots=True)
class TradingWindow:
    start: time
    end: time

    def contains(self, now: datetime) -> bool:
        current = now.time()
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= current < self.end
        return current >= self.start or current < self.end


@dataclass(slots=True)
class DayManyBianParams:
    lots: float = 0.3
    bracket_buffer_price: float = 0.01
    reverse_buffer_price: float = 5.0
    stop_loss_distance_price: float = 7.0
    take_profit_distance_price: float = 15.0
    final_take_profit_distance_price: float = 1000.0
    trailing_stop_distance_price: float = 15.0
    use_dynamic_trailing_loss_distance: bool = True
    dynamic_trailing_step_price: float = 4.0
    dynamic_trailing_distance_reduction: float = 1.0
    min_dynamic_trailing_loss_distance: float = 7.0
    daily_loss_limit_percent: float = 10.0
    max_cycles_per_day: int = 5
    cooldown_seconds: int = 1
    use_hybrid_entry_strategy: bool = False
    enable_pyramiding: bool = False
    add_step_profit_price: float = 16.0
    max_add_orders: int = 10
    add_lot_multiplier: float = 1.0
    close_round_when_last_sl: bool = True
    trading_windows: list[TradingWindow] = field(
        default_factory=lambda: [TradingWindow(time(1, 0, 0), time(23, 55, 0))]
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DayManyBianParams:
        normalized = dict(data)
        windows = normalized.pop("trading_windows", None)
        params = cls(**{_snake_key(key): value for key, value in normalized.items()})
        if windows:
            params.trading_windows = [
                TradingWindow(_parse_time(item["start"]), _parse_time(item["end"]))
                for item in windows
            ]
        return params

    def validate(self) -> None:
        if self.reverse_buffer_price < 0:
            raise ValueError("reverse_buffer_price must be >= 0")
        if self.use_dynamic_trailing_loss_distance:
            if self.dynamic_trailing_step_price <= 0:
                raise ValueError("dynamic_trailing_step_price must be > 0")
            if self.dynamic_trailing_distance_reduction < 0:
                raise ValueError("dynamic_trailing_distance_reduction must be >= 0")
            if self.min_dynamic_trailing_loss_distance <= 0:
                raise ValueError("min_dynamic_trailing_loss_distance must be > 0")
            if self.min_dynamic_trailing_loss_distance > self.trailing_stop_distance_price:
                raise ValueError(
                    "min_dynamic_trailing_loss_distance cannot exceed trailing_stop_distance_price"
                )


@dataclass(slots=True)
class BracketSetup:
    entry_type: EntryType
    upper: float | None = None
    lower: float | None = None
    reason: str = ""


@dataclass(slots=True)
class DayManyBianState:
    day_start_equity: float = 100_000.0
    current_equity: float = 100_000.0
    current_day: datetime | None = None
    yesterday_high: float = 0.0
    yesterday_low: float = 0.0
    today_high: float = 0.0
    today_low: float = 0.0
    cycle_count_today: int = 0
    opened_cycle_today: bool = False
    next_trade_time: datetime | None = None
    daily_loss_limit_locked: bool = False
    active_cycle_id: str | None = None
    add_orders_opened: int = 0

    def reset_day(self, now: datetime, equity: float) -> None:
        self.current_day = now
        self.day_start_equity = equity
        self.current_equity = equity
        self.cycle_count_today = 0
        self.opened_cycle_today = False
        self.next_trade_time = None
        self.daily_loss_limit_locked = False
        self.active_cycle_id = None
        self.add_orders_opened = 0


class DayManyBianStrategy(Strategy):
    def __init__(self) -> None:
        self.states: dict[str, DayManyBianState] = {}

    def state_for(self, instance: StrategyInstance) -> DayManyBianState:
        state = self.states.get(instance.id)
        if state is None:
            state = DayManyBianState()
            self.states[instance.id] = state
        return state

    def on_quote(
        self,
        instance: StrategyInstance,
        quote: QuoteState,
        order_book: PaperOrderBook,
    ) -> list[str]:
        params = DayManyBianParams.from_dict(instance.params)
        params.validate()
        state = self.state_for(instance)
        now = utc_now()
        messages: list[str] = []

        if state.current_day is None:
            state.reset_day(now, state.current_equity)
        elif state.current_day.date() != now.date():
            state.reset_day(now, state.current_equity)
            order_book.cancel_pending(instance.id)
            messages.append("Daily state reset")

        if not any(window.contains(now) for window in params.trading_windows):
            order_book.cancel_pending(instance.id)
            messages.append("Outside trading window")
            return messages

        if daily_loss_limit_hit(
            day_start_equity=state.day_start_equity,
            current_equity=state.current_equity,
            daily_loss_limit_percent=params.daily_loss_limit_percent,
        ):
            state.daily_loss_limit_locked = True
            order_book.cancel_pending(instance.id)
            messages.append(IntentType.LOCK_INSTANCE.value)
            return messages

        closed = order_book.manage_stops(quote)
        if closed:
            state.cycle_count_today += 1
            state.next_trade_time = now + timedelta(seconds=params.cooldown_seconds)
            state.active_cycle_id = None
            state.add_orders_opened = 0
            messages.append(IntentType.CLOSE_CYCLE.value)

        fills = order_book.apply_quote(quote)
        if fills:
            state.opened_cycle_today = True
            for fill in fills:
                if fill.side == OrderSide.BUY:
                    order_book.cancel_pending(instance.id, side=OrderSide.SELL)
                else:
                    order_book.cancel_pending(instance.id, side=OrderSide.BUY)
            messages.append(IntentType.PAPER_FILL.value)

        self._manage_trailing(instance, quote, order_book, params)
        self._manage_pyramiding(instance, quote, order_book, params, state)

        has_open_or_pending = (
            order_book.open_for_instance(instance.id)
            or order_book.pending_for_instance(instance.id)
        )
        if has_open_or_pending:
            return messages
        if state.next_trade_time and now < state.next_trade_time:
            return messages
        if state.cycle_count_today >= params.max_cycles_per_day:
            return messages

        setup = select_fresh_entry_bracket(params, state, quote)
        if setup.entry_type == "none":
            return messages
        state.active_cycle_id = state.active_cycle_id or new_id("cyc")
        if setup.entry_type in ("both", "buy_stop") and setup.upper is not None:
            order_book.place_virtual_stop(
                strategy_instance_id=instance.id,
                cycle_id=state.active_cycle_id,
                symbol=instance.symbol,
                side=OrderSide.BUY,
                role=OrderRole.MAIN,
                quantity=params.lots,
                stop_price=setup.upper,
                stop_loss=setup.upper - params.stop_loss_distance_price,
                take_profit=setup.upper + params.final_take_profit_distance_price,
            )
        if setup.entry_type in ("both", "sell_stop") and setup.lower is not None:
            order_book.place_virtual_stop(
                strategy_instance_id=instance.id,
                cycle_id=state.active_cycle_id,
                symbol=instance.symbol,
                side=OrderSide.SELL,
                role=OrderRole.MAIN,
                quantity=params.lots,
                stop_price=setup.lower,
                stop_loss=setup.lower + params.stop_loss_distance_price,
                take_profit=setup.lower - params.final_take_profit_distance_price,
            )
        messages.append(IntentType.PLACE_VIRTUAL_STOP.value)
        return messages

    def _manage_trailing(
        self,
        instance: StrategyInstance,
        quote: QuoteState,
        order_book: PaperOrderBook,
        params: DayManyBianParams,
    ) -> None:
        if quote.bid is None or quote.ask is None:
            return
        for order in order_book.open_for_instance(instance.id):
            if order.fill_price is None:
                continue
            if order.side == OrderSide.BUY:
                profit_distance = quote.bid - order.fill_price
                if profit_distance < params.take_profit_distance_price:
                    continue
                distance = active_trailing_distance(
                    params,
                    OrderSide.BUY,
                    order.fill_price,
                    quote.bid,
                )
                order_book.move_stop(order, quote.bid - distance)
            else:
                profit_distance = order.fill_price - quote.ask
                if profit_distance < params.take_profit_distance_price:
                    continue
                distance = active_trailing_distance(
                    params,
                    OrderSide.SELL,
                    order.fill_price,
                    quote.ask,
                )
                order_book.move_stop(order, quote.ask + distance)

    def _manage_pyramiding(
        self,
        instance: StrategyInstance,
        quote: QuoteState,
        order_book: PaperOrderBook,
        params: DayManyBianParams,
        state: DayManyBianState,
    ) -> None:
        if not params.enable_pyramiding or params.add_step_profit_price <= 0:
            return
        open_orders = order_book.open_for_instance(instance.id)
        main = next((order for order in open_orders if order.role == OrderRole.MAIN), None)
        if main is None or main.fill_price is None:
            return
        add_count = len([order for order in open_orders if order.role == OrderRole.ADD_ON])
        if add_count >= params.max_add_orders:
            return
        next_add = add_count + 1
        if main.side == OrderSide.BUY:
            buy_trigger = main.fill_price + params.add_step_profit_price * next_add
            if quote.bid is None or quote.bid < buy_trigger:
                return
            price = quote.ask or quote.bid
            stop_loss = price - params.stop_loss_distance_price
            take_profit = price + params.final_take_profit_distance_price
        else:
            sell_trigger = main.fill_price - params.add_step_profit_price * next_add
            if quote.ask is None or quote.ask > sell_trigger:
                return
            price = quote.bid or quote.ask
            stop_loss = price + params.stop_loss_distance_price
            take_profit = price - params.final_take_profit_distance_price
        order = order_book.place_virtual_stop(
            strategy_instance_id=instance.id,
            cycle_id=main.cycle_id,
            symbol=instance.symbol,
            side=main.side,
            role=OrderRole.ADD_ON,
            quantity=params.lots * params.add_lot_multiplier,
            stop_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            parent_order_id=main.id,
        )
        order.metadata["intent"] = IntentType.OPEN_ADD_ON.value
        order_book.apply_quote(quote)
        state.add_orders_opened = next_add


def select_fresh_entry_bracket(
    params: DayManyBianParams,
    state: DayManyBianState,
    quote: QuoteState,
) -> BracketSetup:
    if quote.mid_price is None:
        return BracketSetup("none", reason="quote is missing bid/ask")
    if state.yesterday_high <= 0 or state.yesterday_low <= 0:
        return BracketSetup("none", reason="daily bracket unavailable")
    if state.yesterday_high <= state.yesterday_low:
        return BracketSetup("none", reason="invalid daily bracket")
    if state.cycle_count_today > 0 or state.opened_cycle_today:
        return _select_reverse_buffer_entry_bracket(params, state, quote)
    return _select_original_entry_bracket(params, state, quote)


def _select_original_entry_bracket(
    params: DayManyBianParams,
    state: DayManyBianState,
    quote: QuoteState,
) -> BracketSetup:
    mid = quote.mid_price
    assert mid is not None
    if not params.use_hybrid_entry_strategy:
        return BracketSetup(
            "both",
            upper=state.yesterday_high + params.bracket_buffer_price,
            lower=state.yesterday_low - params.bracket_buffer_price,
        )
    if state.yesterday_low < mid < state.yesterday_high:
        return BracketSetup(
            "both",
            upper=state.yesterday_high + params.bracket_buffer_price,
            lower=state.yesterday_low - params.bracket_buffer_price,
        )
    if state.today_high <= 0 or state.today_low <= 0 or state.today_high <= state.today_low:
        return BracketSetup("none", reason="today range unavailable")
    if mid >= state.yesterday_high:
        return BracketSetup("buy_stop", upper=state.today_high + params.bracket_buffer_price)
    if mid <= state.yesterday_low:
        return BracketSetup("sell_stop", lower=state.today_low - params.bracket_buffer_price)
    return BracketSetup("none")


def _select_reverse_buffer_entry_bracket(
    params: DayManyBianParams,
    state: DayManyBianState,
    quote: QuoteState,
) -> BracketSetup:
    mid = quote.mid_price
    assert mid is not None
    yesterday_range = state.yesterday_high - state.yesterday_low
    if 2.0 * params.reverse_buffer_price >= yesterday_range:
        return BracketSetup("none", reason="reverse buffer makes middle zone invalid")
    middle_low = state.yesterday_low + params.reverse_buffer_price
    middle_high = state.yesterday_high - params.reverse_buffer_price
    if middle_low <= mid <= middle_high:
        return BracketSetup(
            "both",
            upper=state.yesterday_high + params.bracket_buffer_price,
            lower=state.yesterday_low - params.bracket_buffer_price,
        )
    if not params.use_hybrid_entry_strategy:
        return BracketSetup("none", reason="outside reverse-buffer middle zone")
    if state.today_low > 0 and state.today_low < state.yesterday_low:
        lower_hybrid_low = state.today_low + params.reverse_buffer_price
        lower_hybrid_high = state.yesterday_low + params.reverse_buffer_price
        if lower_hybrid_low <= mid < lower_hybrid_high:
            return BracketSetup(
                "sell_stop",
                lower=state.today_low - params.bracket_buffer_price,
            )
    if state.today_high > 0 and state.today_high > state.yesterday_high:
        upper_hybrid_low = state.yesterday_high - params.reverse_buffer_price
        upper_hybrid_high = state.today_high - params.reverse_buffer_price
        if upper_hybrid_low < mid <= upper_hybrid_high:
            return BracketSetup(
                "buy_stop",
                upper=state.today_high + params.bracket_buffer_price,
            )
    return BracketSetup("none", reason="outside reverse-buffer hybrid zones")


def active_trailing_distance(
    params: DayManyBianParams,
    side: OrderSide,
    open_price: float,
    current_price: float,
) -> float:
    if not params.use_dynamic_trailing_loss_distance:
        return params.trailing_stop_distance_price
    if side == OrderSide.BUY:
        trailing_start = open_price + params.take_profit_distance_price
        favorable_move = current_price - trailing_start
    else:
        trailing_start = open_price - params.take_profit_distance_price
        favorable_move = trailing_start - current_price
    if favorable_move <= 0:
        return params.trailing_stop_distance_price
    steps = int(favorable_move // params.dynamic_trailing_step_price)
    distance = (
        params.trailing_stop_distance_price
        - steps * params.dynamic_trailing_distance_reduction
    )
    return max(distance, params.min_dynamic_trailing_loss_distance)


def _snake_key(key: str) -> str:
    output = []
    for char in key:
        if char.isupper() and output:
            output.append("_")
        output.append(char.lower())
    return "".join(output)


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)
