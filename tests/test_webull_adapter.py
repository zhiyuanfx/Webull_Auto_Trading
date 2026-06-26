from decimal import Decimal

import pytest

from strategy_desk.config import Settings
from strategy_desk.domain import (
    AssetClass,
    ExecutionMode,
    OrderCommand,
    OrderStatus,
    OrderTicket,
    OrderType,
    Side,
)
from strategy_desk.gateway import GatewayRejected
from strategy_desk.webull import WebullTradingAdapter


class Response:
    status_code = 200

    def json(self):
        return {"client_order_id": "client-1", "order_id": "webull-1"}


class Orders:
    def __init__(self):
        self.placed = None
        self.cancelled = None

    def place_order(self, account_id, orders):
        self.placed = (account_id, orders)
        return Response()

    def cancel_order(self, account_id, client_order_id):
        self.cancelled = (account_id, client_order_id)
        return Response()

    def get_order_detail(self, account_id, client_order_id):
        assert (account_id, client_order_id) == ("uat-account", "client-1")
        response = Response()
        response.json = lambda: {
            "orders": [
                {
                    "client_order_id": "client-1",
                    "order_id": "webull-1",
                    "status": "PARTIAL_FILLED",
                    "filled_quantity": "0.5",
                    "filled_price": "2400.2",
                }
            ]
        }
        return response


class FailingOrders(Orders):
    def place_order(self, account_id, orders):
        raise RuntimeError("SDK failure leaked prod-secret")


class Client:
    def __init__(self):
        self.order_v2 = Orders()
        self.order_v3 = Orders()


class FailingClient:
    def __init__(self):
        self.order_v3 = FailingOrders()


class CoreClient:
    def __init__(self):
        self.request = None

    def get_response(self, request):
        self.request = request
        response = Response()
        response.json = lambda: [{"symbol": "MGCQ6", "contract_month": "202608"}]
        return response


def pending_ticket() -> OrderTicket:
    command = OrderCommand(
        strategy_instance_id="strategy",
        account_id="uat-account",
        symbol="MGCQ6",
        asset_class=AssetClass.FUTURES,
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("2400.1"),
        client_order_id="client-1",
    )
    return OrderTicket(id="ticket-1", command=command, status=OrderStatus.PENDING)


def equity_ticket() -> OrderTicket:
    command = OrderCommand(
        strategy_instance_id="strategy",
        account_id="uat-account",
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.1"),
        client_order_id="client-1",
    )
    return OrderTicket(id="ticket-1", command=command, status=OrderStatus.PENDING)


@pytest.mark.asyncio
async def test_uat_adapter_maps_broker_neutral_order_to_documented_payload():
    adapter = WebullTradingAdapter(Settings(), ExecutionMode.WEBULL_UAT)
    adapter._client = Client()
    result = await adapter.place(pending_ticket())
    assert adapter._client.order_v2.placed is None
    account, orders = adapter._client.order_v3.placed
    assert account == "uat-account"
    assert orders[0] == {
        "client_order_id": "client-1",
        "combo_type": "NORMAL",
        "symbol": "MGCQ6",
        "instrument_type": "FUTURES",
        "market": "US",
        "order_type": "LIMIT",
        "quantity": "1",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "limit_price": "2400.1",
    }
    assert result.broker_order_id == "webull-1"


@pytest.mark.asyncio
async def test_uat_adapter_uses_common_order_api_for_equity_payload():
    adapter = WebullTradingAdapter(Settings(), ExecutionMode.WEBULL_UAT)
    adapter._client = Client()
    result = await adapter.place(equity_ticket())
    assert adapter._client.order_v2.placed is None
    account, orders = adapter._client.order_v3.placed
    assert account == "uat-account"
    assert orders[0] == {
        "client_order_id": "client-1",
        "combo_type": "NORMAL",
        "symbol": "AAPL",
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "quantity": "1",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "limit_price": "100.1",
        "support_trading_session": "CORE",
    }
    assert result.broker_order_id == "webull-1"


@pytest.mark.asyncio
async def test_uat_adapter_uses_common_order_api_for_futures_cancel():
    adapter = WebullTradingAdapter(Settings(), ExecutionMode.WEBULL_UAT)
    adapter._client = Client()
    result = await adapter.cancel(pending_ticket())
    assert adapter._client.order_v2.cancelled is None
    assert adapter._client.order_v3.cancelled == ("uat-account", "client-1")
    assert result.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_live_adapter_rejects_mutation_without_both_gates():
    adapter = WebullTradingAdapter(Settings(webull_live_enabled=True), ExecutionMode.WEBULL_LIVE)
    with pytest.raises(GatewayRejected, match="explicit UI confirmation"):
        await adapter.place(pending_ticket())


@pytest.mark.asyncio
async def test_sdk_exception_is_sanitized_and_translated():
    adapter = WebullTradingAdapter(
        Settings(webull_prod_app_secret="prod-secret"),
        ExecutionMode.WEBULL_UAT,
    )
    adapter._client = FailingClient()
    with pytest.raises(GatewayRejected) as error:
        await adapter.place(pending_ticket())
    assert error.value.code == "RuntimeError"
    assert "prod-secret" not in str(error.value)
    assert "<redacted>" in str(error.value)


@pytest.mark.asyncio
async def test_futures_contracts_use_documented_v2_path_through_sdk_signer():
    adapter = WebullTradingAdapter(Settings(), ExecutionMode.WEBULL_UAT)
    adapter._client = Client()
    adapter._api_client = CoreClient()
    values = await adapter.futures_contracts("mgc")
    request = adapter._api_client.request
    assert request.get_action_name() == "/openapi/instrument/futures/list"
    assert request.get_query_params() == {
        "category": "US_FUTURES",
        "code": "MGC",
        "status": "OC",
    }
    assert values[0]["symbol"] == "MGCQ6"


@pytest.mark.asyncio
async def test_order_detail_is_normalized_for_reconciliation():
    adapter = WebullTradingAdapter(Settings(), ExecutionMode.WEBULL_UAT)
    adapter._client = Client()
    event = await adapter.order_detail_event(pending_ticket())
    assert event == {
        "account_id": "uat-account",
        "client_order_id": "client-1",
        "order_id": "webull-1",
        "order_status": "PARTIAL_FILLED",
        "scene_type": "FILLED",
        "filled_qty": "0.5",
        "filled_price": "2400.2",
    }
