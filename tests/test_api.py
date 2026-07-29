from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

import webull_auto_trading.api as api
from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
    LiveIntentAction,
    LiveOrderIntent,
    OrderRole,
    OrderSide,
    OrderStatus,
    RuntimeMode,
    StrategyInstance,
)
from webull_auto_trading.execution import AccountSnapshot, LiveOrdersSnapshot
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.runtime import RuntimeService


def test_market_stream_status_endpoint_returns_safe_status(monkeypatch) -> None:
    fake_service = FakeStreamService(
        {
            "state": "connected",
            "connected": True,
            "desired_symbols": ["NASDAQ:AAPL"],
            "subscribed_symbols": ["NASDAQ:AAPL"],
            "last_message_at": "2026-07-06T12:00:00+00:00",
            "last_error": None,
            "reconnect_attempt": 0,
        }
    )
    monkeypatch.setattr(api, "get_stream_service", lambda: fake_service)

    with TestClient(api.create_app()) as client:
        response = client.get("/api/market/stream-status")

    assert response.status_code == 200
    assert response.json() == fake_service.payload
    assert "api_key" not in response.text.lower()
    assert fake_service.started is True
    assert fake_service.stopped is True


def test_live_account_read_refreshes_and_then_returns_cached_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        webull_prod_app_key="key",
        webull_prod_app_secret="secret",
        webull_account_default_alias="stock_margin",
        webull_account_stock_margin_id="acct-secret",
        runtime_db_path=tmp_path / "runtime.sqlite3",
        strategies_test_config_path=tmp_path / "strategies.test.yml",
        strategies_live_config_path=tmp_path / "strategies.live.yml",
        _env_file=None,
    )
    runtime = RuntimeService(settings)
    runtime.initialize(seed_config=False)
    runtime.repository.set_runtime_mode(RuntimeMode.LIVE)
    fake_reader = FakeReadService(settings)
    api.LIVE_ACCOUNT_CACHE.clear()
    monkeypatch.setattr(api, "get_runtime", lambda: runtime)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_stream_service", lambda: FakeStreamService({}))
    monkeypatch.setattr(api, "WebullReadService", lambda _settings: fake_reader)

    with TestClient(api.create_app()) as client:
        refreshed = client.get("/api/account?refresh=true&account_alias=stock_margin")
        cached = client.get("/api/account?account_alias=stock_margin")

    assert refreshed.status_code == 200
    assert cached.status_code == 200
    assert fake_reader.calls == ["acct-secret"]
    assert cached.json()["balance"] == {"total_cash_balance": "123.45"}
    assert cached.json()["account_id_configured"] is True
    assert "acct-secret" not in refreshed.text
    assert "acct-secret" not in cached.text


def test_account_alias_endpoint_does_not_expose_account_ids(tmp_path, monkeypatch) -> None:
    settings = Settings(
        webull_account_default_alias="stock_margin",
        webull_account_stock_margin_id="acct-secret",
        runtime_db_path=tmp_path / "runtime.sqlite3",
        _env_file=None,
    )
    runtime = RuntimeService(settings)
    runtime.initialize(seed_config=False)
    monkeypatch.setattr(api, "get_runtime", lambda: runtime)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_stream_service", lambda: FakeStreamService({}))

    with TestClient(api.create_app()) as client:
        response = client.get("/api/webull/account-aliases")

    assert response.status_code == 200
    assert response.json()["aliases"] == [{"alias": "stock_margin", "configured": True}]
    assert "acct-secret" not in response.text


def test_orders_view_returns_test_mode_paper_data(tmp_path, monkeypatch) -> None:
    settings = Settings(
        runtime_db_path=tmp_path / "runtime.sqlite3",
        strategies_test_config_path=tmp_path / "strategies.test.yml",
        strategies_live_config_path=tmp_path / "strategies.live.yml",
        _env_file=None,
    )
    runtime = RuntimeService(settings)
    runtime.initialize(seed_config=False)
    order_book = PaperOrderBook()
    buy_order = order_book.place_virtual_stop(
        strategy_instance_id="st-paper",
        cycle_id="cyc-paper",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=2,
        stop_price=100,
        stop_loss=95,
        take_profit=105,
    )
    sell_order = order_book.place_virtual_stop(
        strategy_instance_id="st-paper",
        cycle_id="cyc-paper",
        symbol="NASDAQ:AAPL",
        side=OrderSide.SELL,
        role=OrderRole.MAIN,
        quantity=3,
        stop_price=90,
        stop_loss=95,
        take_profit=85,
    )
    runtime.repository.sync_paper_state(order_book.orders, [], market_prices={})
    monkeypatch.setattr(api, "get_runtime", lambda: runtime)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_stream_service", lambda: FakeStreamService({}))

    with TestClient(api.create_app()) as client:
        response = client.get("/api/orders-view")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "test"
    paper_orders = {item["side"]: item for item in payload["paper_orders"]}
    assert set(paper_orders) == {"BUY", "SELL"}
    assert {
        key: paper_orders["BUY"][key]
        for key in ("quantity", "stop_price", "fill_price", "stop_loss", "take_profit")
    } == {
        "quantity": 2,
        "stop_price": 100,
        "fill_price": None,
        "stop_loss": 95,
        "take_profit": 105,
    }
    assert {
        key: paper_orders["SELL"][key]
        for key in ("quantity", "stop_price", "fill_price", "stop_loss", "take_profit")
    } == {
        "quantity": 3,
        "stop_price": 90,
        "fill_price": None,
        "stop_loss": 95,
        "take_profit": 85,
    }
    assert {item["id"] for item in payload["paper_orders"]} == {buy_order.id, sell_order.id}
    assert [item["runtime_mode"] for item in payload["paper_cycles"]] == ["test"]
    assert "live_intents" not in payload


def test_orders_view_returns_live_data_without_account_ids(tmp_path, monkeypatch) -> None:
    settings = Settings(
        webull_prod_app_key="key",
        webull_prod_app_secret="secret",
        webull_account_default_alias="stock_margin",
        webull_account_stock_margin_id="acct-secret",
        runtime_db_path=tmp_path / "runtime.sqlite3",
        strategies_test_config_path=tmp_path / "strategies.test.yml",
        strategies_live_config_path=tmp_path / "strategies.live.yml",
        _env_file=None,
    )
    runtime = RuntimeService(settings)
    runtime.initialize(seed_config=False)
    runtime.repository.set_runtime_mode(RuntimeMode.LIVE)
    live_order = PaperOrderBook().place_virtual_stop(
        strategy_instance_id="st-live",
        cycle_id="cyc-live",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=4,
        stop_price=100,
        stop_loss=95,
        take_profit=110,
    )
    live_order.status = OrderStatus.OPEN
    live_order.fill_price = 101
    live_order.stop_loss = 98
    runtime.repository.sync_live_virtual_state([live_order])
    runtime.repository.upsert_live_order_intent(
        LiveOrderIntent(
            id="loi-live",
            strategy_instance_id="st-live",
            cycle_id="cyc-live",
            action=LiveIntentAction.OPEN_MARKET,
            side=OrderSide.BUY,
            quantity=1,
            market_data_symbol="NASDAQ:AAPL",
            webull_symbol="AAPL",
            account_alias="stock_margin",
            account_id="acct-secret",
            client_order_id="client-live",
            response={"account_id": "acct-secret", "status": "ok"},
        )
    )
    fake_reader = FakeReadService(settings)
    api.LIVE_ORDERS_CACHE.clear()
    monkeypatch.setattr(api, "get_runtime", lambda: runtime)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_stream_service", lambda: FakeStreamService({}))
    monkeypatch.setattr(api, "WebullReadService", lambda _settings: fake_reader)

    with TestClient(api.create_app()) as client:
        response = client.get("/api/orders-view?refresh=true&account_alias=stock_margin")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "live"
    assert fake_reader.order_calls == ["acct-secret"]
    assert payload["broker_open_orders"] == [{"client_order_id": "client-live"}]
    assert payload["broker_order_history"] == [{"client_order_id": "client-old"}]
    assert payload["live_intents"][0]["client_order_id"] == "client-live"
    assert {
        key: payload["live_virtual_orders"][0][key]
        for key in ("quantity", "stop_price", "fill_price", "stop_loss", "take_profit")
    } == {
        "quantity": 4,
        "stop_price": 100,
        "fill_price": 101,
        "stop_loss": 98,
        "take_profit": 110,
    }
    assert payload["live_virtual_orders"][0]["status"] == "OPEN"
    assert [item["runtime_mode"] for item in payload["live_cycles"]] == ["live"]
    assert "acct-secret" not in response.text
    assert "paper_orders" not in payload


def test_pause_api_returns_cancellation_count_and_logs_activity(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        runtime_db_path=tmp_path / "runtime.sqlite3",
        strategies_test_config_path=tmp_path / "strategies.test.yml",
        strategies_live_config_path=tmp_path / "strategies.live.yml",
        _env_file=None,
    )
    runtime = RuntimeService(settings)
    runtime.initialize(seed_config=False)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(
            id="st-pause",
            strategy_name="recycle_buy",
            symbol="NASDAQ:AAPL",
        )
    )
    order = runtime.order_book.place_virtual_stop(
        strategy_instance_id="st-pause",
        cycle_id="cyc-pause",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        stop_price=110,
        stop_loss=105,
        take_profit=120,
    )
    runtime.repository.sync_paper_state(
        runtime.order_book.orders,
        runtime.order_book.fills,
        market_prices={},
    )
    stream_service = FakeStreamService({})
    live_coordinator = FakeStreamService({})
    monkeypatch.setattr(api, "get_runtime", lambda: runtime)
    monkeypatch.setattr(api, "get_stream_service", lambda: stream_service)
    monkeypatch.setattr(api, "get_live_coordinator", lambda: live_coordinator)

    with TestClient(api.create_app()) as client:
        response = client.put(
            "/api/strategies/st-pause",
            json={"enabled": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "st-pause"
    assert payload["enabled"] is False
    assert payload["cancelled_pending"] == 1
    assert runtime.repository.list_paper_orders()[0].status == OrderStatus.CANCELLED
    event = next(
        row
        for row in runtime.repository.list_table("activity")
        if row["event_type"] == "VirtualEntriesCancelledOnPause"
    )
    event_payload = json.loads(event["payload_json"])
    assert event["strategy_instance_id"] == "st-pause"
    assert event["symbol"] == "NASDAQ:AAPL"
    assert event_payload == {
        "strategy_id": "st-pause",
        "symbol": "NASDAQ:AAPL",
        "mode": "test",
        "cancelled_pending": 1,
    }
    assert order.id in {item.id for item in runtime.repository.list_paper_orders()}
    assert stream_service.started is True
    assert live_coordinator.started is True


class FakeReadService:
    def __init__(self, _settings: Settings) -> None:
        self.calls: list[str] = []
        self.order_calls: list[str] = []

    async def snapshot(self, account_id: str | None = None) -> AccountSnapshot:
        assert account_id is not None
        self.calls.append(account_id)
        return AccountSnapshot(
            configured=True,
            account_id=account_id,
            balance={"total_cash_balance": "123.45"},
            positions=[],
            open_orders=[],
        )

    async def live_orders(self, account_id: str | None = None) -> LiveOrdersSnapshot:
        assert account_id is not None
        self.order_calls.append(account_id)
        return LiveOrdersSnapshot(
            configured=True,
            account_id=account_id,
            open_orders=[{"client_order_id": "client-live", "account_id": account_id}],
            order_history=[{"client_order_id": "client-old", "account_id": account_id}],
        )


class FakeStreamService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def status(self) -> dict[str, Any]:
        return self.payload
