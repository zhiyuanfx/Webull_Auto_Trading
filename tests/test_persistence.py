import sqlite3

from webull_auto_trading.domain import (
    ExecutionMode,
    LiveIntentAction,
    LiveIntentStatus,
    LiveOrderIntent,
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
            strategy_name="recycle_buy",
            symbol="NASDAQ:AAPL",
            mode=ExecutionMode.PAPER,
            webull_symbol="AAPL",
            account_alias="stock_margin",
            asset_class="stock",
            live_execution_enabled=True,
            params={"lots": 0.5},
        )
    )

    restarted = RuntimeRepository(db_path)
    instances = restarted.list_strategy_instances()

    assert created.id == "st-1"
    assert len(instances) == 1
    assert instances[0].mode == ExecutionMode.PAPER
    assert instances[0].market_data_symbol == "NASDAQ:AAPL"
    assert instances[0].webull_symbol == "AAPL"
    assert instances[0].account_alias == "stock_margin"
    assert instances[0].asset_class == "stock"
    assert instances[0].live_execution_enabled is True
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


def test_replace_strategy_instances_preserves_operator_pause(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()
    repo.upsert_strategy_instance(
        StrategyInstance(
            id="st-1",
            strategy_name="recycle_buy",
            symbol="NASDAQ:AAPL",
            enabled=False,
        )
    )

    repo.replace_strategy_instances(
        [
            StrategyInstance(
                id="st-1",
                strategy_name="recycle_buy",
                symbol="NASDAQ:AAPL",
                enabled=True,
            ),
            StrategyInstance(
                id="st-2",
                strategy_name="recycle_buy",
                symbol="NASDAQ:MSFT",
                enabled=True,
            ),
        ]
    )

    instances = {item.id: item for item in repo.list_strategy_instances()}

    assert instances["st-1"].enabled is False
    assert instances["st-2"].enabled is True


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


def test_existing_cycles_migrate_to_test_runtime_mode(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE cycles (
                id TEXT PRIMARY KEY,
                strategy_instance_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                realized_pnl REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cycles(
                id, strategy_instance_id, symbol, status, opened_at, metadata_json
            )
            VALUES ('cyc-legacy', 'st-1', 'NASDAQ:AAPL', 'OPEN', '2026-01-01T00:00:00+00:00', '{}')
            """
        )

    repo = RuntimeRepository(db_path)
    repo.init_db()

    assert repo.list_cycles(RuntimeMode.TEST)[0]["id"] == "cyc-legacy"
    assert repo.list_cycles(RuntimeMode.LIVE) == []


def test_paper_reset_and_cleanup_preserve_live_order_storage(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()
    paper_order = PaperOrderBook().place_market_order(
        strategy_instance_id="st-paper",
        cycle_id="cyc-paper",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        fill_price=100,
        stop_loss=95,
        take_profit=105,
    )
    paper_order.status = OrderStatus.CLOSED
    paper_order.metadata["close_price"] = 101
    paper_order.closed_at = paper_order.opened_at
    repo.sync_paper_state([paper_order], [], market_prices={})
    live_order = PaperOrderBook().place_market_order(
        strategy_instance_id="st-live",
        cycle_id="cyc-live",
        symbol="NASDAQ:AAPL",
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        fill_price=100,
        stop_loss=95,
        take_profit=105,
    )
    live_order.status = OrderStatus.CLOSED
    live_order.metadata["close_price"] = 102
    live_order.closed_at = live_order.opened_at
    repo.sync_live_virtual_state([live_order])
    intent = LiveOrderIntent(
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
    )
    repo.upsert_live_order_intent(intent)

    repo.reset_paper_account()

    assert repo.list_paper_orders() == []
    assert repo.list_cycles(RuntimeMode.TEST) == []
    assert repo.list_live_virtual_orders()[0].id == live_order.id
    assert repo.list_live_order_intents()[0].id == "loi-live"
    assert repo.list_cycles(RuntimeMode.LIVE)[0]["id"] == "cyc-live"

    with repo.connect() as conn:
        conn.execute(
            """
            UPDATE cycles
            SET status = 'COMPLETED', closed_at = '2000-01-01T00:00:00+00:00'
            """
        )
    repo.cleanup(dry_run=False, closed_cycle_days=1)

    assert repo.list_cycles(RuntimeMode.LIVE)[0]["id"] == "cyc-live"


def test_live_order_intents_persist_and_block_unresolved(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()
    intent = LiveOrderIntent(
        id="loi-1",
        strategy_instance_id="st-1",
        cycle_id="cyc-1",
        action=LiveIntentAction.OPEN_MARKET,
        side=OrderSide.BUY,
        quantity=1,
        market_data_symbol="NASDAQ:AAPL",
        webull_symbol="AAPL",
        account_alias="stock_margin",
        account_id="acct-1",
        client_order_id="client-1",
        request={"order_type": "MARKET"},
    )

    repo.upsert_live_order_intent(intent)

    restarted = RuntimeRepository(db_path)
    intents = restarted.list_live_order_intents()
    assert intents[0].client_order_id == "client-1"
    assert intents[0].status == LiveIntentStatus.PENDING_SUBMIT
    assert restarted.has_unresolved_live_intent("st-1") is True

    intent.status = LiveIntentStatus.FILLED
    restarted.upsert_live_order_intent(intent)

    assert restarted.has_unresolved_live_intent("st-1") is False
