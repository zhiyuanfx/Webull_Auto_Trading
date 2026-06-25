from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from strategy_desk.domain import (
    AssetClass,
    Fill,
    MarketQuote,
    OrderCommand,
    OrderOrigin,
    OrderStatus,
    OrderTicket,
    OrderType,
    RiskLimits,
    Side,
)
from strategy_desk.persistence import Ledger
from strategy_desk.schedule import TradingSchedule


class GatewayRejected(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class BrokerAdapter(ABC):
    @abstractmethod
    async def place(self, ticket: OrderTicket) -> OrderTicket: ...

    @abstractmethod
    async def replace(self, ticket: OrderTicket, limit_price: Decimal | None) -> OrderTicket: ...

    @abstractmethod
    async def cancel(self, ticket: OrderTicket) -> OrderTicket: ...


class PositionBook:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def get(self, strategy_id: str, symbol: str) -> dict[str, Decimal]:
        return self.ledger.position(strategy_id, symbol)

    def apply(self, fill: Fill) -> dict[str, Decimal]:
        position = self.get(fill.strategy_instance_id, fill.symbol)
        realized = position["realized_pnl"] - fill.commission
        remaining = fill.quantity if fill.side == Side.BUY else -fill.quantity
        lots = self.ledger.lots(fill.strategy_instance_id, fill.symbol)
        kept: list[dict[str, object]] = []
        for lot in lots:
            lot_quantity = Decimal(str(lot["signed_quantity"]))
            if remaining == 0 or (lot_quantity > 0) == (remaining > 0):
                kept.append(lot)
                continue
            closing = min(abs(lot_quantity), abs(remaining))
            entry_price = Decimal(str(lot["entry_price"]))
            realized += (
                (fill.price - entry_price) * closing
                if lot_quantity > 0
                else (entry_price - fill.price) * closing
            )
            lot_quantity += closing if lot_quantity < 0 else -closing
            remaining += closing if remaining < 0 else -closing
            if lot_quantity:
                lot["signed_quantity"] = lot_quantity
                kept.append(lot)
        if remaining:
            kept.append(
                {
                    "signed_quantity": remaining,
                    "entry_price": fill.price,
                    "opened_at": fill.filled_at.isoformat(),
                }
            )

        new_qty = sum((Decimal(str(lot["signed_quantity"])) for lot in kept), Decimal("0"))
        new_avg = (
            sum(
                (
                    abs(Decimal(str(lot["signed_quantity"]))) * Decimal(str(lot["entry_price"]))
                    for lot in kept
                ),
                Decimal("0"),
            )
            / abs(new_qty)
            if new_qty
            else Decimal("0")
        )

        self.ledger.replace_lots(fill.strategy_instance_id, fill.symbol, kept)
        self.ledger.save_position(
            fill.strategy_instance_id, fill.symbol, new_qty, new_avg, realized
        )
        return {"quantity": new_qty, "average_price": new_avg, "realized_pnl": realized}


class SimulatorAdapter(BrokerAdapter):
    def __init__(
        self,
        on_fill,
        commission_per_unit: Decimal = Decimal("0"),
        slippage_bps: Decimal = Decimal("0"),
        latency_ms: int = 0,
        partial_fills: bool = True,
        on_update=None,
        account_state=None,
        position_state=None,
    ) -> None:
        self.on_fill = on_fill
        self.commission_per_unit = commission_per_unit
        self.slippage_bps = slippage_bps
        self.latency_ms = latency_ms
        self.partial_fills = partial_fills
        self.on_update = on_update
        self.account_state = account_state
        self.position_state = position_state
        self.quotes: dict[str, MarketQuote] = {}
        self.working: dict[str, OrderTicket] = {}

    async def update_quote(self, quote: MarketQuote) -> None:
        self.quotes[quote.symbol] = quote
        for ticket in list(self.working.values()):
            if ticket.command.symbol == quote.symbol:
                await self._try_fill(ticket, quote)

    async def place(self, ticket: OrderTicket) -> OrderTicket:
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        quote = self.quotes.get(ticket.command.symbol)
        submitted = ticket.model_copy(
            update={"status": OrderStatus.SUBMITTED, "updated_at": datetime.now(UTC)}
        )
        self.working[ticket.id] = submitted
        if quote:
            return await self._try_fill(submitted, quote)
        return submitted

    async def replace(self, ticket: OrderTicket, limit_price: Decimal | None) -> OrderTicket:
        if ticket.status not in {OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED}:
            raise GatewayRejected("NOT_REPLACEABLE", "Only working orders can be replaced")
        command = ticket.command.model_copy(update={"limit_price": limit_price})
        updated = ticket.model_copy(update={"command": command, "updated_at": datetime.now(UTC)})
        self.working[ticket.id] = updated
        quote = self.quotes.get(command.symbol)
        return await self._try_fill(updated, quote) if quote else updated

    async def cancel(self, ticket: OrderTicket) -> OrderTicket:
        if ticket.status not in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL_FILLED,
            OrderStatus.PENDING,
        }:
            raise GatewayRejected("NOT_CANCELLABLE", "Order is already terminal")
        self.working.pop(ticket.id, None)
        return ticket.model_copy(
            update={"status": OrderStatus.CANCELLED, "updated_at": datetime.now(UTC)}
        )

    async def _try_fill(self, ticket: OrderTicket, quote: MarketQuote) -> OrderTicket:
        command = ticket.command
        executable = False
        if command.order_type == OrderType.MARKET:
            executable = True
        elif command.order_type == OrderType.LIMIT:
            executable = (command.side == Side.BUY and quote.ask <= command.limit_price) or (
                command.side != Side.BUY and quote.bid >= command.limit_price
            )
        elif command.order_type in {OrderType.STOP_LOSS, OrderType.STOP_LOSS_LIMIT}:
            triggered = (command.side == Side.BUY and quote.ask >= command.stop_price) or (
                command.side != Side.BUY and quote.bid <= command.stop_price
            )
            if command.order_type == OrderType.STOP_LOSS:
                executable = triggered
            else:
                executable = triggered and (
                    (command.side == Side.BUY and quote.ask <= command.limit_price)
                    or (command.side != Side.BUY and quote.bid >= command.limit_price)
                )
        if not executable:
            return ticket

        remaining = command.quantity - ticket.filled_quantity
        available = quote.ask_size if command.side == Side.BUY else quote.bid_size
        quantity = min(remaining, available) if self.partial_fills and available > 0 else remaining
        base = quote.ask if command.side == Side.BUY else quote.bid
        direction = Decimal("1") if command.side == Side.BUY else Decimal("-1")
        price = base * (Decimal("1") + direction * self.slippage_bps / Decimal("10000"))
        if self.account_state and self.position_state:
            account = self.account_state()
            current = Decimal(str(self.position_state(command.symbol)["quantity"]))
            signed = quantity if command.side == Side.BUY else -quantity
            increase_quantity = max(Decimal("0"), abs(current + signed) - abs(current))
            if increase_quantity:
                if command.asset_class == AssetClass.FUTURES:
                    required = (
                        Decimal(str(account["futures_margin_per_contract"])) * increase_quantity
                    )
                else:
                    leverage = max(Decimal(str(account["leverage"])), Decimal("1"))
                    required = price * increase_quantity / leverage
                required += self.commission_per_unit * quantity
                if Decimal(str(account["cash"])) < required:
                    rejected = ticket.model_copy(
                        update={
                            "status": OrderStatus.REJECTED,
                            "rejection_code": "SIM_BUYING_POWER",
                            "rejection_message": "Simulator buying power is insufficient",
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    self.working.pop(ticket.id, None)
                    if self.on_update:
                        await self.on_update(rejected)
                    return rejected
        fill = Fill(
            ticket_id=ticket.id,
            strategy_instance_id=command.strategy_instance_id,
            symbol=command.symbol,
            side=command.side,
            quantity=quantity,
            price=price,
            commission=self.commission_per_unit * quantity,
        )
        total_filled = ticket.filled_quantity + quantity
        previous_value = (ticket.average_fill_price or Decimal("0")) * ticket.filled_quantity
        average = (previous_value + price * quantity) / total_filled
        status = (
            OrderStatus.FILLED if total_filled == command.quantity else OrderStatus.PARTIAL_FILLED
        )
        updated = ticket.model_copy(
            update={
                "status": status,
                "filled_quantity": total_filled,
                "average_fill_price": average,
                "updated_at": datetime.now(UTC),
            }
        )
        if status == OrderStatus.FILLED:
            self.working.pop(ticket.id, None)
        else:
            self.working[ticket.id] = updated
        await self.on_fill(updated, fill)
        return updated


class ExecutionGateway:
    def __init__(self, ledger: Ledger, adapter: BrokerAdapter | None = None) -> None:
        self.ledger = ledger
        self.positions = PositionBook(ledger)
        self.adapter = adapter or SimulatorAdapter(self._handle_fill)
        self._adapters: dict[str, BrokerAdapter] = {}
        self._sessions: dict[str, asyncio.Queue[OrderTicket]] = {}
        self._risk: dict[str, RiskLimits] = {}
        self._schedules: dict[str, TradingSchedule] = {}
        self._order_times: dict[str, deque[datetime]] = defaultdict(deque)
        self._paused: set[str] = set()
        self._close_only: set[tuple[str, str]] = set()
        self._quotes: dict[str, MarketQuote] = {}
        self._lock = asyncio.Lock()

    def register_strategy(
        self,
        strategy_instance_id: str,
        risk: RiskLimits | None = None,
        schedule: TradingSchedule | None = None,
        adapter: BrokerAdapter | None = None,
    ) -> asyncio.Queue[OrderTicket]:
        queue: asyncio.Queue[OrderTicket] = asyncio.Queue(maxsize=256)
        self._sessions[strategy_instance_id] = queue
        self._risk[strategy_instance_id] = risk or RiskLimits()
        if schedule:
            self._schedules[strategy_instance_id] = schedule
        if adapter:
            self._adapters[strategy_instance_id] = adapter
        return queue

    async def place(self, command: OrderCommand) -> OrderTicket:
        async with self._lock:
            existing = self.ledger.ticket_by_client_order_id(
                command.account_id, command.client_order_id
            )
            if existing:
                comparable = {"submitted_at"}
                old = existing.command.model_dump(exclude=comparable)
                new = command.model_dump(exclude=comparable)
                if old != new:
                    raise GatewayRejected(
                        "IDEMPOTENCY_CONFLICT",
                        "Client order ID was already used for a different command",
                    )
                return existing
            self._validate(command)
            ticket = OrderTicket(id=uuid4().hex, command=command, status=OrderStatus.PENDING)
            self.ledger.save_ticket(ticket)
            adapter = self._adapter_for(command.strategy_instance_id)
            try:
                ticket = await adapter.place(ticket)
            except GatewayRejected as exc:
                ticket = ticket.model_copy(
                    update={
                        "status": OrderStatus.REJECTED,
                        "rejection_code": exc.code,
                        "rejection_message": str(exc),
                        "updated_at": datetime.now(UTC),
                    }
                )
            self.ledger.save_ticket(ticket)
            if not isinstance(adapter, SimulatorAdapter) or ticket.status == OrderStatus.SUBMITTED:
                await self._publish(ticket)
            return ticket

    async def replace(
        self, strategy_id: str, ticket_id: str, limit_price: Decimal | None
    ) -> OrderTicket:
        async with self._lock:
            ticket = self._owned_ticket(strategy_id, ticket_id)
            adapter = self._adapter_for(strategy_id)
            ticket = await adapter.replace(ticket, limit_price)
            self.ledger.save_ticket(ticket)
            if not isinstance(adapter, SimulatorAdapter) or ticket.status == OrderStatus.SUBMITTED:
                await self._publish(ticket)
            return ticket

    async def cancel(self, strategy_id: str, ticket_id: str) -> OrderTicket:
        async with self._lock:
            ticket = self._owned_ticket(strategy_id, ticket_id)
            ticket = await self._adapter_for(strategy_id).cancel(ticket)
            self.ledger.save_ticket(ticket)
            await self._publish(ticket)
            return ticket

    async def update_quote(self, quote: MarketQuote) -> None:
        self._quotes[quote.symbol] = quote
        adapters = {self.adapter, *self._adapters.values()}
        for adapter in adapters:
            if isinstance(adapter, SimulatorAdapter):
                await adapter.update_quote(quote)

    async def apply_broker_event(self, payload: dict[str, object]) -> OrderTicket:
        """Reconcile a cumulative Webull order event into a strategy-local ticket."""
        account_id = str(payload.get("account_id", ""))
        client_order_id = str(payload.get("client_order_id", ""))
        ticket = self.ledger.ticket_by_client_order_id(account_id, client_order_id)
        if ticket is None:
            self.ledger.audit("UNASSIGNED_BROKER_EVENT", payload)
            raise GatewayRejected("UNASSIGNED_BROKER_EVENT", "Broker event has no local ticket")

        scene = str(payload.get("scene_type", ""))
        reported_status = str(payload.get("order_status", ""))
        status = ticket.status
        if scene == "FINAL_FILLED" or reported_status == "FILLED":
            status = OrderStatus.FILLED
        elif scene == "FILLED" or reported_status == "PARTIAL_FILLED":
            status = OrderStatus.PARTIAL_FILLED
        elif scene == "PLACE_FAILED" or reported_status == "FAILED":
            status = OrderStatus.FAILED
        elif scene == "CANCEL_SUCCESS" or reported_status == "CANCELLED":
            status = OrderStatus.CANCELLED
        elif scene == "MODIFY_SUCCESS":
            status = OrderStatus.SUBMITTED

        cumulative = Decimal(str(payload.get("filled_qty") or ticket.filled_quantity))
        if cumulative < ticket.filled_quantity:
            raise GatewayRejected("FILL_REGRESSION", "Broker cumulative fill quantity regressed")
        average_price = (
            Decimal(str(payload["filled_price"]))
            if payload.get("filled_price") not in {None, ""}
            else ticket.average_fill_price
        )
        updated = ticket.model_copy(
            update={
                "status": status,
                "broker_order_id": payload.get("order_id") or ticket.broker_order_id,
                "filled_quantity": cumulative,
                "average_fill_price": average_price,
                "updated_at": datetime.now(UTC),
            }
        )
        delta = cumulative - ticket.filled_quantity
        if delta > 0:
            if average_price is None:
                raise GatewayRejected("FILL_PRICE_MISSING", "Filled event did not include a price")
            previous_value = (ticket.average_fill_price or Decimal("0")) * ticket.filled_quantity
            incremental_price = (average_price * cumulative - previous_value) / delta
            fill = Fill(
                ticket_id=ticket.id,
                strategy_instance_id=ticket.command.strategy_instance_id,
                symbol=ticket.command.symbol,
                side=ticket.command.side,
                quantity=delta,
                price=incremental_price,
            )
            self._record_fill(ticket, fill)
        self.ledger.save_ticket(updated)
        await self._publish(updated)
        return updated

    def set_paused(self, strategy_id: str, paused: bool) -> None:
        if paused:
            self._paused.add(strategy_id)
        else:
            self._paused.discard(strategy_id)

    def set_close_only(self, strategy_id: str, symbol: str, enabled: bool) -> None:
        key = (strategy_id, symbol)
        if enabled:
            self._close_only.add(key)
        else:
            self._close_only.discard(key)

    async def cancel_all(self, strategy_id: str | None = None) -> list[OrderTicket]:
        terminal = {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.FAILED,
            OrderStatus.REJECTED,
        }
        tickets = (
            self.ledger.tickets_for_strategy(strategy_id)
            if strategy_id
            else self.ledger.all_tickets()
        )
        results: list[OrderTicket] = []
        for ticket in tickets:
            if ticket.status not in terminal:
                results.append(await self.cancel(ticket.command.strategy_instance_id, ticket.id))
        return results

    async def flatten(
        self,
        strategy_id: str,
        origin: OrderOrigin = OrderOrigin.SYSTEM_EMERGENCY,
    ) -> list[OrderTicket]:
        await self.cancel_all(strategy_id)
        results: list[OrderTicket] = []
        tickets = self.ledger.tickets_for_strategy(strategy_id)
        account_by_symbol = {ticket.command.symbol: ticket.command for ticket in tickets}
        for position in self.ledger.positions(strategy_id):
            quantity = Decimal(str(position["quantity"]))
            if quantity == 0:
                continue
            previous = account_by_symbol.get(str(position["symbol"]))
            if previous is None:
                raise GatewayRejected(
                    "POSITION_UNATTRIBUTED", "Cannot identify account for virtual position"
                )
            command = OrderCommand(
                strategy_instance_id=strategy_id,
                account_id=previous.account_id,
                symbol=previous.symbol,
                asset_class=previous.asset_class,
                side=Side.SELL if quantity > 0 else Side.BUY,
                quantity=abs(quantity),
                order_type=OrderType.MARKET,
                origin=origin,
            )
            results.append(await self.place(command))
        return results

    async def close_position(
        self,
        strategy_id: str,
        symbol: str,
        origin: OrderOrigin,
    ) -> OrderTicket | None:
        quantity = self.positions.get(strategy_id, symbol)["quantity"]
        if quantity == 0:
            return None
        tickets = self.ledger.tickets_for_strategy(strategy_id)
        previous = next(
            (ticket.command for ticket in reversed(tickets) if ticket.command.symbol == symbol),
            None,
        )
        if previous is None:
            raise GatewayRejected("POSITION_UNATTRIBUTED", "Cannot identify account for position")
        closing_side = Side.SELL if quantity > 0 else Side.BUY
        existing_close = next(
            (
                ticket
                for ticket in reversed(tickets)
                if ticket.command.symbol == symbol
                and ticket.command.origin != OrderOrigin.STRATEGY
                and ticket.command.side == closing_side
                and ticket.status
                in {
                    OrderStatus.PENDING,
                    OrderStatus.SUBMITTED,
                    OrderStatus.PARTIAL_FILLED,
                }
            ),
            None,
        )
        if existing_close:
            return existing_close
        for ticket in tickets:
            if ticket.command.symbol == symbol and ticket.status in {
                OrderStatus.PENDING,
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIAL_FILLED,
            }:
                await self.cancel(strategy_id, ticket.id)
        return await self.place(
            OrderCommand(
                strategy_instance_id=strategy_id,
                account_id=previous.account_id,
                symbol=symbol,
                asset_class=previous.asset_class,
                side=closing_side,
                quantity=abs(quantity),
                order_type=OrderType.MARKET,
                origin=origin,
            )
        )

    def _adapter_for(self, strategy_id: str) -> BrokerAdapter:
        return self._adapters.get(strategy_id, self.adapter)

    def _owned_ticket(self, strategy_id: str, ticket_id: str) -> OrderTicket:
        ticket = self.ledger.ticket_by_id(ticket_id)
        if not ticket or ticket.command.strategy_instance_id != strategy_id:
            raise GatewayRejected("TICKET_NOT_FOUND", "Ticket does not belong to this strategy")
        return ticket

    def _validate(self, command: OrderCommand) -> None:
        if command.strategy_instance_id in self._paused and command.origin == OrderOrigin.STRATEGY:
            raise GatewayRejected("STRATEGY_PAUSED", "Strategy order session is paused")
        risk = self._risk.get(command.strategy_instance_id, RiskLimits())
        if risk.allowed_symbols and command.symbol not in risk.allowed_symbols:
            raise GatewayRejected("SYMBOL_NOT_ALLOWED", f"{command.symbol} is not allowed")
        if self.ledger.open_ticket_count(command.strategy_instance_id) >= risk.max_open_orders:
            raise GatewayRejected("OPEN_ORDER_LIMIT", "Maximum open orders reached")
        now = datetime.now(UTC)
        if command.origin == OrderOrigin.STRATEGY:
            quote = self._quotes.get(command.symbol)
            if quote is None:
                raise GatewayRejected(
                    "MARKET_DATA_MISSING", "No normalized market quote is available"
                )
            if now - quote.received_at > timedelta(seconds=5):
                raise GatewayRejected(
                    "MARKET_DATA_STALE", "Latest normalized market quote is stale"
                )
            reference = command.limit_price or (
                quote.ask if command.side == Side.BUY else quote.bid
            )
            if reference * command.quantity > risk.max_notional:
                raise GatewayRejected("NOTIONAL_LIMIT", "Maximum strategy order notional exceeded")
        times = self._order_times[command.strategy_instance_id]
        cutoff = now - timedelta(minutes=1)
        while times and times[0] < cutoff:
            times.popleft()
        if len(times) >= risk.max_orders_per_minute:
            raise GatewayRejected("ORDER_RATE_LIMIT", "Strategy order-rate limit reached")
        schedule = self._schedules.get(command.strategy_instance_id)
        if schedule and command.origin == OrderOrigin.STRATEGY and not schedule.is_open(now):
            current = self.positions.get(command.strategy_instance_id, command.symbol)["quantity"]
            signed = command.quantity if command.side == Side.BUY else -command.quantity
            if current == 0 or (current > 0) == (signed > 0) or abs(signed) > abs(current):
                raise GatewayRejected(
                    "OUTSIDE_TRADING_WINDOW", "New exposure is blocked outside the trading window"
                )
        if (
            command.strategy_instance_id,
            command.symbol,
        ) in self._close_only and command.origin == OrderOrigin.STRATEGY:
            current = self.positions.get(command.strategy_instance_id, command.symbol)["quantity"]
            signed = command.quantity if command.side == Side.BUY else -command.quantity
            if current == 0 or (current > 0) == (signed > 0) or abs(signed) > abs(current):
                raise GatewayRejected(
                    "CONTRACT_CLOSE_ONLY", "Expiring futures contract is close-only"
                )
        current = self.positions.get(command.strategy_instance_id, command.symbol)["quantity"]
        if (
            self.positions.get(command.strategy_instance_id, command.symbol)["realized_pnl"]
            <= -risk.max_daily_realized_loss
        ):
            raise GatewayRejected("DAILY_LOSS_LIMIT", "Strategy daily realized-loss limit reached")
        signed = command.quantity if command.side == Side.BUY else -command.quantity
        if abs(current + signed) > risk.max_position:
            raise GatewayRejected("POSITION_LIMIT", "Maximum strategy position exceeded")
        times.append(now)

    async def _handle_fill(self, ticket: OrderTicket, fill: Fill) -> None:
        self._record_fill(ticket, fill)
        self.ledger.save_ticket(ticket)
        await self._publish(ticket)

    async def _handle_adapter_update(self, ticket: OrderTicket) -> None:
        self.ledger.save_ticket(ticket)
        await self._publish(ticket)

    def _record_fill(self, ticket: OrderTicket, fill: Fill) -> None:
        before = self.positions.get(fill.strategy_instance_id, fill.symbol)
        self.ledger.save_fill(fill)
        after = self.positions.apply(fill)
        if ticket.command.asset_class == AssetClass.EQUITY:
            gross = fill.price * fill.quantity
            cash_change = gross if fill.side in {Side.SELL, Side.SHORT} else -gross
            cash_change -= fill.commission
        else:
            cash_change = after["realized_pnl"] - before["realized_pnl"]
        self.ledger.adjust_simulator_cash(ticket.command.account_id, cash_change)

    async def _publish(self, ticket: OrderTicket) -> None:
        queue = self._sessions.get(ticket.command.strategy_instance_id)
        if queue:
            if queue.full():
                queue.get_nowait()
            await queue.put(ticket)
