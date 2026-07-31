import asyncio
import sys
import threading
import types
from datetime import timedelta

import pytest

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
    LiveAccountState,
    LiveIntentAction,
    LiveIntentStatus,
    LiveOrderIntent,
    OrderRole,
    OrderSide,
    OrderStatus,
    PaperOrder,
    RuntimeMode,
    StrategyInstance,
    new_id,
    utc_now,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.runtime import RuntimeService, StrategyResetBlocked
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


def test_pause_cancels_only_target_pending_paper_orders_and_is_idempotent(
    tmp_path,
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.seed_strategy_instances(
        [
            StrategyInstance(id="st-1", strategy_name="recycle_buy", symbol="NASDAQ:AAPL"),
            StrategyInstance(id="st-2", strategy_name="recycle_buy", symbol="NASDAQ:AAPL"),
        ]
    )
    runtime.order_book.orders = [
        virtual_order("target-main", "st-1", OrderStatus.PENDING),
        virtual_order(
            "target-add-on",
            "st-1",
            OrderStatus.PENDING,
            role=OrderRole.ADD_ON,
        ),
        virtual_order("other-pending", "st-2", OrderStatus.PENDING),
        virtual_order("target-opening", "st-1", OrderStatus.OPENING),
        virtual_order("target-filled", "st-1", OrderStatus.FILLED),
        virtual_order("target-open", "st-1", OrderStatus.OPEN),
        virtual_order("target-closing", "st-1", OrderStatus.CLOSING),
    ]
    runtime.repository.sync_paper_state(
        runtime.order_book.orders,
        runtime.order_book.fills,
        market_prices={},
    )

    first = runtime.set_strategy_enabled("st-1", False)
    second = runtime.set_strategy_enabled("st-1", False)

    assert first["cancelled_pending"] == 2
    assert second["cancelled_pending"] == 0
    assert first["enabled"] is False
    persisted = {
        order.id: order for order in runtime.repository.list_paper_orders()
    }
    assert persisted["target-main"].status == OrderStatus.CANCELLED
    assert persisted["target-add-on"].status == OrderStatus.CANCELLED
    assert persisted["target-main"].closed_at is not None
    assert persisted["target-main"].metadata["intent"] == "CancelVirtualOrder"
    assert persisted["other-pending"].status == OrderStatus.PENDING
    assert persisted["target-opening"].status == OrderStatus.OPENING
    assert persisted["target-filled"].status == OrderStatus.FILLED
    assert persisted["target-open"].status == OrderStatus.OPEN
    assert persisted["target-closing"].status == OrderStatus.CLOSING


def test_live_pause_cancels_only_local_pending_without_webull_or_live_intent(
    tmp_path,
) -> None:
    client = FailOnCallLiveOrderClient()
    runtime = make_live_runtime(tmp_path, client)
    runtime.repository.seed_strategy_instances(
        [
            live_strategy(),
            StrategyInstance(
                id="st-other",
                strategy_name="recycle_buy",
                symbol="NASDAQ:AAPL",
            ),
        ]
    )
    runtime.live_virtual_book.orders = [
        virtual_order("live-pending", "st-live", OrderStatus.PENDING),
        virtual_order("other-live-pending", "st-other", OrderStatus.PENDING),
        virtual_order("live-opening", "st-live", OrderStatus.OPENING),
        virtual_order("live-open", "st-live", OrderStatus.OPEN),
        virtual_order("live-closing", "st-live", OrderStatus.CLOSING),
    ]
    runtime.repository.sync_live_virtual_state(runtime.live_virtual_book.orders)

    result = runtime.set_strategy_enabled("st-live", False)

    assert result["cancelled_pending"] == 1
    persisted = {
        order.id: order for order in runtime.repository.list_live_virtual_orders()
    }
    assert persisted["live-pending"].status == OrderStatus.CANCELLED
    assert persisted["other-live-pending"].status == OrderStatus.PENDING
    assert persisted["live-opening"].status == OrderStatus.OPENING
    assert persisted["live-open"].status == OrderStatus.OPEN
    assert persisted["live-closing"].status == OrderStatus.CLOSING
    assert runtime.repository.list_live_order_intents() == []
    assert client.calls == []


def test_resume_keeps_cancelled_entries_and_uses_reloaded_parameters(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.strategies["fresh_oco"] = FreshOcoStrategy()
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(
            id="st-oco",
            strategy_name="fresh_oco",
            symbol="NASDAQ:AAPL",
            params={"offset": 5},
        )
    )
    quote_item = {
        "code": "NASDAQ:AAPL",
        "bid": 99,
        "ask": 101,
        "last_price": 100,
        "delay_seconds": 0,
    }
    runtime.ingest_quote_item(quote_item)

    pause_result = runtime.set_strategy_enabled("st-oco", False)
    runtime.repository.replace_strategy_instances(
        [
            StrategyInstance(
                id="st-oco",
                strategy_name="fresh_oco",
                symbol="NASDAQ:AAPL",
                params={"offset": 20},
            )
        ]
    )
    reloaded = runtime.repository.list_strategy_instances()[0]
    resume_result = runtime.set_strategy_enabled("st-oco", True)
    runtime.ingest_quote_item(quote_item)

    assert pause_result["cancelled_pending"] == 2
    assert reloaded.enabled is False
    assert reloaded.params == {"offset": 20}
    assert resume_result["cancelled_pending"] == 0
    cancelled = [
        order
        for order in runtime.order_book.orders
        if order.status == OrderStatus.CANCELLED
    ]
    pending = [
        order
        for order in runtime.order_book.orders
        if order.status == OrderStatus.PENDING
    ]
    assert len(cancelled) == 2
    assert len(pending) == 2
    assert sorted(order.stop_price for order in pending) == [80, 120]


def test_pause_waits_for_running_evaluation_and_blocks_later_callbacks(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    strategy = BlockingPendingStrategy()
    runtime.strategies["blocking_pending"] = strategy
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(
            id="st-blocking",
            strategy_name="blocking_pending",
            symbol="NASDAQ:AAPL",
        )
    )
    quote_item = {
        "code": "NASDAQ:AAPL",
        "bid": 99,
        "ask": 101,
        "last_price": 100,
        "delay_seconds": 0,
    }
    evaluation_errors: list[BaseException] = []
    pause_result: dict[str, object] = {}
    pause_started = threading.Event()
    pause_finished = threading.Event()

    def evaluate() -> None:
        try:
            runtime.ingest_quote_item(quote_item)
        except BaseException as exc:
            evaluation_errors.append(exc)

    def pause() -> None:
        pause_started.set()
        pause_result.update(runtime.set_strategy_enabled("st-blocking", False))
        pause_finished.set()

    evaluation_thread = threading.Thread(target=evaluate)
    pause_thread = threading.Thread(target=pause)
    evaluation_thread.start()
    assert strategy.entered.wait(timeout=2)
    pause_thread.start()
    assert pause_started.wait(timeout=2)
    assert not pause_finished.wait(timeout=0.1)

    strategy.release.set()
    evaluation_thread.join(timeout=2)
    pause_thread.join(timeout=2)

    assert not evaluation_thread.is_alive()
    assert not pause_thread.is_alive()
    assert evaluation_errors == []
    assert pause_result["cancelled_pending"] == 1
    assert runtime.order_book.orders[0].status == OrderStatus.CANCELLED
    runtime.ingest_quote_item(quote_item)
    assert strategy.calls == 1


def test_test_reset_is_paused_isolated_persistent_and_idempotent(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.repository.seed_strategy_instances(
        [
            StrategyInstance(
                id="st-1",
                strategy_name="recycle_buy",
                symbol="NASDAQ:AAPL",
                enabled=False,
            ),
            StrategyInstance(
                id="st-2",
                strategy_name="recycle_buy",
                symbol="NASDAQ:AAPL",
                enabled=False,
            ),
        ]
    )
    target_open = virtual_order("target-open", "st-1", OrderStatus.OPEN)
    target_pending = virtual_order("target-pending", "st-1", OrderStatus.PENDING)
    other_open = virtual_order("other-open", "st-2", OrderStatus.OPEN)
    runtime.order_book.orders = [target_open, target_pending, other_open]
    runtime.quote_book.merge_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 110, "ask": 111}
    )
    strategy = runtime._strategy_for_instance(
        next(
            item
            for item in runtime.repository.list_strategy_instances()
            if item.id == "st-1"
        )
    )
    assert strategy is not None
    strategy.state_for(  # type: ignore[attr-defined]
        next(
            item
            for item in runtime.repository.list_strategy_instances()
            if item.id == "st-1"
        )
    ).active_cycle_id = target_open.cycle_id
    runtime.repository.upsert_strategy_runtime_state(
        "st-1",
        {"active_cycle_id": target_open.cycle_id},
    )
    runtime.repository.sync_paper_state(
        runtime.order_book.orders,
        runtime.order_book.fills,
        market_prices={"NASDAQ:AAPL": 110},
    )

    first = asyncio.run(runtime.reset_strategy("st-1"))
    second = asyncio.run(runtime.reset_strategy("st-1"))

    orders = {order.id: order for order in runtime.repository.list_paper_orders()}
    assert first["reconciled_allocations"] == 1
    assert first["cancelled_pending"] == 1
    assert first["completed_cycles"] == 1
    assert second["reconciled_allocations"] == 0
    assert second["cancelled_pending"] == 0
    assert second["completed_cycles"] == 0
    assert orders["target-open"].status == OrderStatus.CLOSED
    assert orders["target-open"].metadata["close_price"] == 110
    assert orders["target-pending"].status == OrderStatus.CANCELLED
    assert orders["other-open"].status == OrderStatus.OPEN
    assert runtime.repository.get_strategy_runtime_state("st-1") == {}
    assert strategy.state_for(  # type: ignore[attr-defined]
        next(
            item
            for item in runtime.repository.list_strategy_instances()
            if item.id == "st-1"
        )
    ).active_cycle_id is None
    target_cycle = next(
        item
        for item in runtime.repository.list_cycles(RuntimeMode.TEST)
        if item["id"] == target_open.cycle_id
    )
    assert target_cycle["status"] == "COMPLETED"
    assert all(
        not item.enabled
        for item in runtime.repository.list_strategy_instances()
        if item.id == "st-1"
    )


def test_test_reset_rejects_enabled_or_missing_quote_without_mutation(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    instance = StrategyInstance(
        id="st-reset",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
    )
    runtime.repository.upsert_strategy_instance(instance)
    order = virtual_order("target-open", instance.id, OrderStatus.OPEN)
    runtime.order_book.orders = [order]
    runtime.repository.sync_paper_state([order], [], market_prices={})

    with pytest.raises(StrategyResetBlocked, match="pause"):
        asyncio.run(runtime.reset_strategy(instance.id))
    runtime.repository.update_strategy_instance(instance.id, {"enabled": False})
    with pytest.raises(StrategyResetBlocked, match="no current paper close quote"):
        asyncio.run(runtime.reset_strategy(instance.id))

    assert runtime.order_book.orders[0].status == OrderStatus.OPEN
    assert runtime.repository.list_paper_orders()[0].status == OrderStatus.OPEN


def test_live_reconcile_reset_matches_manual_fill_and_preserves_other_strategy(
    tmp_path,
) -> None:
    filled_at = utc_now()
    client = ResetReadClient(
        positions=[{"symbol": "AAPL", "quantity": "2"}],
        history=[
            {
                "client_order_id": "manual-close-1",
                "order_id": "wb-manual-1",
                "symbol": "AAPL",
                "side": "SELL",
                "status": "FILLED",
                "filled_quantity": "0.4",
                "filled_price": "109",
                "filled_time_at": filled_at.isoformat(),
            },
            {
                "client_order_id": "manual-close-2",
                "order_id": "wb-manual-2",
                "symbol": "AAPL",
                "side": "SELL",
                "status": "FILLED",
                "filled_quantity": "0.6",
                "filled_price": "111",
                "filled_time_at": filled_at.isoformat(),
            }
        ],
    )
    runtime = make_live_runtime(tmp_path, client)
    target = live_strategy()
    target.enabled = False
    other = StrategyInstance(
        id="st-other",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
        webull_symbol="AAPL",
        account_alias="stock_margin",
        asset_class="stock",
        enabled=True,
        live_execution_enabled=True,
    )
    runtime.repository.seed_strategy_instances([target, other])
    target_order = virtual_order("target-open", target.id, OrderStatus.OPEN)
    target_order.opened_at = filled_at - timedelta(minutes=5)
    target_order.metadata["broker_fill_confirmed"] = True
    other_order = virtual_order("other-open", other.id, OrderStatus.OPEN)
    other_order.quantity = 2
    other_order.opened_at = filled_at - timedelta(minutes=10)
    other_order.metadata["broker_fill_confirmed"] = True
    runtime.live_virtual_book.orders = [target_order, other_order]
    runtime.repository.sync_live_virtual_state(runtime.live_virtual_book.orders)
    opening_intent = LiveOrderIntent(
        id="loi-opening",
        strategy_instance_id=target.id,
        cycle_id=target_order.cycle_id,
        action=LiveIntentAction.OPEN_MARKET,
        side=OrderSide.BUY,
        quantity=1,
        market_data_symbol=target.symbol,
        webull_symbol=target.webull_symbol,
        account_alias=target.account_alias,
        account_id="acct-1",
        client_order_id="wat-opening",
        status=LiveIntentStatus.FILLED,
    )
    runtime.repository.upsert_live_order_intent(opening_intent)
    runtime.repository.upsert_live_symbol_reconciliation(
        account_alias="stock_margin",
        webull_symbol="AAPL",
        external_baseline_quantity=0,
        observed_position=3,
        expected_position=3,
        status="READY",
    )

    result = asyncio.run(runtime.reset_strategy(target.id))
    second = asyncio.run(runtime.reset_strategy(target.id))

    orders = {
        order.id: order
        for order in runtime.repository.list_live_virtual_orders()
    }
    cycle = next(
        item
        for item in runtime.repository.list_cycles(RuntimeMode.LIVE)
        if item["id"] == target_order.cycle_id
    )
    reconciliation = runtime.repository.get_live_symbol_reconciliation(
        "stock_margin",
        "AAPL",
    )
    assert result["broker_position_observed"] == 2
    assert result["expected_broker_position"] == 2
    assert result["reconciled_allocations"] == 1
    assert result["completed_cycles"] == 1
    assert second["reconciled_allocations"] == 0
    assert orders["target-open"].status == OrderStatus.CLOSED
    assert orders["target-open"].metadata["close_reason"] == "manual_broker_close"
    assert orders["target-open"].metadata["manual_broker_order_ids"] == [
        "wb-manual-1",
        "wb-manual-2",
    ]
    assert orders["target-open"].metadata[
        "manual_close_fill_price"
    ] == pytest.approx(110.2)
    assert orders["other-open"].status == OrderStatus.OPEN
    assert cycle["status"] == "COMPLETED"
    assert cycle["realized_pnl"] == pytest.approx(10.2)
    assert reconciliation is not None
    assert reconciliation["external_baseline_quantity"] == 0
    assert reconciliation["status"] == "READY"
    assert client.calls.count("positions") == 2
    assert "place_order" not in client.calls


def test_live_reset_rejects_working_order_without_local_mutation(tmp_path) -> None:
    filled_at = utc_now()
    client = ResetReadClient(
        positions=[],
        open_orders=[
            {
                "client_order_id": "manual-working",
                "symbol": "AAPL",
                "status": "SUBMITTED",
            }
        ],
        history=[],
    )
    runtime = make_live_runtime(tmp_path, client)
    strategy = live_strategy()
    strategy.enabled = False
    runtime.repository.upsert_strategy_instance(strategy)
    runtime.repository.upsert_live_symbol_reconciliation(
        account_alias="stock_margin",
        webull_symbol="AAPL",
        external_baseline_quantity=0,
        observed_position=0,
        expected_position=0,
        status="READY",
    )
    order = virtual_order("target-open", strategy.id, OrderStatus.OPEN)
    order.opened_at = filled_at - timedelta(minutes=5)
    order.metadata["broker_fill_confirmed"] = True
    runtime.live_virtual_book.orders = [order]
    runtime.repository.sync_live_virtual_state([order])

    with pytest.raises(StrategyResetBlocked, match="broker order is still working"):
        asyncio.run(runtime.reset_strategy(strategy.id))

    assert runtime.live_virtual_book.orders[0].status == OrderStatus.OPEN
    assert runtime.repository.list_live_virtual_orders()[0].status == OrderStatus.OPEN


@pytest.mark.parametrize(
    "status",
    [
        LiveIntentStatus.PENDING_SUBMIT,
        LiveIntentStatus.SUBMITTED,
        LiveIntentStatus.ACCEPTED,
        LiveIntentStatus.PARTIAL_FILLED,
        LiveIntentStatus.UNKNOWN,
        LiveIntentStatus.DESYNCED,
    ],
)
def test_live_reset_rejects_every_unresolved_intent_before_webull_reads(
    tmp_path,
    status,
) -> None:
    client = FailOnResetReadClient()
    runtime = make_live_runtime(tmp_path, client)
    strategy = live_strategy()
    strategy.enabled = False
    runtime.repository.upsert_strategy_instance(strategy)
    runtime.repository.upsert_live_order_intent(
        LiveOrderIntent(
            id=f"intent-{status.value}",
            strategy_instance_id=strategy.id,
            cycle_id="cycle-1",
            action=LiveIntentAction.OPEN_MARKET,
            side=OrderSide.BUY,
            quantity=1,
            market_data_symbol=strategy.symbol,
            webull_symbol=strategy.webull_symbol,
            account_alias=strategy.account_alias,
            account_id="acct-1",
            client_order_id=f"client-{status.value}",
            status=status,
        )
    )

    with pytest.raises(StrategyResetBlocked, match=status.value.lower()):
        asyncio.run(runtime.reset_strategy(strategy.id))

    assert client.calls == []


def test_stateful_strategy_without_reset_contract_is_rejected(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.strategies["unsafe_stateful"] = UnsafeStatefulStrategy()
    runtime.repository.upsert_strategy_instance(
        StrategyInstance(
            id="st-unsafe",
            strategy_name="unsafe_stateful",
            symbol="NASDAQ:AAPL",
            enabled=False,
        )
    )

    with pytest.raises(StrategyResetBlocked, match="does not implement reset_state"):
        asyncio.run(runtime.reset_strategy("st-unsafe"))

    assert runtime.repository.count_activity_events("StrategyStateReset") == 0


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
    mark_live_strategy_ready(runtime)

    runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 100, "ask": 101, "last_price": 100.5, "delay_seconds": 0}
    )
    first_intent = runtime.repository.list_live_order_intents()[0]
    first_intent.status = LiveIntentStatus.FILLED
    first_intent.response = {
        "orders": [{"status": "FILLED", "filled_price": "102"}]
    }
    runtime.repository.upsert_live_order_intent(first_intent)
    runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 107, "ask": 108, "last_price": 107.5, "delay_seconds": 0}
    )

    assert [order["side"] for _account_id, order in client.orders] == ["BUY", "SELL"]
    assert all(order["order_type"] == "MARKET" for _account_id, order in client.orders)
    assert all(account_id == "acct-1" for account_id, _order in client.orders)
    assert [intent.action.value for intent in runtime.repository.list_live_order_intents()] == [
        "CLOSE_MARKET",
        "OPEN_MARKET",
    ]
    virtual_order = runtime.repository.list_live_virtual_orders()[0]
    assert virtual_order.status == OrderStatus.CLOSING
    assert virtual_order.fill_price == 102
    assert virtual_order.stop_loss == 97
    assert virtual_order.take_profit == 107
    assert virtual_order.metadata["risk_from_fill"] is True
    assert runtime.repository.list_cycles(RuntimeMode.LIVE)[0]["runtime_mode"] == "live"


def test_live_recycle_buy_does_not_submit_twice_while_opening(tmp_path) -> None:
    client = FakeLiveOrderClient()
    runtime = make_live_runtime(tmp_path, client)
    runtime.repository.upsert_strategy_instance(live_strategy())
    mark_live_strategy_ready(runtime)
    quote_item = {
        "code": "NASDAQ:AAPL",
        "bid": 100,
        "ask": 101,
        "last_price": 100.5,
        "delay_seconds": 0,
    }

    runtime.ingest_quote_item(quote_item)
    runtime.ingest_quote_item(quote_item)

    assert len(client.orders) == 1
    assert len(runtime.repository.list_live_order_intents()) == 1
    assert len(runtime.repository.list_live_virtual_orders()) == 1
    assert runtime.repository.list_live_virtual_orders()[0].status == OrderStatus.OPENING


def test_live_order_rejection_pauses_strategy(tmp_path) -> None:
    runtime = make_live_runtime(tmp_path, RejectingLiveOrderClient())
    runtime.repository.upsert_strategy_instance(live_strategy())
    mark_live_strategy_ready(runtime)

    runtime.ingest_quote_item(
        {"code": "NASDAQ:AAPL", "bid": 100, "ask": 101, "last_price": 100.5, "delay_seconds": 0}
    )

    instance = runtime.repository.list_strategy_instances()[0]
    intent = runtime.repository.list_live_order_intents()[0]
    virtual_order = runtime.repository.list_live_virtual_orders()[0]
    assert instance.enabled is False
    assert intent.status == LiveIntentStatus.REJECTED
    assert virtual_order.status == OrderStatus.ERROR
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


def mark_live_strategy_ready(runtime: RuntimeService) -> None:
    runtime.ingest_live_account_state(
        LiveAccountState(
            account_alias="stock_margin",
            total_net_liquidation_value=100_000,
            observed_at=utc_now(),
            strategy_instance_id="st-live",
            previews_ready=True,
            reconciliation_ready=True,
        )
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


class FailOnCallLiveOrderClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def place_order(self, account_id: str, order: dict[str, str]) -> dict[str, str]:
        self.calls.append("place_order")
        raise AssertionError("pause must not submit a Webull order")


class ResetReadClient:
    def __init__(
        self,
        *,
        positions: list[dict],
        history: list[dict],
        open_orders: list[dict] | None = None,
    ) -> None:
        self.position_rows = positions
        self.history_rows = history
        self.open_order_rows = list(open_orders or [])
        self.calls: list[str] = []

    async def positions(self, account_id: str) -> list[dict]:
        assert account_id == "acct-1"
        self.calls.append("positions")
        return self.position_rows

    async def open_orders(self, account_id: str) -> list[dict]:
        assert account_id == "acct-1"
        self.calls.append("open_orders")
        return self.open_order_rows

    async def order_history(self, account_id: str) -> list[dict]:
        assert account_id == "acct-1"
        self.calls.append("order_history")
        return self.history_rows

    async def order_detail(
        self,
        account_id: str,
        client_order_id: str,
    ) -> dict:
        assert account_id == "acct-1"
        self.calls.append("order_detail")
        return {
            "orders": [
                row
                for row in self.history_rows
                if row.get("client_order_id") == client_order_id
            ]
        }

    async def place_order(self, account_id: str, order: dict) -> dict:
        self.calls.append("place_order")
        raise AssertionError("reset must not place an order")


class FailOnResetReadClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def fail(*_args, **_kwargs):
            self.calls.append(name)
            raise AssertionError("unsafe reset must fail before Webull reads")

        return fail


class UnsafeStatefulStrategy(Strategy):
    def export_state(self, instance: StrategyInstance) -> dict:
        return {"strategy_instance_id": instance.id, "active": True}

    def on_quote(
        self,
        instance: StrategyInstance,
        quote,
        order_book: PaperOrderBook,
    ) -> list[str]:
        return []


class FreshOcoStrategy(Strategy):
    def on_quote(
        self,
        instance: StrategyInstance,
        quote,
        order_book: PaperOrderBook,
    ) -> list[str]:
        if (
            order_book.pending_for_instance(instance.id)
            or order_book.open_for_instance(instance.id)
            or quote.mid_price is None
        ):
            return []
        offset = float(instance.params["offset"])
        cycle_id = new_id("cyc")
        for side, stop_price in (
            (OrderSide.BUY, quote.mid_price + offset),
            (OrderSide.SELL, quote.mid_price - offset),
        ):
            order_book.place_virtual_stop(
                strategy_instance_id=instance.id,
                cycle_id=cycle_id,
                symbol=instance.symbol,
                side=side,
                role=OrderRole.MAIN,
                quantity=1,
                stop_price=stop_price,
                stop_loss=stop_price,
                take_profit=stop_price,
            )
        return ["PlaceVirtualStop"]


class BlockingPendingStrategy(Strategy):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def on_quote(
        self,
        instance: StrategyInstance,
        quote,
        order_book: PaperOrderBook,
    ) -> list[str]:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release strategy evaluation")
        order_book.place_virtual_stop(
            strategy_instance_id=instance.id,
            cycle_id=new_id("cyc"),
            symbol=instance.symbol,
            side=OrderSide.BUY,
            role=OrderRole.MAIN,
            quantity=1,
            stop_price=(quote.ask or 0) + 10,
            stop_loss=1,
            take_profit=2,
        )
        return []


def virtual_order(
    order_id: str,
    strategy_id: str,
    status: OrderStatus,
    *,
    role: OrderRole = OrderRole.MAIN,
) -> PaperOrder:
    return PaperOrder(
        id=order_id,
        strategy_instance_id=strategy_id,
        cycle_id=f"cycle-{order_id}",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=role,
        quantity=1,
        status=status,
        stop_price=110,
        fill_price=100 if status != OrderStatus.PENDING else None,
    )
