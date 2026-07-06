from __future__ import annotations

from dataclasses import dataclass, field

from webull_auto_trading.domain import (
    IntentType,
    OrderRole,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
    QuoteState,
    new_id,
    utc_now,
)


@dataclass(slots=True)
class PaperOrderBook:
    orders: list[PaperOrder] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)

    def pending_for_instance(self, strategy_instance_id: str) -> list[PaperOrder]:
        return [
            order
            for order in self.orders
            if order.strategy_instance_id == strategy_instance_id
            and order.status == OrderStatus.PENDING
        ]

    def open_for_instance(self, strategy_instance_id: str) -> list[PaperOrder]:
        return [
            order
            for order in self.orders
            if order.strategy_instance_id == strategy_instance_id
            and order.status == OrderStatus.FILLED
        ]

    def place_virtual_stop(
        self,
        *,
        strategy_instance_id: str,
        cycle_id: str,
        symbol: str,
        side: OrderSide,
        role: OrderRole,
        quantity: float,
        stop_price: float,
        stop_loss: float,
        take_profit: float,
        parent_order_id: str | None = None,
    ) -> PaperOrder:
        order = PaperOrder(
            id=new_id("ord"),
            strategy_instance_id=strategy_instance_id,
            cycle_id=cycle_id,
            symbol=symbol,
            side=side,
            role=role,
            quantity=quantity,
            stop_price=stop_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            parent_order_id=parent_order_id,
            metadata={"intent": IntentType.PLACE_VIRTUAL_STOP.value},
        )
        self.orders.append(order)
        return order

    def cancel_pending(self, strategy_instance_id: str, *, side: OrderSide | None = None) -> int:
        count = 0
        for order in self.pending_for_instance(strategy_instance_id):
            if side is not None and order.side != side:
                continue
            order.status = OrderStatus.CANCELLED
            order.closed_at = utc_now()
            order.metadata["intent"] = IntentType.CANCEL_VIRTUAL_ORDER.value
            count += 1
        return count

    def apply_quote(self, quote: QuoteState) -> list[PaperFill]:
        fills: list[PaperFill] = []
        if quote.bid is None or quote.ask is None:
            return fills
        for order in self.orders:
            if order.symbol != quote.symbol or order.status != OrderStatus.PENDING:
                continue
            if order.stop_price is None:
                continue
            fill_price: float | None = None
            if order.side == OrderSide.BUY and quote.ask >= order.stop_price:
                fill_price = order.stop_price
            elif order.side == OrderSide.SELL and quote.bid <= order.stop_price:
                fill_price = order.stop_price
            if fill_price is None:
                continue
            order.status = OrderStatus.FILLED
            order.fill_price = fill_price
            order.opened_at = utc_now()
            order.metadata["intent"] = IntentType.PAPER_FILL.value
            fill = PaperFill(
                id=new_id("fill"),
                order_id=order.id,
                strategy_instance_id=order.strategy_instance_id,
                cycle_id=order.cycle_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=fill_price,
            )
            self.fills.append(fill)
            fills.append(fill)
        return fills

    def manage_stops(self, quote: QuoteState) -> list[PaperOrder]:
        closed: list[PaperOrder] = []
        if quote.bid is None or quote.ask is None:
            return closed
        for order in self.orders:
            if order.symbol != quote.symbol or order.status != OrderStatus.FILLED:
                continue
            if order.stop_loss is None:
                continue
            stop_hit = (
                order.side == OrderSide.BUY
                and quote.bid <= order.stop_loss
                or order.side == OrderSide.SELL
                and quote.ask >= order.stop_loss
            )
            take_profit_hit = False
            if order.take_profit is not None:
                take_profit_hit = (
                    order.side == OrderSide.BUY
                    and quote.bid >= order.take_profit
                    or order.side == OrderSide.SELL
                    and quote.ask <= order.take_profit
                )
            if stop_hit or take_profit_hit:
                order.status = OrderStatus.CLOSED
                order.closed_at = utc_now()
                order.metadata["close_reason"] = "stop_loss" if stop_hit else "take_profit"
                order.metadata["close_price"] = order.stop_loss if stop_hit else order.take_profit
                closed.append(order)
        return closed

    def move_stop(self, order: PaperOrder, new_stop_loss: float) -> bool:
        if order.status != OrderStatus.FILLED:
            return False
        if order.side == OrderSide.BUY:
            if order.stop_loss is not None and new_stop_loss <= order.stop_loss:
                return False
        elif order.stop_loss is not None and new_stop_loss >= order.stop_loss:
            return False
        order.stop_loss = new_stop_loss
        order.metadata["intent"] = IntentType.MOVE_STOP.value
        return True
