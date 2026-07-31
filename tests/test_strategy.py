from datetime import UTC, datetime, timedelta

from webull_auto_trading.domain import (
    ExecutionMode,
    IntentType,
    LiveAccountState,
    OrderRole,
    OrderSide,
    OrderStatus,
    QuoteState,
    StrategyInstance,
    utc_now,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.risk import daily_loss_limit_hit
from webull_auto_trading.strategy.recycle_buy import RecycleBuyStrategy


def quote(symbol: str, bid: float, ask: float) -> QuoteState:
    now = datetime(2026, 7, 5, 16, 0, tzinfo=UTC)
    return QuoteState(
        symbol,
        {
            "code": symbol,
            "bid": bid,
            "ask": ask,
            "last_price": (bid + ask) / 2,
            "lp_time": now.timestamp(),
            "delay_seconds": 0,
        },
        received_at=now,
    )


def test_virtual_pending_order_fills_on_bid_ask_crossing() -> None:
    book = PaperOrderBook()
    order = book.place_virtual_stop(
        strategy_instance_id="st-1",
        cycle_id="cyc-1",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        stop_price=101,
        stop_loss=94,
        take_profit=120,
    )

    fills = book.apply_quote(quote("NASDAQ:AAPL", 100.5, 101.2))

    assert len(fills) == 1
    assert fills[0].order_id == order.id
    assert order.fill_price == 101


def test_market_order_opens_immediately_with_fill_record() -> None:
    book = PaperOrderBook()

    order = book.place_market_order(
        strategy_instance_id="st-1",
        cycle_id="cyc-1",
        symbol="COMEX_MINI:MGCQ2026",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        fill_price=2500,
        stop_loss=2495,
        take_profit=2505,
    )

    assert order.status == OrderStatus.FILLED
    assert order.metadata["intent"] == IntentType.PLACE_MARKET_ORDER.value
    assert book.fills[0].price == 2500


def test_recycle_buy_opens_closes_and_waits_for_cooldown() -> None:
    strategy = RecycleBuyStrategy()
    book = PaperOrderBook()
    instance = StrategyInstance(
        id="st-mgc",
        strategy_name="recycle_buy",
        symbol="COMEX_MINI:MGCQ2026",
        params={
            "lots": 1,
            "stop_loss_distance_price": 5,
            "take_profit_distance_price": 5,
            "cooldown_seconds": 5,
        },
    )

    messages = strategy.on_quote(instance, quote("COMEX_MINI:MGCQ2026", 2499.5, 2500), book)

    assert messages == [IntentType.PLACE_MARKET_ORDER.value, IntentType.PAPER_FILL.value]
    assert book.open_for_instance(instance.id)[0].stop_loss == 2495
    assert book.open_for_instance(instance.id)[0].take_profit == 2505

    messages = strategy.on_quote(instance, quote("COMEX_MINI:MGCQ2026", 2505, 2505.5), book)

    assert messages == [IntentType.CLOSE_CYCLE.value]
    assert book.orders[0].status == OrderStatus.CLOSED
    assert len(book.orders) == 1

    state = strategy.state_for(instance)
    state.next_trade_time = utc_now() - timedelta(seconds=1)
    messages = strategy.on_quote(instance, quote("COMEX_MINI:MGCQ2026", 2504.5, 2505), book)

    assert messages == [IntentType.PLACE_MARKET_ORDER.value, IntentType.PAPER_FILL.value]
    assert len(book.orders) == 2


def test_recycle_buy_live_entry_requires_fresh_ready_account_state() -> None:
    strategy = RecycleBuyStrategy()
    book = PaperOrderBook()
    instance = StrategyInstance(
        id="st-live-mgc",
        strategy_name="recycle_buy",
        symbol="COMEX_MINI:MGCQ2026",
        account_alias="futures",
        asset_class="futures",
        live_execution_enabled=True,
        params={
            "lots": 1,
            "stop_loss_distance_price": 5,
            "take_profit_distance_price": 5,
            "cooldown_seconds": 5,
        },
    )
    current_quote = quote("COMEX_MINI:MGCQ2026", 2499.5, 2500)

    assert strategy.on_quote(instance, current_quote, book) == []
    assert book.orders == []

    strategy.on_account_snapshot(
        instance,
        LiveAccountState(
            account_alias="futures",
            total_net_liquidation_value=100_000,
            observed_at=utc_now(),
            strategy_instance_id=instance.id,
            previews_ready=True,
            reconciliation_ready=True,
        ),
        book,
    )
    messages = strategy.on_quote(instance, current_quote, book)

    assert messages == [IntentType.PLACE_MARKET_ORDER.value, IntentType.PAPER_FILL.value]
    assert book.orders[0].metadata["stop_loss_distance_price"] == 5
    assert book.orders[0].metadata["take_profit_distance_price"] == 5


def test_recycle_buy_does_not_duplicate_inflight_live_allocations() -> None:
    for status in (OrderStatus.OPENING, OrderStatus.CLOSING):
        strategy = RecycleBuyStrategy()
        book = PaperOrderBook()
        instance = StrategyInstance(
            id=f"st-{status.value.lower()}",
            strategy_name="recycle_buy",
            symbol="COMEX_MINI:MGCQ2026",
        )
        existing = book.place_market_order(
            strategy_instance_id=instance.id,
            cycle_id="cyc-existing",
            symbol=instance.symbol,
            side=OrderSide.BUY,
            role=OrderRole.MAIN,
            quantity=1,
            fill_price=2500,
            stop_loss=2495,
            take_profit=2505,
        )
        existing.status = status

        messages = strategy.on_quote(
            instance,
            quote("COMEX_MINI:MGCQ2026", 2499.5, 2500),
            book,
        )

        assert messages == []
        assert len(book.orders) == 1
        assert strategy.state_for(instance).active_cycle_id == "cyc-existing"


def test_recycle_buy_manages_only_its_own_same_symbol_order() -> None:
    strategy = RecycleBuyStrategy()
    book = PaperOrderBook()
    target = StrategyInstance(
        id="st-target",
        strategy_name="recycle_buy",
        symbol="COMEX_MINI:MGCQ2026",
        params={"cooldown_seconds": 5},
    )
    target_order = book.place_market_order(
        strategy_instance_id=target.id,
        cycle_id="cyc-target",
        symbol=target.symbol,
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        fill_price=2500,
        stop_loss=2495,
        take_profit=2505,
    )
    other_order = book.place_market_order(
        strategy_instance_id="st-other",
        cycle_id="cyc-other",
        symbol=target.symbol,
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        fill_price=2500,
        stop_loss=2495,
        take_profit=2505,
    )

    messages = strategy.on_quote(
        target,
        quote("COMEX_MINI:MGCQ2026", 2494.5, 2495),
        book,
    )

    assert messages == [IntentType.CLOSE_CYCLE.value]
    assert target_order.status == OrderStatus.CLOSED
    assert other_order.status == OrderStatus.FILLED


def test_recycle_buy_persists_control_state_without_live_readiness() -> None:
    instance = StrategyInstance(
        id="st-state",
        strategy_name="recycle_buy",
        symbol="COMEX_MINI:MGCQ2026",
    )
    strategy = RecycleBuyStrategy()
    state = strategy.state_for(instance)
    state.active_cycle_id = "cyc-state"
    state.next_trade_time = utc_now() + timedelta(seconds=5)
    state.previews_ready = True
    state.reconciliation_ready = True

    exported = strategy.export_state(instance)
    restored = RecycleBuyStrategy()
    restored.import_state(instance, exported)
    restored_state = restored.state_for(instance)

    assert restored_state.active_cycle_id == "cyc-state"
    assert restored_state.next_trade_time == state.next_trade_time
    assert restored_state.previews_ready is False
    assert restored_state.reconciliation_ready is False


def test_recycle_buy_reset_state_clears_only_selected_instance() -> None:
    first = StrategyInstance(
        id="st-first",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
    )
    second = StrategyInstance(
        id="st-second",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
    )
    strategy = RecycleBuyStrategy()
    strategy.state_for(first).active_cycle_id = "cycle-first"
    strategy.state_for(first).next_trade_time = utc_now() + timedelta(minutes=1)
    strategy.state_for(first).previews_ready = True
    strategy.state_for(second).active_cycle_id = "cycle-second"

    strategy.reset_state(first)

    assert strategy.state_for(first).active_cycle_id is None
    assert strategy.state_for(first).next_trade_time is None
    assert strategy.state_for(first).previews_ready is False
    assert strategy.state_for(second).active_cycle_id == "cycle-second"


def test_order_book_isolates_two_strategy_instances_on_same_quote() -> None:
    book = PaperOrderBook()
    book.place_virtual_stop(
        strategy_instance_id="st-a",
        cycle_id="cyc-a",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role="MAIN",
        quantity=1,
        stop_price=101,
        stop_loss=94,
        take_profit=120,
    )
    book.place_virtual_stop(
        strategy_instance_id="st-b",
        cycle_id="cyc-b",
        symbol="NASDAQ:MSFT",
        side=OrderSide.BUY,
        role="MAIN",
        quantity=1,
        stop_price=201,
        stop_loss=194,
        take_profit=220,
    )

    fills = book.apply_quote(quote("NASDAQ:AAPL", 100.5, 101.2))

    assert [fill.strategy_instance_id for fill in fills] == ["st-a"]
    assert book.pending_for_instance("st-b")


def test_global_vs_instance_daily_loss_lock_logic() -> None:
    assert daily_loss_limit_hit(
        day_start_equity=100_000,
        current_equity=89_900,
        daily_loss_limit_percent=10,
    )
    assert not daily_loss_limit_hit(
        day_start_equity=100_000,
        current_equity=91_000,
        daily_loss_limit_percent=10,
    )


def test_strategy_instance_model_uses_paper_mode() -> None:
    instance = StrategyInstance(
        id="st-1",
        strategy_name="recycle_buy",
        symbol="NASDAQ:AAPL",
        mode=ExecutionMode.PAPER,
    )

    assert instance.mode == ExecutionMode.PAPER
