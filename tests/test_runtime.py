import sys
import types

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
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
