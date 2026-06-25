from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from strategy_desk.domain import OrderCommand, RiskLimits, StrategyState
from strategy_desk.gateway import BrokerAdapter, ExecutionGateway
from strategy_desk.order_session import OrderSession
from strategy_desk.persistence import Ledger
from strategy_desk.plugins import PluginRegistry, StrategyPlugin
from strategy_desk.schedule import TradingSchedule


def _sanitize_worker_environment() -> None:
    for key in tuple(os.environ):
        if key.startswith("WEBULL_") or key in {"APP_KEY", "APP_SECRET", "ACCESS_TOKEN"}:
            os.environ.pop(key, None)


class WorkerOrderSession:
    def __init__(self, strategy_id: str, account_id: str, connection: Connection) -> None:
        self.strategy_id = strategy_id
        self.account_id = account_id
        self.connection = connection

    async def _rpc(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.to_thread(self.connection.send, {"action": action, "payload": payload})
        response = await asyncio.to_thread(self.connection.recv)
        if not response["ok"]:
            raise RuntimeError(f"{response['code']}: {response['message']}")
        return response["value"]

    async def place(self, **values: Any) -> dict[str, Any]:
        command = {
            **values,
            "strategy_instance_id": self.strategy_id,
            "account_id": self.account_id,
        }
        for key, value in tuple(command.items()):
            if hasattr(value, "value"):
                command[key] = value.value
            elif isinstance(value, Decimal):
                command[key] = str(value)
        return await self._rpc("place", command)

    async def replace(self, ticket_id: str, *, limit_price: Decimal | None) -> dict[str, Any]:
        return await self._rpc(
            "replace",
            {"ticket_id": ticket_id, "limit_price": str(limit_price) if limit_price else None},
        )

    async def cancel(self, ticket_id: str) -> dict[str, Any]:
        return await self._rpc("cancel", {"ticket_id": ticket_id})

    async def tickets(self) -> list[dict[str, Any]]:
        response = await self._rpc("tickets", {})
        return response["tickets"]


class WorkerContext:
    def __init__(self, instance_id: str, orders: WorkerOrderSession, rpc: Connection) -> None:
        self.instance_id = instance_id
        self.orders = orders
        self._rpc_connection = rpc

    async def checkpoint(self, key: str, value: object) -> None:
        await self.orders._rpc("checkpoint", {"key": key, "value": value})

    async def restore(self, key: str, default: object = None) -> object:
        result = await self.orders._rpc("restore", {"key": key})
        return result.get("value", default)

    async def log(self, level: str, message: str, **fields: Any) -> None:
        await self.orders._rpc("log", {"level": level, "message": message, "fields": fields})


async def _worker_loop(
    plugin_directory: str,
    instance_id: str,
    account_id: str,
    parameters: dict[str, Any],
    rpc: Connection,
    events: Connection,
) -> None:
    _sanitize_worker_environment()
    registry = PluginRegistry(Path(plugin_directory).parent)
    plugin = next(item for item in registry.discover() if item.directory == Path(plugin_directory))
    strategy_class = registry.load_class(plugin)
    try:
        strategy = strategy_class(parameters=parameters)
    except TypeError:
        strategy = strategy_class()
    orders = WorkerOrderSession(instance_id, account_id, rpc)
    context = WorkerContext(instance_id, orders, rpc)
    await strategy.on_start(context)
    await orders._rpc("ready", {})
    while True:
        event = await asyncio.to_thread(events.recv)
        kind = event["kind"]
        if kind == "stop":
            await strategy.on_stop(event.get("reason", "requested"))
            return
        if kind == "quote":
            await strategy.on_quote(event["value"])
        elif kind == "tick":
            await strategy.on_tick(event["value"])
        elif kind == "timer":
            await strategy.on_timer(event["value"])
        elif kind == "order":
            await strategy.on_order_update(event["value"])
        elif kind == "window_open":
            await strategy.on_window_open(event["value"])
        elif kind == "window_close":
            await strategy.on_window_close(event["value"])
        elif kind == "contract_expiring":
            await strategy.on_contract_expiring(event["value"])
        elif kind == "contract_rolled":
            await strategy.on_contract_rolled(event["value"])


def _worker_entry(*args: Any) -> None:
    try:
        asyncio.run(_worker_loop(*args))
    except EOFError:
        pass
    except Exception:
        traceback.print_exc()
        raise


@dataclass
class WorkerHandle:
    plugin: StrategyPlugin
    process: mp.Process
    rpc: Connection
    events: Connection
    order_session: OrderSession
    tasks: list[asyncio.Task]
    event_lock: asyncio.Lock
    schedule: TradingSchedule | None
    ready: asyncio.Future[None]


class StrategySupervisor:
    def __init__(self, gateway: ExecutionGateway, ledger: Ledger) -> None:
        self.gateway = gateway
        self.ledger = ledger
        self._context = mp.get_context("spawn")
        self._workers: dict[str, WorkerHandle] = {}

    async def start(
        self,
        instance_id: str,
        account_id: str,
        plugin: StrategyPlugin,
        parameters: dict[str, Any],
        risk: RiskLimits | None = None,
        schedule: TradingSchedule | None = None,
        adapter: BrokerAdapter | None = None,
    ) -> None:
        if instance_id in self._workers:
            raise RuntimeError("Strategy is already running")
        parent_rpc, child_rpc = self._context.Pipe(duplex=True)
        child_events, parent_events = self._context.Pipe(duplex=False)
        session = OrderSession(
            instance_id,
            account_id,
            self.gateway,
            risk=risk,
            schedule=schedule,
            adapter=adapter,
        )
        process = self._context.Process(
            target=_worker_entry,
            args=(
                str(plugin.directory),
                instance_id,
                account_id,
                parameters,
                child_rpc,
                child_events,
            ),
            name=f"strategy-{instance_id}",
            daemon=True,
        )
        process.start()
        handle = WorkerHandle(
            plugin,
            process,
            parent_rpc,
            parent_events,
            session,
            [],
            asyncio.Lock(),
            schedule,
            asyncio.get_running_loop().create_future(),
        )
        self._workers[instance_id] = handle
        handle.tasks.extend(
            [
                asyncio.create_task(self._serve_rpc(instance_id, handle)),
                asyncio.create_task(self._forward_orders(instance_id, handle)),
                asyncio.create_task(self._timer_loop(handle)),
                asyncio.create_task(self._monitor(instance_id, handle)),
            ]
        )
        try:
            await asyncio.wait_for(asyncio.shield(handle.ready), timeout=10)
        except Exception:
            await self.stop(instance_id, "worker startup failed")
            raise

    async def stop(self, instance_id: str, reason: str = "requested") -> None:
        handle = self._workers.pop(instance_id, None)
        if not handle:
            return
        try:
            try:
                async with handle.event_lock:
                    handle.events.send({"kind": "stop", "reason": reason})
            except (BrokenPipeError, EOFError, OSError):
                pass
            await asyncio.to_thread(handle.process.join, 5)
            if handle.process.is_alive():
                handle.process.terminate()
                await asyncio.to_thread(handle.process.join, 2)
        finally:
            for task in handle.tasks:
                task.cancel()
            handle.rpc.close()
            handle.events.close()

    async def publish(self, kind: str, value: Any) -> None:
        for handle in tuple(self._workers.values()):
            if handle.process.is_alive():
                async with handle.event_lock:
                    await asyncio.to_thread(handle.events.send, {"kind": kind, "value": value})

    async def publish_to(self, instance_id: str, kind: str, value: Any) -> None:
        handle = self._workers.get(instance_id)
        if handle and handle.process.is_alive():
            async with handle.event_lock:
                await asyncio.to_thread(handle.events.send, {"kind": kind, "value": value})

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "instance_id": instance_id,
                "alive": handle.process.is_alive(),
                "pid": handle.process.pid,
                "plugin": handle.plugin.manifest.id,
            }
            for instance_id, handle in self._workers.items()
        ]

    async def _serve_rpc(self, instance_id: str, handle: WorkerHandle) -> None:
        while handle.process.is_alive():
            request = await asyncio.to_thread(handle.rpc.recv)
            try:
                action, payload = request["action"], request["payload"]
                if action == "ready":
                    if not handle.ready.done():
                        handle.ready.set_result(None)
                    value = {}
                elif action == "place":
                    value = (
                        await self.gateway.place(OrderCommand.model_validate(payload))
                    ).model_dump(mode="json")
                elif action == "replace":
                    price = Decimal(payload["limit_price"]) if payload["limit_price"] else None
                    value = (
                        await self.gateway.replace(instance_id, payload["ticket_id"], price)
                    ).model_dump(mode="json")
                elif action == "cancel":
                    value = (
                        await self.gateway.cancel(instance_id, payload["ticket_id"])
                    ).model_dump(mode="json")
                elif action == "tickets":
                    value = {
                        "tickets": [
                            ticket.model_dump(mode="json")
                            for ticket in self.ledger.tickets_for_strategy(instance_id)
                        ]
                    }
                elif action == "checkpoint":
                    self.ledger.save_checkpoint(instance_id, payload["key"], payload["value"])
                    value = {}
                elif action == "restore":
                    value = {"value": self.ledger.load_checkpoint(instance_id, payload["key"])}
                elif action == "log":
                    self.ledger.audit("STRATEGY_LOG", payload, instance_id)
                    value = {}
                else:
                    raise ValueError(f"Unknown worker action: {action}")
                await asyncio.to_thread(handle.rpc.send, {"ok": True, "value": value})
            except Exception as exc:
                await asyncio.to_thread(
                    handle.rpc.send,
                    {"ok": False, "code": type(exc).__name__, "message": str(exc)},
                )

    async def _monitor(self, instance_id: str, handle: WorkerHandle) -> None:
        await asyncio.to_thread(handle.process.join)
        if self._workers.get(instance_id) is not handle:
            return
        self._workers.pop(instance_id, None)
        if not handle.ready.done():
            handle.ready.set_exception(
                RuntimeError(
                    f"Strategy worker exited during startup with code {handle.process.exitcode}"
                )
            )
        current_task = asyncio.current_task()
        for task in handle.tasks:
            if task is not current_task:
                task.cancel()
        handle.rpc.close()
        handle.events.close()
        instance = self.ledger.instance(instance_id)
        if instance:
            instance["state"] = StrategyState.DEGRADED
            self.ledger.save_instance(instance)
            self.ledger.end_active_run(instance_id, "CRASHED")
        self.ledger.audit(
            "STRATEGY_WORKER_EXITED",
            {"exit_code": handle.process.exitcode},
            instance_id,
        )

    async def _forward_orders(self, instance_id: str, handle: WorkerHandle) -> None:
        async for ticket in handle.order_session.events():
            if not handle.process.is_alive():
                return
            async with handle.event_lock:
                await asyncio.to_thread(
                    handle.events.send, {"kind": "order", "value": ticket.model_dump(mode="json")}
                )

    async def _timer_loop(self, handle: WorkerHandle) -> None:
        previous_window: bool | None = None
        while handle.process.is_alive():
            now = datetime.now(UTC)
            if handle.schedule:
                current_window = handle.schedule.is_open(now)
                if previous_window is not None and current_window != previous_window:
                    kind = "window_open" if current_window else "window_close"
                    async with handle.event_lock:
                        await asyncio.to_thread(
                            handle.events.send,
                            {"kind": kind, "value": {"at": now.isoformat()}},
                        )
                previous_window = current_window
            async with handle.event_lock:
                await asyncio.to_thread(
                    handle.events.send,
                    {"kind": "timer", "value": {"at": now.isoformat()}},
                )
            await asyncio.sleep(1)
