from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from strategy_desk.domain import (
    AssetClass,
    OrderCommand,
    OrderOrigin,
    OrderTicket,
    OrderType,
    RiskLimits,
    Side,
    TimeInForce,
)
from strategy_desk.gateway import BrokerAdapter, ExecutionGateway
from strategy_desk.schedule import TradingSchedule


class OrderSession:
    """Credential-free, strategy-local logical order API."""

    def __init__(
        self,
        strategy_instance_id: str,
        account_id: str,
        gateway: ExecutionGateway,
        risk: RiskLimits | None = None,
        schedule: TradingSchedule | None = None,
        adapter: BrokerAdapter | None = None,
    ) -> None:
        self.strategy_instance_id = strategy_instance_id
        self.account_id = account_id
        self.gateway = gateway
        self._events = gateway.register_strategy(
            strategy_instance_id, risk=risk, schedule=schedule, adapter=adapter
        )

    async def place(
        self,
        *,
        symbol: str,
        asset_class: AssetClass,
        side: Side,
        quantity: Decimal,
        order_type: OrderType,
        time_in_force: TimeInForce = TimeInForce.DAY,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        origin: OrderOrigin = OrderOrigin.STRATEGY,
        client_order_id: str | None = None,
    ) -> OrderTicket:
        values = {
            "strategy_instance_id": self.strategy_instance_id,
            "account_id": self.account_id,
            "symbol": symbol,
            "asset_class": asset_class,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "limit_price": limit_price,
            "stop_price": stop_price,
            "origin": origin,
        }
        if client_order_id:
            values["client_order_id"] = client_order_id
        return await self.gateway.place(OrderCommand(**values))

    async def replace(self, ticket_id: str, *, limit_price: Decimal | None) -> OrderTicket:
        return await self.gateway.replace(self.strategy_instance_id, ticket_id, limit_price)

    async def cancel(self, ticket_id: str) -> OrderTicket:
        return await self.gateway.cancel(self.strategy_instance_id, ticket_id)

    def tickets(self) -> list[OrderTicket]:
        return self.gateway.ledger.tickets_for_strategy(self.strategy_instance_id)

    async def events(self) -> AsyncIterator[OrderTicket]:
        while True:
            yield await self._events.get()
