from webull_auto_trading.domain import (
    ExecutionMode,
    OrderRole,
    OrderSide,
    OrderStatus,
    RuntimeMode,
    StrategyInstance,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.persistence import RuntimeRepository


def test_sqlite_strategy_instances_survive_repository_restart(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()
    created = repo.upsert_strategy_instance(
        StrategyInstance(
            id="st-1",
            strategy_name="day_many_bian",
            symbol="NASDAQ:AAPL",
            mode=ExecutionMode.PAPER,
            params={"lots": 0.5},
        )
    )

    restarted = RuntimeRepository(db_path)
    instances = restarted.list_strategy_instances()

    assert created.id == "st-1"
    assert len(instances) == 1
    assert instances[0].mode == ExecutionMode.PAPER
    assert instances[0].params == {"lots": 0.5}


def test_runtime_mode_and_paper_account_persist(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()

    assert repo.get_runtime_mode() == RuntimeMode.TEST
    repo.set_runtime_mode(RuntimeMode.LIVE)
    repo.deposit_paper_account(250)

    restarted = RuntimeRepository(db_path)
    assert restarted.get_runtime_mode() == RuntimeMode.LIVE
    assert restarted.get_active_paper_account().starting_balance == 10_250


def test_paper_orders_fills_and_positions_restore(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()
    book = PaperOrderBook()
    order = book.place_virtual_stop(
        strategy_instance_id="st-1",
        cycle_id="cyc-1",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=2,
        stop_price=100,
        stop_loss=95,
        take_profit=120,
    )
    order.status = OrderStatus.FILLED
    order.fill_price = 100
    repo.sync_paper_state(book.orders, book.fills, market_prices={"NASDAQ:AAPL": 105})

    restarted = RuntimeRepository(db_path)
    orders = restarted.list_paper_orders()
    positions = restarted.list_paper_positions()
    account = restarted.recompute_paper_account(market_prices={"NASDAQ:AAPL": 105})

    assert orders[0].id == order.id
    assert positions[0].quantity == 2
    assert account.unrealized_pnl == 10
