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
from webull_auto_trading.runtime import RuntimeService


def test_individual_flatten_pauses_and_closes_at_quote_side(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL")
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
        StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL")
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
        StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL")
    )

    summary = runtime.flatten_all_strategies()

    assert summary["global_pause"] is True
    assert runtime.repository.get_setting("global_pause") is True


def test_live_mode_rejects_flatten(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.set_runtime_mode(RuntimeMode.LIVE)
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL")
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
            StrategyInstance(id="st-1", strategy_name="day_many_bian", symbol="NASDAQ:AAPL"),
            StrategyInstance(id="st-2", strategy_name="day_many_bian", symbol="NASDAQ:AAPL"),
            StrategyInstance(
                id="st-3",
                strategy_name="day_many_bian",
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
