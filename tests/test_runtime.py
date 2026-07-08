import sys
import types

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
    LiveIntentStatus,
    OrderRole,
    OrderSide,
    OrderStatus,
    PaperOrder,
    RuntimeMode,
    StrategyInstance,
    new_id,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.runtime import RuntimeService
from webull_auto_trading.strategy.base import Strategy
from webull_auto_trading.webull import WebullError


def test_runtime_merges_wrapped_and_top_level_partial_quote_updates(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL")
    )

    runtime.ingest_market_message(
        '{"last_update":1783267200000,"total_items":1,'
        '"data":[{"code":"NASDAQ:AAPL","bid":100.0,"ask":100.1}]}'
    )
    runtime.ingest_market_message(
        '{"code":"NASDAQ:AAPL","status":"OPEN","last_price":100.05,"delay_seconds":0}'
    )

    quote = runtime.current_quotes()["NASDAQ:AAPL"]

    assert quote.fields["bid"] == 100.0
    assert quote.fields["ask"] == 100.1
    assert quote.fields["last_price"] == 100.05
    assert quote.fields["last_update"] == 1783267200000
    assert [item.raw for item in runtime.stream_buffer.list_for_symbol("NASDAQ:AAPL")] == [
        {
            "code": "NASDAQ:AAPL",
            "bid": 100.0,
            "ask": 100.1,
            "last_update": 1783267200000,
        },
        {
            "code": "NASDAQ:AAPL",
            "status": "OPEN",
            "last_price": 100.05,
            "delay_seconds": 0,
        },
    ]


def test_individual_flatten_pauses_and_closes_at_quote_side(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL")
    )
    runtime.order_book.orders = [
        PaperOrder(
            id=new_id("ord"),
            strategy_instance_id="st-1",
            cycle_id="cyc-1",
            symbol="NASDAQ:AAPL",
            side=OrderSide.BUY,
            role=OrderRole.MAIN,
            quantity=2,
            status=OrderStatus.FILLED,
            fill_price=100,
        )
    ]
    runtime.quote_book.merge_quote_item({"code": "NASDAQ:AAPL", "bid": 110, "ask": 111})

    summary = runtime.flatten_strategy("st-1")
    account = runtime.repository.recompute_paper_account(market_prices={"NASDAQ:AAPL": 110})
    instance = runtime.repository.list_strategy_instances()[0]

    assert summary["closed_positions"] == 1
    assert summary["warnings"] == []
    assert instance.enabled is False
    assert runtime.order_book.orders[0].metadata["close_price"] == 110
    assert account.realized_pnl == 20


def test_flatten_reports_missing_quote_without_inventing_close_price(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL")
    )
    runtime.order_book.orders = [
        PaperOrder(
            id=new_id("ord"),
            strategy_instance_id="st-1",
            cycle_id="cyc-1",
            symbol="NASDAQ:AAPL",
            side=OrderSide.SELL,
            role=OrderRole.MAIN,
            quantity=1,
            status=OrderStatus.FILLED,
            fill_price=100,
        )
    ]

    summary = runtime.flatten_strategy("st-1")

    assert summary["closed_positions"] == 0
    assert summary["warnings"] == ["Cannot flatten NASDAQ:AAPL: no current quote available"]
    assert runtime.order_book.orders[0].status == OrderStatus.FILLED
    assert "close_price" not in runtime.order_book.orders[0].metadata


def test_global_flatten_sets_global_pause(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL")
    )

    summary = runtime.flatten_all_strategies()

    assert summary["global_pause"] is True
    assert runtime.repository.get_setting("global_pause") is True


def test_live_mode_rejects_flatten(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.set_runtime_mode(RuntimeMode.LIVE)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL")
    )

    try:
        runtime.flatten_strategy("st-1")
    except PermissionError as exc:
        assert "paper-only" in str(exc)
    else:
        raise AssertionError("live flatten should be rejected")


def test_enabled_symbol_streams_are_deduplicated(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.seed_strategy_instances(
        [
            StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL"),
            StrategyInstance(id="st-2", strategy_name="recycle_buy", symbol="NASDAQ:AAPL"),
            StrategyInstance(
                id="st-3",
                strategy_name="recycle_buy",
                symbol="NASDAQ:MSFT",
                enabled=False,
            ),
        ]
    )

    assert runtime.enabled_symbol_streams() == [
        {
            "symbol": "NASDAQ:AAPL",
            "strategy_ids": ["st-1", "st-2"],
            "strategy_count": 2,
        }
    ]


def test_runtime_resolves_builtin_recycle_buy_strategy(tmp_path) -> None:
    runtime = make_runtime(tmp_path)

    assert runtime.resolve_strategy("recycle_buy") is runtime.strategies["recycle_buy"]


def test_runtime_resolves_private_strategy_module_by_name(tmp_path, monkeypatch) -> None:
    module = types.ModuleType("webull_auto_trading.strategy.private_alpha")

    class PrivateAlphaStrategy(Strategy):
        def on_quote(
            self,
            instance: StrategyInstance,
            quote,
            order_book: PaperOrderBook,
        ) -> list[str]:
            return [f"private:{instance.id}:{quote.symbol}:{len(order_book.orders)}"]

    module.PrivateAlphaStrategy = PrivateAlphaStrategy
    monkeypatch.setitem(sys.modules, module.__name__, module)
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-private", strategy_name="private_alpha", symbol="NASDAQ:AAPL")
    )

    messages = runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 100, "ask": 101, "last_price": 100.5, "delay_seconds": 0}
    )

    assert messages == ["private:st-private:NASDAQ:AAPL:0"]
    assert runtime.resolve_strategy("private_alpha") is runtime.strategies["private_alpha"]


def test_runtime_skips_missing_private_strategy_without_trading(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-missing", strategy_name="missing_private", symbol="NASDAQ:AAPL")
    )

    messages = runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 100, "ask": 101, "last_price": 100.5, "delay_seconds": 0}
    )

    assert messages == ["Unknown strategy: missing_private"]
    assert runtime.order_book.orders == []


def test_live_quote_submits_open_and_virtual_close_market_orders(tmp_path) -> None:
    client = FakeLiveOrderClient()
    runtime = make_live_runtime(tmp_path, client)
    runtime.repository.upsert_strategy_instance(live_strategy())

    runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 100, "ask": 101, "last_price": 100.5, "delay_seconds": 0}
    )
    first_intent = runtime.repository.list_live_order_intents()[0]
    first_intent.status = LiveIntentStatus.FILLED
    runtime.repository.upsert_live_order_intent(first_intent)
    runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 106, "ask": 107, "last_price": 106.5, "delay_seconds": 0}
    )

    assert [order["side"] for _account_id, order in client.orders] == ["BUY", "SELL"]
    assert all(order["order_type"] == "MARKET" for _account_id, order in client.orders)
    assert all(account_id == "acct-1" for account_id, _order in client.orders)
    assert [intent.action.value for intent in runtime.repository.list_live_order_intents()] == [
        "CLOSE_MARKET",
        "OPEN_MARKET",
    ]
    assert runtime.repository.list_live_virtual_orders()[0].status == OrderStatus.CLOSED
    assert runtime.repository.list_cycles(RuntimeMode.LIVE)[0]["runtime_mode"] == "live"


def test_live_order_rejection_pauses_strategy(tmp_path) -> None:
    runtime = make_live_runtime(tmp_path, RejectingLiveOrderClient())
    runtime.repository.upsert_strategy_instance(live_strategy())

    runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 100, "ask": 101, "last_price": 100.5, "delay_seconds": 0}
    )

    instance = runtime.repository.list_strategy_instances()[0]
    intent = runtime.repository.list_live_order_intents()[0]
    virtual_order = runtime.repository.list_live_virtual_orders()[0]
    assert instance.enabled is False
    assert intent.status == LiveIntentStatus.REJECTED
    assert virtual_order.status == OrderStatus.CANCELLED
    assert virtual_order.metadata["live_status"] == "REJECTED"


def make_runtime(tmp_path) -> RuntimeService:
    runtime = RuntimeService(
        Settings(
            runtime_db_path=tmp_path / "runtime.sqlite3",
            strategies_test_config_path=tmp_path / "strategies.test.yml",
            strategies_live_config_path=tmp_path / "strategies.live.yml",
        )
    )
    runtime.initialize(seed_config=False)
    return runtime


def make_live_runtime(tmp_path, client) -> RuntimeService:
    runtime = RuntimeService(
        Settings(
            runtime_db_path=tmp_path / "runtime.sqlite3",
            strategies_test_config_path=tmp_path / "strategies.test.yml",
            strategies_live_config_path=tmp_path / "strategies.live.yml",
            webull_account_stock_margin_id="acct-1",
            live_execution_master_enable=True,
            _env_file=None,
        ),
        live_order_client=client,
    )
    runtime.initialize(seed_config=False)
    runtime.repository.set_runtime_mode(RuntimeMode.LIVE)
    return runtime


def live_strategy() -> StrategyInstance:
    return StrategyInstance(
        id="st-live",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
        market_data_symbol="NASDAQ:AAPL",
        webull_symbol="AAPL",
        account_alias="stock_margin",
        asset_class="stock",
        enabled=True,
        live_execution_enabled=True,
        params={
            "lots": 1,
            "stop_loss_distance_price": 5,
            "take_profit_distance_price": 5,
            "cooldown_seconds": 5,
        },
    )


class FakeLiveOrderClient:
    def __init__(self) -> None:
        self.orders: list[tuple[str, dict[str, str]]] = []

    async def place_order(self, account_id: str, order: dict[str, str]) -> dict[str, str]:
        self.orders.append((account_id, order))
        return {"client_order_id": order["client_order_id"], "order_id": f"wb-{len(self.orders)}"}


class RejectingLiveOrderClient:
    async def place_order(self, account_id: str, order: dict[str, str]) -> dict[str, str]:
        raise WebullError("REJECTED", "order rejected")
