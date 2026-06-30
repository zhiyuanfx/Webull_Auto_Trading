from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from webull_bridge.domain import (
    Action,
    BridgeError,
    BridgePayload,
    EventStatus,
    InstrumentType,
    OrderType,
    RouteConfig,
    make_client_order_id,
)
from webull_bridge.persistence import Ledger
from webull_bridge.webull import WebullError


class TradingClient(Protocol):
    configured: bool

    async def preview_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]: ...
    async def place_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]: ...
    async def replace_order(self, account_id: str, change: dict[str, Any]) -> dict[str, Any]: ...
    async def cancel_order(self, account_id: str, client_order_id: str) -> dict[str, Any]: ...
    async def positions(self, account_id: str) -> list[dict[str, Any]]: ...
    async def open_orders(self, account_id: str) -> list[dict[str, Any]]: ...
    async def list_accounts(self) -> list[dict[str, Any]]: ...


class BridgeExecutor:
    def __init__(self, ledger: Ledger, client: TradingClient) -> None:
        self.ledger = ledger
        self.client = client

    async def process_queue(self, *, limit: int = 20) -> None:
        if not self.execution_enabled:
            return
        for event in self.ledger.queued_events(limit=limit):
            await self.process_event(int(event["id"]))

    @property
    def execution_enabled(self) -> bool:
        return bool(self.ledger.get_setting("execution_enabled", False))

    def set_execution_enabled(self, enabled: bool) -> None:
        self.ledger.set_setting("execution_enabled", enabled)
        self.ledger.audit(
            "EXECUTION_SWITCH",
            "warning" if not enabled else "info",
            f"Global execution {'enabled' if enabled else 'paused'}",
            {"enabled": enabled},
        )

    async def process_event(self, event_pk: int) -> None:
        event = self.ledger.get_event(event_pk)
        if event["status"] != EventStatus.QUEUED.value:
            return
        route = self.ledger.require_route(event["route_id"])
        if not self.execution_enabled or not route.enabled:
            return
        self.ledger.update_event(event_pk, EventStatus.PROCESSING.value)
        try:
            payload = BridgePayload.model_validate(event["payload"])
            await self._execute(event_pk, route, payload)
        except (BridgeError, WebullError, ValueError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            message = getattr(exc, "message", str(exc))
            self.ledger.update_event(event_pk, EventStatus.FAILED.value, error=f"{code}: {message}")
            self.ledger.audit(
                "ORDER_FAILED", "error", message, {"event_pk": event_pk, "code": code}
            )

    async def _execute(self, event_pk: int, route: RouteConfig, payload: BridgePayload) -> None:
        self._validate_route(route, payload)
        if payload.action == Action.CANCEL:
            await self._cancel(event_pk, route, payload)
        elif payload.action == Action.REPLACE:
            await self._replace(event_pk, route, payload)
        elif payload.action == Action.FLATTEN:
            await self._flatten(event_pk, route, payload)
        else:
            await self._place(event_pk, route, payload)

    def _validate_route(self, route: RouteConfig, payload: BridgePayload) -> None:
        if not route.account_id:
            raise BridgeError("ACCOUNT_MISSING", "Route has no Webull account ID")
        if payload.order_type not in route.accepted_order_types:
            raise BridgeError("ORDER_TYPE_BLOCKED", f"{payload.order_type.value} is not accepted")
        if payload.symbol and route.allowed_symbols and payload.symbol not in route.allowed_symbols:
            raise BridgeError("SYMBOL_BLOCKED", f"{payload.symbol} is not allowed on this route")
        if payload.quantity and payload.quantity > route.max_quantity:
            raise BridgeError("QUANTITY_LIMIT", "Quantity exceeds route max_quantity")
        ref_price = payload.limit_price or payload.stop_price
        if payload.quantity and ref_price and payload.quantity * ref_price > route.max_notional:
            raise BridgeError("NOTIONAL_LIMIT", "Order exceeds route max_notional")

    async def _place(self, event_pk: int, route: RouteConfig, payload: BridgePayload) -> None:
        side = payload.action.value
        client_order_id = payload.client_order_id or make_client_order_id(
            route.route_id, payload.event_id, payload.action.value
        )
        order = self._order_payload(payload, side=side, client_order_id=client_order_id)
        order_pk = self.ledger.create_order(
            event_pk,
            {
                "route_id": route.route_id,
                "account_id": route.account_id,
                "client_order_id": client_order_id,
                "action": payload.action.value,
                "symbol": payload.symbol,
                "side": side,
                "order": order,
            },
        )
        self.ledger.update_event(event_pk, EventStatus.PROCESSING.value, normalized=order)
        preview = await self.client.preview_order(route.account_id, order)
        self.ledger.update_order(order_pk, EventStatus.PROCESSING.value, preview=preview)
        response = await self.client.place_order(route.account_id, order)
        self.ledger.update_order(
            order_pk,
            EventStatus.SUBMITTED.value,
            response=response,
            webull_order_id=response.get("order_id"),
        )
        self.ledger.update_event(event_pk, EventStatus.SUBMITTED.value, normalized=order)
        self.ledger.audit(
            "ORDER_SUBMITTED",
            "info",
            f"{side} {payload.quantity} {payload.symbol}",
            {"event_pk": event_pk, "client_order_id": client_order_id},
        )

    async def _cancel(self, event_pk: int, route: RouteConfig, payload: BridgePayload) -> None:
        target = payload.order_reference
        if target is None:
            raise BridgeError("CLIENT_ORDER_ID_MISSING", "Cancel requires a target client order id")
        order_pk = self.ledger.create_order(
            event_pk,
            {
                "route_id": route.route_id,
                "account_id": route.account_id,
                "target_client_order_id": target,
                "action": payload.action.value,
                "symbol": payload.symbol,
                "side": None,
                "order": {"client_order_id": target},
            },
        )
        response = await self.client.cancel_order(route.account_id, target)
        self.ledger.update_order(order_pk, EventStatus.CANCELLED.value, response=response)
        self.ledger.update_event(
            event_pk, EventStatus.CANCELLED.value, normalized={"client_order_id": target}
        )

    async def _replace(self, event_pk: int, route: RouteConfig, payload: BridgePayload) -> None:
        target = payload.order_reference
        if target is None:
            raise BridgeError(
                "CLIENT_ORDER_ID_MISSING", "Replace requires a target client order id"
            )
        change: dict[str, Any] = {"client_order_id": target}
        if payload.quantity is not None:
            change["quantity"] = str(payload.quantity)
        if payload.order_type is not None:
            change["order_type"] = payload.order_type.value
        if payload.limit_price is not None:
            change["limit_price"] = str(payload.limit_price)
        if payload.stop_price is not None:
            change["stop_price"] = str(payload.stop_price)
        change["time_in_force"] = payload.time_in_force.value
        order_pk = self.ledger.create_order(
            event_pk,
            {
                "route_id": route.route_id,
                "account_id": route.account_id,
                "target_client_order_id": target,
                "action": payload.action.value,
                "symbol": payload.symbol,
                "side": None,
                "order": change,
            },
        )
        response = await self.client.replace_order(route.account_id, change)
        self.ledger.update_order(order_pk, EventStatus.SUBMITTED.value, response=response)
        self.ledger.update_event(event_pk, EventStatus.SUBMITTED.value, normalized=change)

    async def _flatten(self, event_pk: int, route: RouteConfig, payload: BridgePayload) -> None:
        positions = await self.client.positions(route.account_id)
        self.ledger.save_position_snapshot(route.account_id, positions)
        match = next(
            (item for item in positions if str(item.get("symbol", "")).upper() == payload.symbol),
            None,
        )
        if not match:
            raise BridgeError("POSITION_MISSING", f"No Webull position found for {payload.symbol}")
        quantity = Decimal(str(match.get("quantity") or "0"))
        if quantity == 0:
            raise BridgeError("POSITION_FLAT", f"{payload.symbol} is already flat")
        side = "SELL" if quantity > 0 else "BUY"
        close_quantity = min(abs(quantity), payload.quantity or abs(quantity))
        client_order_id = payload.client_order_id or make_client_order_id(
            route.route_id, payload.event_id, payload.action.value
        )
        close_payload = payload.model_copy(
            update={"quantity": close_quantity, "order_type": OrderType.MARKET}
        )
        order = self._order_payload(close_payload, side=side, client_order_id=client_order_id)
        order_pk = self.ledger.create_order(
            event_pk,
            {
                "route_id": route.route_id,
                "account_id": route.account_id,
                "client_order_id": client_order_id,
                "action": payload.action.value,
                "symbol": payload.symbol,
                "side": side,
                "order": order,
            },
        )
        preview = await self.client.preview_order(route.account_id, order)
        self.ledger.update_order(order_pk, EventStatus.PROCESSING.value, preview=preview)
        response = await self.client.place_order(route.account_id, order)
        self.ledger.update_order(
            order_pk,
            EventStatus.SUBMITTED.value,
            response=response,
            webull_order_id=response.get("order_id"),
        )
        self.ledger.update_event(event_pk, EventStatus.SUBMITTED.value, normalized=order)

    def _order_payload(
        self, payload: BridgePayload, *, side: str, client_order_id: str
    ) -> dict[str, Any]:
        if len(client_order_id) > 32:
            raise BridgeError(
                "CLIENT_ORDER_ID_TOO_LONG", "client_order_id must be at most 32 characters"
            )
        order: dict[str, Any] = {
            "client_order_id": client_order_id,
            "combo_type": payload.combo_type,
            "symbol": payload.symbol,
            "instrument_type": payload.instrument_type.value,
            "market": payload.market,
            "order_type": payload.order_type.value,
            "quantity": str(payload.quantity),
            "side": side,
            "time_in_force": payload.time_in_force.value,
            "entrust_type": payload.entrust_type,
        }
        if payload.instrument_type == InstrumentType.EQUITY:
            order["support_trading_session"] = payload.support_trading_session or "CORE"
        if payload.limit_price is not None:
            order["limit_price"] = str(payload.limit_price)
        if payload.stop_price is not None:
            order["stop_price"] = str(payload.stop_price)
        return order

    async def refresh_positions(self, account_id: str) -> list[dict[str, Any]]:
        positions = await self.client.positions(account_id)
        self.ledger.save_position_snapshot(account_id, positions)
        return positions

    async def reconcile_open_orders(self, account_id: str) -> list[dict[str, Any]]:
        open_orders = await self.client.open_orders(account_id)
        return open_orders
