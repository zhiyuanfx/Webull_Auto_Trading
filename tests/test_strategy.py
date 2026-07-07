from datetime import UTC, datetime, timedelta

from webull_auto_trading.domain import (
    ExecutionMode,
    IntentType,
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
