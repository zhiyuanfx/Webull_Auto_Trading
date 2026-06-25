from datetime import UTC, datetime
from decimal import Decimal

import pytest

from strategy_desk.domain import (
    AssetClass,
    Fill,
    MarketQuote,
    OrderCommand,
    OrderStatus,
    OrderTicket,
    OrderType,
    Side,
)
from strategy_desk.gateway import ExecutionGateway, SimulatorAdapter
from strategy_desk.order_session import OrderSession
from strategy_desk.persistence import Ledger


@pytest.mark.asyncio
async def test_strategy_session_places_and_fills_isolated_order(tmp_path):
    ledger = Ledger(tmp_path / "test.sqlite3")
    ledger.initialize()
    gateway = ExecutionGateway(ledger)
    adapter = SimulatorAdapter(gateway._handle_fill)
    session = OrderSession("strategy-a", "SIM-1", gateway, adapter=adapter)
    await gateway.update_quote(
        MarketQuote(
            symbol="AAPL",
            bid=Decimal("189.90"),
            ask=Decimal("190.00"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
            source_at=datetime.now(UTC),
        )
    )
    ticket = await session.place(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        quantity=Decimal("2"),
        order_type=OrderType.MARKET,
    )
    assert ticket.status == OrderStatus.FILLED
    assert ledger.position("strategy-a", "AAPL")["quantity"] == Decimal("2")
    assert session.tickets()[0].command.strategy_instance_id == "strategy-a"


@pytest.mark.asyncio
async def test_session_cannot_cancel_another_strategy_ticket(tmp_path):
    ledger = Ledger(tmp_path / "test.sqlite3")
    ledger.initialize()
    gateway = ExecutionGateway(ledger)
    first = OrderSession("first", "SIM-1", gateway)
    second = OrderSession("second", "SIM-1", gateway)
    await gateway.update_quote(
        MarketQuote(
            symbol="MSFT",
            bid=Decimal("400"),
            ask=Decimal("400.10"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
            source_at=datetime.now(UTC),
        )
    )
    ticket = await first.place(
        symbol="MSFT",
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1"),
    )
    with pytest.raises(Exception, match="does not belong"):
        await second.cancel(ticket.id)


@pytest.mark.asyncio
async def test_cumulative_broker_events_create_incremental_fills(tmp_path):
    ledger = Ledger(tmp_path / "events.sqlite3")
    ledger.initialize()
    gateway = ExecutionGateway(ledger)
    gateway.register_strategy("strategy-a")
    command = OrderCommand(
        strategy_instance_id="strategy-a",
        account_id="uat-account",
        symbol="MGCQ6",
        asset_class=AssetClass.FUTURES,
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("2400"),
        client_order_id="client-event-1",
    )
    ledger.save_ticket(
        OrderTicket(id="ticket-event-1", command=command, status=OrderStatus.SUBMITTED)
    )

    partial = await gateway.apply_broker_event(
        {
            "account_id": "uat-account",
            "client_order_id": "client-event-1",
            "order_id": "webull-event-1",
            "scene_type": "FILLED",
            "filled_qty": "0.5",
            "filled_price": "2400",
        }
    )
    final = await gateway.apply_broker_event(
        {
            "account_id": "uat-account",
            "client_order_id": "client-event-1",
            "order_id": "webull-event-1",
            "scene_type": "FINAL_FILLED",
            "filled_qty": "1",
            "filled_price": "2401",
        }
    )
    assert partial.status == OrderStatus.PARTIAL_FILLED
    assert final.status == OrderStatus.FILLED
    assert len(ledger.fills_for_strategy("strategy-a")) == 2
    assert ledger.position("strategy-a", "MGCQ6")["average_price"] == Decimal("2401")


def test_virtual_positions_close_fifo_lots(tmp_path):
    ledger = Ledger(tmp_path / "fifo.sqlite3")
    ledger.initialize()
    gateway = ExecutionGateway(ledger)
    for price in (Decimal("100"), Decimal("110")):
        gateway.positions.apply(
            Fill(
                ticket_id="unused",
                strategy_instance_id="fifo",
                symbol="AAPL",
                side=Side.BUY,
                quantity=Decimal("1"),
                price=price,
            )
        )
    result = gateway.positions.apply(
        Fill(
            ticket_id="unused",
            strategy_instance_id="fifo",
            symbol="AAPL",
            side=Side.SELL,
            quantity=Decimal("1"),
            price=Decimal("120"),
        )
    )
    assert result == {
        "quantity": Decimal("1"),
        "average_price": Decimal("110"),
        "realized_pnl": Decimal("20"),
    }


@pytest.mark.asyncio
async def test_client_order_id_retries_are_idempotent(tmp_path):
    ledger = Ledger(tmp_path / "idempotency.sqlite3")
    ledger.initialize()
    gateway = ExecutionGateway(ledger)
    session = OrderSession("strategy-a", "SIM-1", gateway)
    await gateway.update_quote(
        MarketQuote(
            symbol="AAPL",
            bid=Decimal("100"),
            ask=Decimal("101"),
            source_at=datetime.now(UTC),
        )
    )
    values = {
        "symbol": "AAPL",
        "asset_class": AssetClass.EQUITY,
        "side": Side.BUY,
        "quantity": Decimal("1"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("99"),
        "client_order_id": "stable-retry-id",
    }
    first = await session.place(**values)
    retry = await session.place(**values)
    assert retry.id == first.id
    assert len(ledger.tickets_for_strategy("strategy-a")) == 1


@pytest.mark.asyncio
async def test_simulator_cash_and_buying_power_are_enforced(tmp_path):
    ledger = Ledger(tmp_path / "funds.sqlite3")
    ledger.initialize()
    ledger.create_simulator_account(
        "SIM-1",
        name="Small",
        initial_cash=Decimal("100"),
        commission_per_unit=Decimal("1"),
        slippage_bps=Decimal("0"),
        latency_ms=0,
        partial_fills=True,
        leverage=Decimal("1"),
        futures_margin_per_contract=Decimal("50"),
    )
    gateway = ExecutionGateway(ledger)

    def account_state():
        return ledger.simulator_accounts()[0]

    adapter = SimulatorAdapter(
        gateway._handle_fill,
        commission_per_unit=Decimal("1"),
        on_update=gateway._handle_adapter_update,
        account_state=account_state,
        position_state=lambda symbol: gateway.positions.get("funds", symbol),
    )
    session = OrderSession("funds", "SIM-1", gateway, adapter=adapter)
    await gateway.update_quote(
        MarketQuote(
            symbol="AAPL",
            bid=Decimal("49"),
            ask=Decimal("50"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
            source_at=datetime.now(UTC),
        )
    )
    filled = await session.place(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
    )
    rejected = await session.place(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        quantity=Decimal("2"),
        order_type=OrderType.MARKET,
    )
    assert filled.status == OrderStatus.FILLED
    assert ledger.simulator_accounts()[0]["cash"] == "49"
    assert rejected.status == OrderStatus.REJECTED
    assert rejected.rejection_code == "SIM_BUYING_POWER"
