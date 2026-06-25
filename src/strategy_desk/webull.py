from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from strategy_desk.config import Settings
from strategy_desk.domain import AssetClass, ExecutionMode, OrderStatus, OrderTicket
from strategy_desk.gateway import BrokerAdapter, GatewayRejected

UAT_HTTP_HOST = "us-openapi-alb.uat.webullbroker.com"
UAT_EVENTS_HOST = "us-openapi-events.uat.webullbroker.com"
PROD_HTTP_HOST = "api.webull.com"
PROD_EVENTS_HOST = "events-api.webull.com"


class WebullTradingAdapter(BrokerAdapter):
    """Lazy official-SDK adapter. Construction is read-only; order calls are explicit."""

    def __init__(
        self, settings: Settings, mode: ExecutionMode, live_confirmed: bool = False
    ) -> None:
        if mode not in {ExecutionMode.WEBULL_UAT, ExecutionMode.WEBULL_LIVE}:
            raise ValueError("Webull adapter requires UAT or LIVE mode")
        self.mode = mode
        self.settings = settings
        self.live_confirmed = live_confirmed
        self._client: Any = None
        self._api_client: Any = None

    def _trade_client(self):
        if self._client is not None:
            return self._client
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:
            raise GatewayRejected(
                "WEBULL_SDK_MISSING", "Install the project with the 'webull' extra"
            ) from exc

        if self.mode == ExecutionMode.WEBULL_UAT:
            key, secret, host = (
                self.settings.webull_uat_app_key,
                self.settings.webull_uat_app_secret,
                UAT_HTTP_HOST,
            )
        else:
            key, secret, host = (
                self.settings.webull_prod_app_key,
                self.settings.webull_prod_app_secret,
                PROD_HTTP_HOST,
            )
        if not key or not secret:
            raise GatewayRejected(
                "CREDENTIALS_MISSING", f"{self.mode} credentials are not configured"
            )
        token_wait = (
            self.settings.webull_uat_token_wait_seconds
            if self.mode == ExecutionMode.WEBULL_UAT
            else self.settings.webull_prod_token_wait_seconds
        )
        api_client = ApiClient(
            key,
            secret,
            self.settings.webull_region,
            connect_timeout=5,
            timeout=10,
            token_check_duration_seconds=token_wait,
            token_check_interval_seconds=5,
        )
        api_client.add_endpoint(self.settings.webull_region, host)
        token_directory = self.settings.webull_token_dir / self.mode.value.lower()
        token_directory.mkdir(parents=True, exist_ok=True)
        api_client.set_token_dir(str(token_directory))
        # Prevent the SDK from enabling its default console/file logger, which can include
        # signed request metadata and application identifiers.
        api_client.set_stream_logger(stream=io.StringIO(), log_level=logging.CRITICAL)
        self._api_client = api_client
        self._client = TradeClient(api_client)
        return self._client

    async def list_accounts(self) -> list[dict[str, Any]]:
        response = await asyncio.to_thread(self._trade_client().account_v2.get_account_list)
        self._require_success(response)
        return response.json()

    async def account_balance(self, account_id: str) -> dict[str, Any]:
        response = await asyncio.to_thread(
            self._trade_client().account_v2.get_account_balance, account_id
        )
        self._require_success(response)
        return response.json()

    async def account_positions(self, account_id: str) -> list[dict[str, Any]]:
        response = await asyncio.to_thread(
            self._trade_client().account_v2.get_account_position, account_id
        )
        self._require_success(response)
        return response.json()

    async def futures_contracts(self, code: str, status: str = "OC") -> list[dict[str, Any]]:
        """Query the documented v2 endpoint through the SDK signer.

        SDK 2.0.11 has no operation-specific wrapper for this endpoint, so this is the
        only raw request surface in the adapter.
        """
        self._trade_client()
        from webull.core.request import ApiRequest

        request = ApiRequest(
            "/openapi/instrument/futures/list",
            version="v2",
            method="GET",
            query_params={},
        )
        request.add_query_param("category", "US_FUTURES")
        request.add_query_param("code", code.strip().upper())
        request.add_query_param("status", status)
        response = await asyncio.to_thread(self._api_client.get_response, request)
        self._require_success(response)
        return response.json()

    async def order_detail_event(self, ticket: OrderTicket) -> dict[str, object]:
        """Normalize the HTTP order-detail response into the gateway event shape."""
        response = await asyncio.to_thread(
            self._order_api(ticket).get_order_detail,
            ticket.command.account_id,
            ticket.command.client_order_id,
        )
        self._require_success(response)
        payload = response.json()
        orders = payload.get("orders") or []
        if not orders:
            raise GatewayRejected("ORDER_DETAIL_EMPTY", "Webull returned no order detail")
        order = orders[0]
        status = str(order.get("status", "UNKNOWN"))
        scene = {
            "FILLED": "FINAL_FILLED",
            "PARTIAL_FILLED": "FILLED",
            "FAILED": "PLACE_FAILED",
            "CANCELLED": "CANCEL_SUCCESS",
        }.get(status, "")
        return {
            "account_id": ticket.command.account_id,
            "client_order_id": ticket.command.client_order_id,
            "order_id": order.get("order_id"),
            "order_status": status,
            "scene_type": scene,
            "filled_qty": order.get("filled_quantity") or "0",
            "filled_price": order.get("filled_price"),
        }

    async def place(self, ticket: OrderTicket) -> OrderTicket:
        self._require_mutation_allowed()
        command = ticket.command
        order = {
            "client_order_id": command.client_order_id,
            "combo_type": "NORMAL",
            "symbol": command.symbol,
            "instrument_type": command.asset_class.value,
            "market": "US",
            "order_type": command.order_type.value,
            "quantity": str(command.quantity),
            "side": command.side.value,
            "time_in_force": command.time_in_force.value,
            "entrust_type": "QTY",
        }
        if command.limit_price is not None:
            order["limit_price"] = str(command.limit_price)
        if command.stop_price is not None:
            order["stop_price"] = str(command.stop_price)
        if command.asset_class == AssetClass.EQUITY:
            order["support_trading_session"] = "CORE"
        response = await asyncio.to_thread(
            self._order_api(ticket).place_order, command.account_id, [order]
        )
        self._require_success(response)
        payload = response.json()
        return ticket.model_copy(
            update={
                "status": OrderStatus.SUBMITTED,
                "broker_order_id": payload.get("order_id"),
                "updated_at": datetime.now(UTC),
            }
        )

    async def replace(self, ticket: OrderTicket, limit_price: Decimal | None) -> OrderTicket:
        self._require_mutation_allowed()
        change = {
            "client_order_id": ticket.command.client_order_id,
            "quantity": str(ticket.command.quantity),
        }
        if limit_price is not None:
            change["limit_price"] = str(limit_price)
        response = await asyncio.to_thread(
            self._order_api(ticket).replace_order,
            ticket.command.account_id,
            [change],
        )
        self._require_success(response)
        command = ticket.command.model_copy(update={"limit_price": limit_price})
        return ticket.model_copy(update={"command": command, "updated_at": datetime.now(UTC)})

    async def cancel(self, ticket: OrderTicket) -> OrderTicket:
        self._require_mutation_allowed()
        response = await asyncio.to_thread(
            self._order_api(ticket).cancel_order,
            ticket.command.account_id,
            ticket.command.client_order_id,
        )
        self._require_success(response)
        return ticket.model_copy(
            update={"status": OrderStatus.CANCELLED, "updated_at": datetime.now(UTC)}
        )

    def _order_api(self, _ticket: OrderTicket):
        client = self._trade_client()
        return client.order_v3

    def _require_mutation_allowed(self) -> None:
        if self.mode == ExecutionMode.WEBULL_LIVE and not (
            self.settings.webull_live_enabled and self.live_confirmed
        ):
            raise GatewayRejected(
                "LIVE_GATE_CLOSED", "Live trading requires environment and explicit UI confirmation"
            )

    @staticmethod
    def _require_success(response: Any) -> None:
        if response.status_code != 200:
            try:
                payload = response.json()
            except Exception:
                payload = {}
            raise GatewayRejected(
                str(payload.get("error_code", f"HTTP_{response.status_code}")),
                str(payload.get("message", "Webull request failed")),
            )


class WebullTradeEventStream:
    """Official gRPC event-stream wrapper; the caller owns reconciliation/persistence."""

    def __init__(self, settings: Settings, mode: ExecutionMode) -> None:
        self.settings = settings
        self.mode = mode

    async def run(self, account_ids: list[str], on_event) -> None:
        try:
            import webull.trade.trade_events_client as events_module
            from webull.trade.events.types import EVENT_TYPE_ORDER, ORDER_STATUS_CHANGED
            from webull.trade.trade_events_client import TradeEventsClient
        except ImportError as exc:
            raise GatewayRejected("WEBULL_SDK_MISSING", "Install the 'webull' extra") from exc
        if self.mode == ExecutionMode.WEBULL_UAT:
            key, secret, host = (
                self.settings.webull_uat_app_key,
                self.settings.webull_uat_app_secret,
                UAT_EVENTS_HOST,
            )
        else:
            key, secret, host = (
                self.settings.webull_prod_app_key,
                self.settings.webull_prod_app_secret,
                PROD_EVENTS_HOST,
            )
        if not key or not secret:
            raise GatewayRejected(
                "CREDENTIALS_MISSING", f"{self.mode} credentials are not configured"
            )
        loop = asyncio.get_running_loop()
        # SDK 2.0.11 prints signed gRPC metadata from module-level `print` calls.
        # Disable those calls without redirecting process-wide stdout.
        events_module.print = lambda *_args, **_kwargs: None
        client = TradeEventsClient(key, secret, self.settings.webull_region, host=host)

        def receive(event_type, subscribe_type, payload, _raw):
            if event_type == EVENT_TYPE_ORDER and subscribe_type == ORDER_STATUS_CHANGED:
                value = json.loads(payload) if isinstance(payload, str) else payload
                if isinstance(value, dict) and isinstance(value.get("payload"), dict):
                    value = value["payload"]
                asyncio.run_coroutine_threadsafe(on_event(value), loop)

        client.on_events_message = receive
        await asyncio.to_thread(client.do_subscribe, account_ids)


class WebullMarketStream:
    """Official MQTT SDK wrapper that deliberately exposes raw parsed SDK messages."""

    def __init__(self, settings: Settings, mode: ExecutionMode) -> None:
        self.settings = settings
        self.mode = mode
        self._client: Any = None
        self._ready = asyncio.Event()
        self._category = ""
        self._subscription_types: list[str] = []

    async def run(
        self,
        symbols: list[str],
        category: str,
        subscription_types: list[str],
        on_message,
    ) -> None:
        try:
            from webull.data.data_streaming_client import DataStreamingClient
            from webull.data.internal.default_retry_policy import DefaultQuotesRetryPolicy
        except ImportError as exc:
            raise GatewayRejected("WEBULL_SDK_MISSING", "Install the 'webull' extra") from exc
        if self.mode == ExecutionMode.WEBULL_UAT:
            key, secret = self.settings.webull_uat_app_key, self.settings.webull_uat_app_secret
            http_host, mqtt_host = UAT_HTTP_HOST, "us-data-api.uat.webullbroker.com"
        else:
            key, secret = self.settings.webull_prod_app_key, self.settings.webull_prod_app_secret
            http_host, mqtt_host = PROD_HTTP_HOST, "data-api.webull.com"
        if not key or not secret:
            raise GatewayRejected(
                "CREDENTIALS_MISSING", f"{self.mode} credentials are not configured"
            )
        loop = asyncio.get_running_loop()
        self._category = category
        self._subscription_types = subscription_types
        client = DataStreamingClient(
            key,
            secret,
            self.settings.webull_region,
            f"desk-{uuid4().hex[:20]}",
            http_host=http_host,
            mqtt_host=mqtt_host,
            retry_policy=DefaultQuotesRetryPolicy(max_retry_times=3, fixed_delay=1000),
        )
        token_wait = (
            self.settings.webull_uat_token_wait_seconds
            if self.mode == ExecutionMode.WEBULL_UAT
            else self.settings.webull_prod_token_wait_seconds
        )
        client.api_client._token_check_duration_seconds = token_wait
        client.api_client._token_check_interval_seconds = 5
        client.api_client._connect_timeout = 5
        client.api_client._read_timeout = 10
        token_directory = self.settings.webull_token_dir / self.mode.value.lower()
        token_directory.mkdir(parents=True, exist_ok=True)
        client.set_token_dir(str(token_directory))
        self._client = client

        def connected(stream, _api_client, _session_id):
            stream.subscribe(symbols, category, subscription_types)

        def subscribed(_stream, _api_client, _session_id):
            loop.call_soon_threadsafe(self._ready.set)

        def message(_stream, topic, values):
            asyncio.run_coroutine_threadsafe(on_message(topic, values), loop)

        client.on_connect_success = connected
        client.on_subscribe_success = subscribed
        client.on_quotes_message = message
        await asyncio.to_thread(client.connect_and_loop_forever, 1, False)

    async def add_symbols(self, symbols: list[str]) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=15)
        except TimeoutError as exc:
            raise GatewayRejected(
                "MARKET_STREAM_TIMEOUT", "Market stream is not connected"
            ) from exc
        if self._client and symbols:
            await asyncio.to_thread(
                self._client.subscribe,
                symbols,
                self._category,
                self._subscription_types,
            )

    async def wait_ready(self) -> None:
        await self._ready.wait()

    async def stop(self) -> None:
        if self._client:
            self._client._thread_terminate = True
            await asyncio.to_thread(self._client.disconnect)
