from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from strategy_desk.config import Settings, get_settings
from strategy_desk.domain import (
    AssetClass,
    ContractMode,
    ExecutionMode,
    FeedSource,
    MarketQuote,
    OrderCommand,
    OrderOrigin,
    OrderStatus,
    OrderType,
    RiskLimits,
    Side,
    StrategyState,
)
from strategy_desk.futures import ExpiryGuard, ExpiryPolicy, ExpiryState, FuturesContract
from strategy_desk.gateway import ExecutionGateway, GatewayRejected, SimulatorAdapter
from strategy_desk.market import MarketDataHub
from strategy_desk.persistence import Ledger
from strategy_desk.plugins import PluginRegistry, StrategyPlugin
from strategy_desk.schedule import TradingSchedule
from strategy_desk.supervisor import StrategySupervisor
from strategy_desk.webull import WebullMarketStream, WebullTradingAdapter


class SimulatorAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)
    commission_per_unit: Decimal = Field(default=Decimal("0"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("0"), ge=0)
    latency_ms: int = Field(default=0, ge=0, le=60000)
    partial_fills: bool = True
    leverage: Decimal = Field(default=Decimal("1"), ge=1)
    futures_margin_per_contract: Decimal = Field(default=Decimal("0"), ge=0)


class FuturesBindingConfig(BaseModel):
    mode: ContractMode
    product_code: str = Field(min_length=1, max_length=12)
    current_symbol: str = Field(min_length=1, max_length=32)
    expiry_policy: ExpiryPolicy = Field(default_factory=ExpiryPolicy)
    contracts: list[FuturesContract] = Field(default_factory=list)


class InstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    plugin_id: str
    plugin_version: str
    mode: ExecutionMode = ExecutionMode.LOCAL_SIM
    feed_source: FeedSource = FeedSource.REPLAY
    account_id: str
    symbols: list[str] = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    contract: FuturesBindingConfig | None = None
    replay_path: Path | None = None
    replay_speed: float = Field(default=1.0, gt=0, le=1000)


class StartRequest(BaseModel):
    live_confirmation: str | None = None


class SimulatorReset(BaseModel):
    initial_cash: Decimal | None = Field(default=None, gt=0)


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ledger = Ledger(settings.strategy_desk_db_path)
        self.ledger.initialize()
        for instance in self.ledger.instances():
            if instance["state"] in {
                StrategyState.RUNNING,
                StrategyState.PAUSED,
                StrategyState.OUTSIDE_WINDOW,
            }:
                instance["state"] = StrategyState.DEGRADED
                self.ledger.save_instance(instance)
                self.ledger.end_active_run(str(instance["id"]), "INTERRUPTED")
                self.ledger.audit(
                    "STALE_RUNTIME_RECOVERED",
                    {"reason": "No worker exists after control-plane restart"},
                    str(instance["id"]),
                )
        self.gateway = ExecutionGateway(self.ledger)
        self.market = MarketDataHub(self.gateway)
        self.plugins = PluginRegistry(settings.strategy_root)
        self.supervisor = StrategySupervisor(self.gateway, self.ledger)
        self.expiry_task: asyncio.Task[None] | None = None
        self.reconcile_task: asyncio.Task[None] | None = None
        self.market_streams: dict[tuple[FeedSource, str], WebullMarketStream] = {}
        self.market_tasks: dict[tuple[FeedSource, str], asyncio.Task[None]] = {}
        self.market_subscribers: dict[tuple[FeedSource, str], dict[str, set[str]]] = {}
        self.replay_tasks: dict[str, asyncio.Task[None]] = {}

    def plugin(self, plugin_id: str, version: str) -> StrategyPlugin:
        plugin = next(
            (
                candidate
                for candidate in self.plugins.discover()
                if candidate.manifest.id == plugin_id and candidate.manifest.version == version
            ),
            None,
        )
        if plugin is None:
            raise HTTPException(404, "Plugin version not found")
        return plugin

    async def expiry_loop(self) -> None:
        while True:
            for instance in self.ledger.instances():
                if instance["state"] in {StrategyState.RUNNING, StrategyState.PAUSED}:
                    try:
                        await self.evaluate_expiry(instance)
                    except Exception as exc:
                        self.ledger.audit(
                            "EXPIRY_GUARD_ERROR",
                            {"error": type(exc).__name__, "message": str(exc)},
                            str(instance["id"]),
                        )
            await asyncio.sleep(60)

    async def reconciliation_loop(self) -> None:
        cursor = 0
        terminal = {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.FAILED,
            OrderStatus.REJECTED,
        }
        while True:
            candidates = [
                ticket
                for ticket in self.ledger.all_tickets()
                if ticket.status not in terminal
                and isinstance(
                    self.gateway._adapters.get(ticket.command.strategy_instance_id),
                    WebullTradingAdapter,
                )
            ]
            if candidates:
                ticket = candidates[cursor % len(candidates)]
                cursor += 1
                adapter = self.gateway._adapters[ticket.command.strategy_instance_id]
                try:
                    event = await adapter.order_detail_event(ticket)
                    await self.gateway.apply_broker_event(event)
                except Exception as exc:
                    self.ledger.audit(
                        "ORDER_RECONCILIATION_FAILED",
                        {
                            "ticket_id": ticket.id,
                            "error": type(exc).__name__,
                            "message": str(exc),
                        },
                        ticket.command.strategy_instance_id,
                    )
            await asyncio.sleep(1.1)

    async def connect_market_feed(self, instance_id: str, config: InstanceCreate) -> None:
        if config.feed_source == FeedSource.REPLAY:
            return
        if config.feed_source == FeedSource.UAT and not self.settings.uat_configured:
            raise GatewayRejected("CREDENTIALS_MISSING", "UAT feed credentials are not configured")
        if config.feed_source == FeedSource.PRODUCTION and not self.settings.production_configured:
            raise GatewayRejected(
                "CREDENTIALS_MISSING", "Production feed credentials are not configured"
            )
        category = "US_FUTURES" if config.contract else "US_STOCK"
        key = (config.feed_source, category)
        subscribers = self.market_subscribers.setdefault(key, {})
        new_symbols: list[str] = []
        for symbol in config.symbols:
            if symbol not in subscribers:
                new_symbols.append(symbol)
            subscribers.setdefault(symbol, set()).add(instance_id)
        stream = self.market_streams.get(key)
        if stream:
            if new_symbols:
                await stream.add_symbols(new_symbols)
            return

        mode = (
            ExecutionMode.WEBULL_UAT
            if config.feed_source == FeedSource.UAT
            else ExecutionMode.WEBULL_LIVE
        )
        stream = WebullMarketStream(self.settings, mode)
        self.market_streams[key] = stream

        async def on_message(topic: str, value: Any) -> None:
            basic = getattr(value, "basic", None)
            symbol = str(getattr(basic, "symbol", ""))
            if not symbol:
                return
            targets = tuple(self.market_subscribers.get(key, {}).get(symbol, set()))
            if topic == "quote" and value.asks and value.bids:
                timestamp = datetime.fromtimestamp(basic.timestamp / 1000, tz=UTC)
                quote = MarketQuote(
                    symbol=symbol,
                    bid=value.bids[0].price,
                    ask=value.asks[0].price,
                    bid_size=Decimal(str(value.bids[0].size or 0)),
                    ask_size=Decimal(str(value.asks[0].size or 0)),
                    source_at=timestamp,
                )
                await self.market.publish(quote)
                for target in targets:
                    await self.supervisor.publish_to(target, "quote", quote.model_dump(mode="json"))
            elif topic == "tick":
                tick = {
                    "symbol": symbol,
                    "price": str(value.price) if value.price is not None else None,
                    "volume": str(value.volume) if value.volume is not None else None,
                    "side": value.side,
                    "time": value.time,
                    "received_at": datetime.now(UTC).isoformat(),
                }
                for target in targets:
                    await self.supervisor.publish_to(target, "tick", tick)

        async def run_stream() -> None:
            try:
                await stream.run(
                    list(subscribers),
                    category,
                    ["QUOTE", "TICK"],
                    on_message,
                )
            except Exception as exc:
                self.ledger.audit(
                    "MARKET_STREAM_FAILED",
                    {
                        "feed": config.feed_source.value,
                        "category": category,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                raise

        stream_task = asyncio.create_task(run_stream())
        self.market_tasks[key] = stream_task
        ready_task = asyncio.create_task(stream.wait_ready())
        done, _ = await asyncio.wait(
            {stream_task, ready_task}, timeout=15, return_when=asyncio.FIRST_COMPLETED
        )
        if ready_task in done:
            return
        ready_task.cancel()
        self.market_streams.pop(key, None)
        self.market_tasks.pop(key, None)
        if stream_task in done:
            exception = stream_task.exception()
            raise GatewayRejected(
                "MARKET_STREAM_FAILED", str(exception or "Market stream stopped")
            ) from exception
        stream_task.cancel()
        raise GatewayRejected(
            "MARKET_STREAM_TIMEOUT", "Market stream did not connect in 15 seconds"
        )

    def disconnect_instance_feed(self, instance_id: str) -> None:
        for symbols in self.market_subscribers.values():
            for targets in symbols.values():
                targets.discard(instance_id)
        replay = self.replay_tasks.pop(instance_id, None)
        if replay:
            replay.cancel()

    def start_replay(self, instance_id: str, config: InstanceCreate) -> None:
        if config.feed_source != FeedSource.REPLAY or config.replay_path is None:
            return
        path = config.replay_path.expanduser().resolve()
        if not path.is_file():
            raise GatewayRejected("REPLAY_NOT_FOUND", f"Replay file does not exist: {path}")

        async def deliver(quote: MarketQuote) -> None:
            await self.supervisor.publish_to(instance_id, "quote", quote.model_dump(mode="json"))

        task = asyncio.create_task(self.market.replay(path, config.replay_speed, on_quote=deliver))
        self.replay_tasks[instance_id] = task

        def finished(completed: asyncio.Task[None]) -> None:
            if completed.cancelled():
                return
            error = completed.exception()
            if error:
                self.ledger.audit(
                    "REPLAY_FAILED",
                    {"path": str(path), "error": type(error).__name__, "message": str(error)},
                    instance_id,
                )

        task.add_done_callback(finished)

    async def evaluate_expiry(self, instance: dict[str, object]) -> None:
        config = InstanceCreate.model_validate(instance["config"])
        binding = config.contract
        if binding is None:
            return
        instance_id = str(instance["id"])
        contract = next(
            (item for item in binding.contracts if item.symbol == binding.current_symbol), None
        )
        if contract is None:
            self.gateway.set_close_only(instance_id, binding.current_symbol, True)
            self.ledger.audit(
                "EXPIRY_METADATA_MISSING",
                {"symbol": binding.current_symbol},
                instance_id,
            )
            return

        today = datetime.now(ZoneInfo("America/New_York")).date()
        guard = ExpiryGuard(binding.expiry_policy)
        expiry_state = guard.state(contract, today)
        previous_state = self.ledger.load_checkpoint(instance_id, "__expiry_state__")
        if previous_state != expiry_state:
            self.ledger.save_checkpoint(instance_id, "__expiry_state__", expiry_state.value)
            self.ledger.audit(
                "EXPIRY_STATE_CHANGED",
                {"symbol": contract.symbol, "state": expiry_state.value},
                instance_id,
            )
            if expiry_state != ExpiryState.NORMAL:
                await self.supervisor.publish_to(
                    instance_id,
                    "contract_expiring",
                    {
                        "symbol": contract.symbol,
                        "state": expiry_state.value,
                        "critical_date": contract.critical_date.isoformat()
                        if contract.critical_date
                        else None,
                    },
                )

        close_only = expiry_state in {
            ExpiryState.CLOSE_ONLY,
            ExpiryState.AUTO_FLATTEN,
            ExpiryState.LOCKED,
            ExpiryState.INVALID,
        }
        self.gateway.set_close_only(instance_id, contract.symbol, close_only)

        if binding.mode == ContractMode.AUTO_ROLL and expiry_state != ExpiryState.NORMAL:
            await self._advance_roll(instance, config, contract, guard, today)
        elif expiry_state in {ExpiryState.AUTO_FLATTEN, ExpiryState.LOCKED}:
            now_et = datetime.now(ZoneInfo("America/New_York"))
            flatten_hour, flatten_minute = map(
                int, binding.expiry_policy.flatten_time_et.split(":")
            )
            if expiry_state == ExpiryState.LOCKED or (now_et.hour, now_et.minute) >= (
                flatten_hour,
                flatten_minute,
            ):
                self.gateway.set_paused(instance_id, True)
                await self.gateway.close_position(
                    instance_id, contract.symbol, OrderOrigin.SYSTEM_EXPIRY
                )

    async def _advance_roll(
        self,
        instance: dict[str, object],
        config: InstanceCreate,
        current: FuturesContract,
        guard: ExpiryGuard,
        today,
    ) -> None:
        instance_id = str(instance["id"])
        binding = config.contract
        assert binding is not None
        roll = self.ledger.load_checkpoint(instance_id, "__system_roll__")
        if not isinstance(roll, dict) or roll.get("old_symbol") != current.symbol:
            next_contract = guard.next_contract(current, binding.contracts, today)
            if next_contract is None:
                self.gateway.set_close_only(instance_id, current.symbol, True)
                self.ledger.audit(
                    "ROLL_TARGET_UNAVAILABLE", {"symbol": current.symbol}, instance_id
                )
                return
            quantity = self.gateway.positions.get(instance_id, current.symbol)["quantity"]
            self.gateway.set_paused(instance_id, True)
            self.gateway.set_close_only(instance_id, current.symbol, True)
            close_ticket = await self.gateway.close_position(
                instance_id, current.symbol, OrderOrigin.SYSTEM_ROLL
            )
            roll = {
                "stage": "CLOSING" if close_ticket else "CLOSED",
                "old_symbol": current.symbol,
                "quantity": str(quantity),
                "next_contract": next_contract.model_dump(mode="json"),
                "close_ticket_id": close_ticket.id if close_ticket else None,
            }
            self.ledger.save_checkpoint(instance_id, "__system_roll__", roll)
            self.ledger.audit("ROLL_STARTED", roll, instance_id)

        next_contract = FuturesContract.model_validate(roll["next_contract"])
        quantity = Decimal(str(roll["quantity"]))
        stage = str(roll["stage"])
        if stage == "CLOSING":
            close_ticket = self.ledger.ticket_by_id(str(roll["close_ticket_id"]))
            if self.gateway.positions.get(instance_id, current.symbol)["quantity"] == 0:
                roll["stage"] = stage = "CLOSED"
                self.ledger.save_checkpoint(instance_id, "__system_roll__", roll)
            elif close_ticket and close_ticket.status in {
                OrderStatus.CANCELLED,
                OrderStatus.FAILED,
                OrderStatus.REJECTED,
            }:
                retry = await self.gateway.close_position(
                    instance_id, current.symbol, OrderOrigin.SYSTEM_EXPIRY
                )
                roll["close_ticket_id"] = retry.id if retry else None
                self.ledger.save_checkpoint(instance_id, "__system_roll__", roll)
                return
            else:
                return

        if stage == "CLOSED" and quantity != 0:
            ticket = await self.gateway.place(
                OrderCommand(
                    strategy_instance_id=instance_id,
                    account_id=config.account_id,
                    symbol=next_contract.symbol,
                    asset_class=AssetClass.FUTURES,
                    side=Side.BUY if quantity > 0 else Side.SELL,
                    quantity=abs(quantity),
                    order_type=OrderType.MARKET,
                    origin=OrderOrigin.SYSTEM_ROLL,
                )
            )
            roll["stage"] = stage = "OPENING"
            roll["open_ticket_id"] = ticket.id
            self.ledger.save_checkpoint(instance_id, "__system_roll__", roll)

        if stage == "OPENING":
            open_ticket = self.ledger.ticket_by_id(str(roll["open_ticket_id"]))
            if not open_ticket or open_ticket.status not in {
                OrderStatus.FILLED,
                OrderStatus.FAILED,
                OrderStatus.REJECTED,
                OrderStatus.CANCELLED,
            }:
                return
            if open_ticket.status != OrderStatus.FILLED:
                self.ledger.audit(
                    "ROLL_REOPEN_FAILED",
                    {"ticket_id": open_ticket.id, "status": open_ticket.status.value},
                    instance_id,
                )
                return

        if stage == "CLOSED" or (stage == "OPENING" and quantity != 0):
            old_symbol = binding.current_symbol
            binding.current_symbol = next_contract.symbol
            config.symbols = [
                next_contract.symbol if symbol == old_symbol else symbol
                for symbol in config.symbols
            ]
            instance["config"] = config.model_dump(mode="json")
            instance["state"] = StrategyState.PAUSED
            self.ledger.save_instance(instance)
            self.gateway.set_close_only(instance_id, old_symbol, False)
            self.disconnect_instance_feed(instance_id)
            try:
                await self.connect_market_feed(instance_id, config)
            except Exception as exc:
                self.ledger.audit(
                    "ROLL_FEED_REBIND_FAILED",
                    {"symbol": next_contract.symbol, "message": str(exc)},
                    instance_id,
                )
                return
            self.gateway.set_paused(instance_id, False)
            instance["state"] = StrategyState.RUNNING
            self.ledger.save_instance(instance)
            roll["stage"] = "COMPLETE"
            self.ledger.save_checkpoint(instance_id, "__system_roll__", roll)
            self.ledger.audit("ROLL_COMPLETED", roll, instance_id)
            await self.supervisor.publish_to(
                instance_id,
                "contract_rolled",
                {"old_symbol": old_symbol, "new_symbol": next_contract.symbol},
            )


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.desk = AppState(configured)
        app.state.desk.expiry_task = asyncio.create_task(app.state.desk.expiry_loop())
        app.state.desk.reconcile_task = asyncio.create_task(app.state.desk.reconciliation_loop())
        try:
            yield
        finally:
            app.state.desk.expiry_task.cancel()
            app.state.desk.reconcile_task.cancel()
            for worker in list(app.state.desk.supervisor._workers):
                await app.state.desk.supervisor.stop(worker, "application shutdown")
                app.state.desk.ledger.end_active_run(worker, "SHUTDOWN")
                instance = app.state.desk.ledger.instance(worker)
                if instance:
                    instance["state"] = StrategyState.STOPPED
                    app.state.desk.ledger.save_instance(instance)
            for stream in app.state.desk.market_streams.values():
                await stream.stop()
            for task in app.state.desk.market_tasks.values():
                task.cancel()
            for task in app.state.desk.replay_tasks.values():
                task.cancel()

    app = FastAPI(title="Webull Strategy Desk", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def state() -> AppState:
        return app.state.desk

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        desk = state()
        freshest_quote = max(
            (quote.received_at for quote in desk.market.last_quotes.values()), default=None
        )
        return {
            "status": "ok",
            "time": datetime.now(UTC),
            "database": str(desk.settings.strategy_desk_db_path),
            "uat_configured": desk.settings.uat_configured,
            "production_configured": desk.settings.production_configured,
            "live_enabled": desk.settings.webull_live_enabled,
            "workers": desk.supervisor.status(),
            "feed_age_ms": (
                int((datetime.now(UTC) - freshest_quote).total_seconds() * 1000)
                if freshest_quote
                else None
            ),
            "market_connections": [
                {"feed": feed.value, "category": category} for feed, category in desk.market_streams
            ],
        }

    @app.get("/api/plugins")
    async def plugins() -> list[dict[str, Any]]:
        return [
            {
                **plugin.manifest.model_dump(mode="json"),
                "source_hash": plugin.source_hash,
            }
            for plugin in state().plugins.discover()
        ]

    @app.post("/api/plugins/validate")
    async def validate_plugins() -> list[dict[str, str]]:
        return state().plugins.validate_all()

    @app.get("/api/simulator/accounts")
    async def simulator_accounts() -> list[dict[str, object]]:
        return state().ledger.simulator_accounts()

    @app.post("/api/simulator/accounts", status_code=201)
    async def create_simulator_account(payload: SimulatorAccountCreate) -> dict[str, str]:
        account_id = f"SIM-{uuid4().hex[:10].upper()}"
        state().ledger.create_simulator_account(account_id, **payload.model_dump())
        return {"id": account_id}

    @app.post("/api/simulator/accounts/{account_id}/reset", status_code=201)
    async def reset_simulator_account(account_id: str, payload: SimulatorReset) -> dict[str, str]:
        desk = state()
        source = next(
            (item for item in desk.ledger.simulator_accounts() if item["id"] == account_id), None
        )
        if source is None:
            raise HTTPException(404, "Simulator account not found")
        new_id = f"SIM-{uuid4().hex[:10].upper()}"
        desk.ledger.create_simulator_account(
            new_id,
            name=f"{source['name']} (reset)",
            initial_cash=payload.initial_cash or Decimal(str(source["initial_cash"])),
            commission_per_unit=Decimal(str(source["commission_per_unit"])),
            slippage_bps=Decimal(str(source["slippage_bps"])),
            latency_ms=int(source["latency_ms"]),
            partial_fills=bool(source["partial_fills"]),
            leverage=Decimal(str(source["leverage"])),
            futures_margin_per_contract=Decimal(str(source["futures_margin_per_contract"])),
        )
        desk.ledger.audit("SIMULATOR_RESET", {"source": account_id, "created": new_id})
        return {"id": new_id}

    @app.get("/api/instances")
    async def instances() -> list[dict[str, object]]:
        desk = state()
        running = {item["instance_id"]: item for item in desk.supervisor.status()}
        result = desk.ledger.instances()
        for item in result:
            worker = running.get(item["id"])
            if worker:
                item["worker"] = worker
        return result

    @app.post("/api/instances", status_code=201)
    async def create_instance(payload: InstanceCreate) -> dict[str, str]:
        desk = state()
        plugin = desk.plugin(payload.plugin_id, payload.plugin_version)
        if payload.mode == ExecutionMode.LOCAL_SIM:
            accounts = {item["id"] for item in desk.ledger.simulator_accounts()}
            if payload.account_id not in accounts:
                raise HTTPException(400, "Simulator account does not exist")
        instance_id = uuid4().hex[:16]
        desk.ledger.save_instance(
            {
                "id": instance_id,
                "plugin_id": payload.plugin_id,
                "plugin_version": payload.plugin_version,
                "plugin_source_hash": plugin.source_hash,
                "mode": payload.mode,
                "account_id": payload.account_id,
                "feed_source": payload.feed_source,
                "state": StrategyState.STOPPED,
                "config": payload.model_dump(mode="json"),
            }
        )
        return {"id": instance_id}

    @app.post("/api/instances/{instance_id}/start")
    async def start_instance(instance_id: str, request: StartRequest) -> dict[str, str]:
        desk = state()
        instance = desk.ledger.instance(instance_id)
        if not instance:
            raise HTTPException(404, "Instance not found")
        config = InstanceCreate.model_validate(instance["config"])
        if config.contract:
            contract = next(
                (
                    item
                    for item in config.contract.contracts
                    if item.symbol == config.contract.current_symbol
                ),
                None,
            )
            if contract is None:
                raise HTTPException(
                    409,
                    "Current futures contract metadata is required before starting",
                )
            expiry_state = ExpiryGuard(config.contract.expiry_policy).state(
                contract, datetime.now(ZoneInfo("America/New_York")).date()
            )
            if expiry_state in {ExpiryState.INVALID, ExpiryState.LOCKED}:
                raise HTTPException(409, f"Contract cannot trade: {expiry_state.value}")
        plugin = desk.plugin(config.plugin_id, config.plugin_version)
        if instance["plugin_source_hash"] != plugin.source_hash:
            raise HTTPException(
                409,
                "Strategy source changed after this immutable instance was created; "
                "create a new instance version",
            )
        strategy_class = desk.plugins.load_class(plugin)
        schedule = getattr(strategy_class, "trading_schedule", None)
        if schedule is not None and not isinstance(schedule, TradingSchedule):
            schedule = TradingSchedule.model_validate(schedule)

        if config.mode == ExecutionMode.LOCAL_SIM:

            def account_state() -> dict[str, object]:
                return next(
                    item
                    for item in desk.ledger.simulator_accounts()
                    if item["id"] == config.account_id
                )

            account = account_state()
            adapter = SimulatorAdapter(
                desk.gateway._handle_fill,
                commission_per_unit=Decimal(str(account["commission_per_unit"])),
                slippage_bps=Decimal(str(account["slippage_bps"])),
                latency_ms=int(account["latency_ms"]),
                partial_fills=bool(account["partial_fills"]),
                on_update=desk.gateway._handle_adapter_update,
                account_state=account_state,
                position_state=lambda symbol: desk.gateway.positions.get(instance_id, symbol),
            )
        else:
            confirmed = request.live_confirmation == "ENABLE LIVE TRADING"
            adapter = WebullTradingAdapter(desk.settings, config.mode, live_confirmed=confirmed)
            if config.mode == ExecutionMode.WEBULL_LIVE and not confirmed:
                raise HTTPException(409, "Type ENABLE LIVE TRADING to confirm")

        await desk.supervisor.start(
            instance_id,
            config.account_id,
            plugin,
            config.parameters,
            risk=config.risk,
            schedule=schedule,
            adapter=adapter,
        )
        try:
            await desk.connect_market_feed(instance_id, config)
            desk.start_replay(instance_id, config)
        except GatewayRejected as exc:
            await desk.supervisor.stop(instance_id, "market feed startup failed")
            raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc
        instance["state"] = StrategyState.RUNNING
        desk.ledger.save_instance(instance)
        run_id = uuid4().hex
        desk.ledger.start_run(
            run_id,
            instance_id,
            config.mode.value,
            config.account_id,
            config.feed_source.value,
        )
        return {"state": StrategyState.RUNNING, "run_id": run_id}

    @app.post("/api/instances/{instance_id}/stop")
    async def stop_instance(instance_id: str) -> dict[str, str]:
        desk = state()
        instance = desk.ledger.instance(instance_id)
        if not instance:
            raise HTTPException(404, "Instance not found")
        await desk.supervisor.stop(instance_id)
        desk.disconnect_instance_feed(instance_id)
        instance["state"] = StrategyState.STOPPED
        desk.ledger.save_instance(instance)
        desk.ledger.end_active_run(instance_id, "STOPPED")
        return {"state": StrategyState.STOPPED}

    @app.post("/api/instances/{instance_id}/pause")
    async def pause_instance(instance_id: str) -> dict[str, str]:
        desk = state()
        instance = desk.ledger.instance(instance_id)
        if not instance:
            raise HTTPException(404, "Instance not found")
        desk.gateway.set_paused(instance_id, True)
        instance["state"] = StrategyState.PAUSED
        desk.ledger.save_instance(instance)
        return {"state": StrategyState.PAUSED}

    @app.post("/api/instances/{instance_id}/resume")
    async def resume_instance(instance_id: str) -> dict[str, str]:
        desk = state()
        instance = desk.ledger.instance(instance_id)
        if not instance:
            raise HTTPException(404, "Instance not found")
        desk.gateway.set_paused(instance_id, False)
        instance["state"] = StrategyState.RUNNING
        desk.ledger.save_instance(instance)
        return {"state": StrategyState.RUNNING}

    @app.post("/api/instances/{instance_id}/cancel-all")
    async def cancel_all(instance_id: str) -> dict[str, int]:
        return {"cancelled": len(await state().gateway.cancel_all(instance_id))}

    @app.post("/api/instances/{instance_id}/flatten")
    async def flatten(instance_id: str) -> dict[str, int]:
        desk = state()
        desk.gateway.set_paused(instance_id, True)
        return {"orders": len(await desk.gateway.flatten(instance_id))}

    @app.post("/api/emergency-stop")
    async def emergency_stop(confirmation: str) -> dict[str, int]:
        if confirmation != "EMERGENCY STOP":
            raise HTTPException(409, "Confirmation must equal EMERGENCY STOP")
        desk = state()
        order_count = 0
        for instance in desk.ledger.instances():
            desk.gateway.set_paused(str(instance["id"]), True)
            order_count += len(await desk.gateway.flatten(str(instance["id"])))
        return {"orders": order_count}

    @app.get("/api/instances/{instance_id}/orders")
    async def instance_orders(instance_id: str) -> list[dict[str, Any]]:
        return [
            ticket.model_dump(mode="json")
            for ticket in state().ledger.tickets_for_strategy(instance_id)
        ]

    @app.get("/api/instances/{instance_id}/runs")
    async def instance_runs(instance_id: str) -> list[dict[str, object]]:
        return state().ledger.runs(instance_id)

    @app.get("/api/instances/{instance_id}/detail")
    async def instance_detail(instance_id: str) -> dict[str, object]:
        desk = state()
        instance = desk.ledger.instance(instance_id)
        if instance is None:
            raise HTTPException(404, "Instance not found")
        return {
            "instance": instance,
            "orders": [
                ticket.model_dump(mode="json")
                for ticket in desk.ledger.tickets_for_strategy(instance_id)
            ],
            "fills": [
                fill.model_dump(mode="json") for fill in desk.ledger.fills_for_strategy(instance_id)
            ],
            "positions": desk.ledger.positions(instance_id),
            "runs": desk.ledger.runs(instance_id),
            "events": desk.ledger.audit_events(instance_id),
            "expiry_state": desk.ledger.load_checkpoint(instance_id, "__expiry_state__"),
        }

    @app.get("/api/positions")
    async def positions() -> list[dict[str, object]]:
        return state().ledger.positions()

    @app.post("/api/market/quotes", status_code=202)
    async def inject_quote(quote: MarketQuote) -> dict[str, str]:
        await state().market.publish(quote)
        await state().supervisor.publish("quote", quote.model_dump(mode="json"))
        return {"status": "accepted"}

    @app.get("/api/accounts/{mode}")
    async def discover_accounts(mode: ExecutionMode) -> list[dict[str, Any]]:
        if mode == ExecutionMode.LOCAL_SIM:
            return state().ledger.simulator_accounts()
        try:
            return await WebullTradingAdapter(state().settings, mode).list_accounts()
        except GatewayRejected as exc:
            raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/accounts/{mode}/{account_id}/balance")
    async def account_balance(mode: ExecutionMode, account_id: str) -> dict[str, Any]:
        if mode == ExecutionMode.LOCAL_SIM:
            account = next(
                (item for item in state().ledger.simulator_accounts() if item["id"] == account_id),
                None,
            )
            if account is None:
                raise HTTPException(404, "Simulator account not found")
            return account
        try:
            return await WebullTradingAdapter(state().settings, mode).account_balance(account_id)
        except GatewayRejected as exc:
            raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/accounts/{mode}/{account_id}/positions")
    async def account_positions(mode: ExecutionMode, account_id: str) -> list[dict[str, Any]]:
        if mode == ExecutionMode.LOCAL_SIM:
            return [
                position
                for position in state().ledger.positions()
                if any(
                    ticket.command.account_id == account_id
                    for ticket in state().ledger.tickets_for_strategy(
                        str(position["strategy_instance_id"])
                    )
                )
            ]
        try:
            return await WebullTradingAdapter(state().settings, mode).account_positions(account_id)
        except GatewayRejected as exc:
            raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/futures/contracts/{mode}")
    async def futures_contracts(mode: ExecutionMode, code: str) -> list[dict[str, Any]]:
        if mode == ExecutionMode.LOCAL_SIM:
            raise HTTPException(400, "Choose UAT or production as the instrument environment")
        try:
            return await WebullTradingAdapter(state().settings, mode).futures_contracts(code)
        except GatewayRejected as exc:
            raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.websocket("/api/ws")
    async def websocket_status(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                desk = state()
                await websocket.send_json(
                    {
                        "time": datetime.now(UTC).isoformat(),
                        "instances": desk.ledger.instances(),
                        "positions": desk.ledger.positions(),
                        "workers": desk.supervisor.status(),
                        "quotes": {
                            symbol: quote.model_dump(mode="json")
                            for symbol, quote in desk.market.last_quotes.items()
                        },
                    }
                )
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return

    frontend = Path("frontend/dist")
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
