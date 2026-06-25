from __future__ import annotations

from decimal import Decimal

from strategy_desk.domain import AssetClass, OrderType, Side
from strategy_desk.schedule import TradingSchedule
from strategy_desk.strategy import BaseStrategy


class Strategy(BaseStrategy):
    trading_schedule = TradingSchedule.from_pairs([("08:00", "11:00")], timezone="America/New_York")

    def __init__(self, parameters=None):
        self.parameters = parameters or {}
        self.context = None
        self.ticket_id = None

    async def on_start(self, context):
        self.context = context
        await context.log(
            "INFO", "Example strategy started", enabled=self.parameters.get("enabled", False)
        )

    async def on_quote(self, event):
        # This deliberately does nothing unless explicitly enabled in instance parameters.
        if not self.parameters.get("enabled", False) or self.ticket_id is not None:
            return
        bid = Decimal(str(event["bid"]))
        ask = Decimal(str(event["ask"]))
        if ask <= bid:
            return
        asset = (
            AssetClass.FUTURES
            if any(character.isdigit() for character in event["symbol"])
            else AssetClass.EQUITY
        )
        ticket = await self.context.orders.place(
            symbol=event["symbol"],
            asset_class=asset,
            side=Side.BUY,
            quantity=Decimal(str(self.parameters.get("quantity", "1"))),
            order_type=OrderType.LIMIT,
            limit_price=bid,
        )
        self.ticket_id = ticket["id"]

    async def on_order_update(self, event):
        if event["status"] in {"FILLED", "CANCELLED", "FAILED", "REJECTED"}:
            self.ticket_id = None

    async def on_stop(self, reason):
        if self.context:
            await self.context.log("INFO", "Example strategy stopped", reason=reason)
