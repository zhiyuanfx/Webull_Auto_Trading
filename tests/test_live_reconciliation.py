from __future__ import annotations

import asyncio

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
    LiveIntentAction,
    LiveIntentStatus,
    LiveOrderIntent,
    OrderRole,
    OrderSide,
    OrderStatus,
    PaperOrder,
    RuntimeMode,
    StrategyInstance,
)
from webull_auto_trading.live_reconciliation import LiveRuntimeCoordinator
from webull_auto_trading.runtime import RuntimeService


def test_order_detail_partial_then_final_uses_actual_fill_price(tmp_path) -> None:
    client = FakeCoordinatorClient(
        details=[
            {
                "status": "PARTIAL_FILLED",
                "filled_quantity": "0.5",
                "avg_filled_price": "2501.5",
            },
            {
                "status": "FILLED",
                "filled_quantity": "1",
                "avg_filled_price": "2502.25",
            },
        ]
    )
    runtime = make_runtime(tmp_path, client)
    strategy = live_strategy("st-mgc")
    runtime.repository.upsert_strategy_instance(strategy)
    order = opening_order(strategy)
    runtime.live_virtual_book.orders.append(order)
    intent = live_intent(strategy, order)
    runtime.repository.upsert_live_order_intent(intent)
    runtime.repository.sync_live_virtual_state(runtime.live_virtual_book.orders)
    coordinator = LiveRuntimeCoordinator(runtime, client=client)

    asyncio.run(coordinator.reconcile_one_order())
    assert order.status == OrderStatus.OPENING
    assert order.metadata["broker_filled_quantity"] == 0.5

    asyncio.run(coordinator.reconcile_one_order())
    assert order.status == OrderStatus.OPEN
    assert order.fill_price == 2502.25
    assert order.stop_loss == 2495.25
    assert order.take_profit == 3502.25
    assert order.metadata["broker_fill_confirmed"] is True


def test_grouped_exit_closes_only_tagged_strategy_allocations(tmp_path) -> None:
    client = FakeCoordinatorClient(
        details=[{"status": "FILLED", "filled_quantity": "2", "avg_filled_price": "2499"}]
    )
    runtime = make_runtime(tmp_path, client)
    first_strategy = live_strategy("st-first")
    other_strategy = live_strategy("st-other")
    runtime.repository.upsert_strategy_instance(first_strategy)
    runtime.repository.upsert_strategy_instance(other_strategy)
    first = filled_order(first_strategy, "order-1")
    add_on = filled_order(first_strategy, "order-2", role=OrderRole.ADD_ON)
    other = filled_order(other_strategy, "order-other")
    first.status = add_on.status = OrderStatus.CLOSING
    runtime.live_virtual_book.orders.extend([first, add_on, other])
    intent = live_intent(
        first_strategy,
        first,
        action=LiveIntentAction.CLOSE_MARKET,
        side=OrderSide.SELL,
        virtual_order_ids=[first.id, add_on.id],
        quantity=2,
    )
    runtime.repository.upsert_live_order_intent(intent)
    coordinator = LiveRuntimeCoordinator(runtime, client=client)

    asyncio.run(coordinator.reconcile_one_order())

    assert first.status == OrderStatus.CLOSED
    assert add_on.status == OrderStatus.CLOSED
    assert other.status == OrderStatus.FILLED


def test_external_baseline_then_unexplained_mismatch_pauses_same_symbol(tmp_path) -> None:
    client = FakeCoordinatorClient()
    runtime = make_runtime(tmp_path, client)
    first = live_strategy("st-first")
    second = live_strategy("st-second")
    runtime.repository.upsert_strategy_instance(first)
    runtime.repository.upsert_strategy_instance(second)
    runtime.live_virtual_book.orders.extend(
        [
            filled_order(first, "buy", side=OrderSide.BUY),
            filled_order(second, "sell", side=OrderSide.SELL),
        ]
    )
    coordinator = LiveRuntimeCoordinator(runtime, client=client)
    instances = runtime.repository.list_strategy_instances()

    coordinator._reconcile_symbol(
        "futures",
        "MGCQ6",
        [{"symbol": "MGCQ6", "quantity": "3"}],
        instances,
    )
    ready = runtime.repository.get_live_symbol_reconciliation("futures", "MGCQ6")
    assert ready is not None
    assert ready["external_baseline_quantity"] == 3
    assert ready["status"] == "READY"

    coordinator._reconcile_symbol(
        "futures",
        "MGCQ6",
        [{"symbol": "MGCQ6", "quantity": "4"}],
        instances,
    )
    mismatch = runtime.repository.get_live_symbol_reconciliation("futures", "MGCQ6")
    assert mismatch is not None
    assert mismatch["status"] == "MISMATCH"
    assert all(
        not instance.enabled
        for instance in runtime.repository.list_strategy_instances()
    )


def make_runtime(tmp_path, client) -> RuntimeService:
    runtime = RuntimeService(
        Settings(
            runtime_db_path=tmp_path / "runtime.sqlite3",
            strategies_test_config_path=tmp_path / "test.yml",
            strategies_live_config_path=tmp_path / "live.yml",
            webull_account_futures_id="acct-futures",
            live_execution_master_enable=True,
            _env_file=None,
        ),
        live_order_client=client,
    )
    runtime.initialize(seed_config=False)
    runtime.repository.set_runtime_mode(RuntimeMode.LIVE)
    return runtime


def live_strategy(strategy_id: str) -> StrategyInstance:
    return StrategyInstance(
        id=strategy_id,
        strategy_name="recycle_buy",
        symbol="COMEX_MINI:MGCQ2026",
        market_data_symbol="COMEX_MINI:MGCQ2026",
        webull_symbol="MGCQ6",
        account_alias="futures",
        asset_class="futures",
        enabled=True,
        live_execution_enabled=True,
    )


def opening_order(strategy: StrategyInstance) -> PaperOrder:
    return PaperOrder(
        id="opening",
        strategy_instance_id=strategy.id,
        cycle_id="cycle-1",
        symbol=strategy.symbol,
        side=OrderSide.BUY,
        role=OrderRole.MAIN,
        quantity=1,
        status=OrderStatus.OPENING,
        fill_price=2498,
        metadata={
            "live_intent_id": "intent-1",
            "stop_loss_distance_price": 7,
            "take_profit_distance_price": 1000,
        },
    )


def filled_order(
    strategy: StrategyInstance,
    order_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    role: OrderRole = OrderRole.MAIN,
) -> PaperOrder:
    return PaperOrder(
        id=order_id,
        strategy_instance_id=strategy.id,
        cycle_id="cycle-1",
        symbol=strategy.symbol,
        side=side,
        role=role,
        quantity=1,
        status=OrderStatus.FILLED,
        fill_price=2500,
        metadata={"broker_fill_confirmed": True},
    )


def live_intent(
    strategy: StrategyInstance,
    order: PaperOrder,
    *,
    action: LiveIntentAction = LiveIntentAction.OPEN_MARKET,
    side: OrderSide = OrderSide.BUY,
    virtual_order_ids: list[str] | None = None,
    quantity: float = 1,
) -> LiveOrderIntent:
    return LiveOrderIntent(
        id="intent-1",
        strategy_instance_id=strategy.id,
        cycle_id=order.cycle_id,
        action=action,
        side=side,
        quantity=quantity,
        market_data_symbol=strategy.symbol,
        webull_symbol=strategy.webull_symbol,
        account_alias=strategy.account_alias,
        account_id="acct-futures",
        client_order_id="client-1",
        execution_key=order.id,
        virtual_order_ids=virtual_order_ids or [order.id],
        status=LiveIntentStatus.SUBMITTED,
    )


class FakeCoordinatorClient:
    def __init__(self, details: list[dict] | None = None) -> None:
        self.details = list(details or [])

    async def order_detail(self, account_id: str, client_order_id: str) -> dict:
        assert account_id == "acct-futures"
        assert client_order_id == "client-1"
        return self.details.pop(0)

    async def preview_order(self, account_id: str, order: dict) -> dict:
        return {"estimated_cost": "1"}

    async def account_balance(self, account_id: str) -> dict:
        return {"total_net_liquidation_value": "100000"}

    async def positions(self, account_id: str) -> list[dict]:
        return []

    async def place_order(self, account_id: str, order: dict) -> dict:
        return {"client_order_id": order["client_order_id"]}
