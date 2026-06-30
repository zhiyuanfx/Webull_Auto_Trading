from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from webull_bridge.config import Settings, get_settings
from webull_bridge.domain import (
    BridgePayload,
    EventStatus,
    RouteConfig,
    RouteUpdate,
    verify_secret,
)
from webull_bridge.executor import BridgeExecutor
from webull_bridge.persistence import Ledger
from webull_bridge.webull import WebullError, WebullTradingClient


class ExecutionSwitch(BaseModel):
    enabled: bool


class Confirmation(BaseModel):
    confirmation: str


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ledger = Ledger(settings.bridge_db_path)
        self.ledger.initialize()
        self.ledger.bootstrap(
            execution_enabled=settings.bridge_execution_enabled,
            route=RouteConfig(
                route_id=settings.bridge_default_route_id,
                name=settings.bridge_default_route_name,
                account_id=settings.webull_account_id,
            ),
            secret=settings.bridge_default_secret,
        )
        self.client = WebullTradingClient(settings)
        self.executor = BridgeExecutor(self.ledger, self.client)
        self.queue_task: asyncio.Task[None] | None = None

    async def queue_loop(self) -> None:
        while True:
            try:
                await self.executor.process_queue(limit=20)
            except Exception as exc:
                self.ledger.audit(
                    "QUEUE_LOOP_ERROR",
                    "error",
                    str(exc)[:500],
                    {"error": type(exc).__name__},
                )
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = AppState(get_settings())
    app.state.bridge = state
    state.queue_task = asyncio.create_task(state.queue_loop())
    try:
        yield
    finally:
        if state.queue_task:
            state.queue_task.cancel()
            try:
                await state.queue_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Webull Bridge", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def bridge(request: Request) -> AppState:
    return request.app.state.bridge


def route_public(route: RouteConfig) -> dict[str, object]:
    payload = route.model_dump(mode="json")
    payload["secret_configured"] = bool(route.secret_hash)
    payload.pop("secret_hash", None)
    return payload


@app.get("/api/health")
async def health(request: Request) -> dict[str, object]:
    state = bridge(request)
    routes = state.ledger.list_routes()
    return {
        "status": "ok",
        "database": str(state.settings.bridge_db_path),
        "webull_configured": state.settings.production_configured,
        "execution_enabled": state.executor.execution_enabled,
        "token_dir": str(state.settings.webull_token_dir / "live"),
        "routes": [route_public(route) for route in routes],
    }


@app.get("/api/settings/execution")
async def get_execution(request: Request) -> dict[str, bool]:
    return {"enabled": bridge(request).executor.execution_enabled}


@app.put("/api/settings/execution")
async def set_execution(request: Request, switch: ExecutionSwitch) -> dict[str, bool]:
    state = bridge(request)
    state.executor.set_execution_enabled(switch.enabled)
    if switch.enabled:
        asyncio.create_task(state.executor.process_queue(limit=100))
    return {"enabled": state.executor.execution_enabled}


@app.get("/api/routes")
async def list_routes(request: Request) -> list[dict[str, object]]:
    return [route_public(route) for route in bridge(request).ledger.list_routes()]


@app.get("/api/routes/{route_id}")
async def get_route(request: Request, route_id: str) -> dict[str, object]:
    route = bridge(request).ledger.get_route(route_id)
    if route is None:
        raise HTTPException(404, "Route not found")
    return route_public(route)


@app.put("/api/routes/{route_id}")
async def update_route(request: Request, route_id: str, update: RouteUpdate) -> dict[str, object]:
    state = bridge(request)
    try:
        route = state.ledger.update_route(route_id, update)
    except KeyError as exc:
        raise HTTPException(404, "Route not found") from exc
    return route_public(route)


@app.post("/api/routes/{route_id}/validate")
async def validate_payload(request: Request, route_id: str, payload: dict) -> dict[str, object]:
    state = bridge(request)
    route = state.ledger.get_route(route_id)
    if route is None:
        raise HTTPException(404, "Route not found")
    secret = str(payload.get("secret", ""))
    if not verify_secret(secret, route.secret_hash):
        raise HTTPException(401, "Invalid webhook secret")
    try:
        parsed = BridgePayload.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc
    return {"ok": True, "payload": parsed.sanitized()}


@app.post("/webhook/tradingview/{route_id}")
async def tradingview_webhook(
    request: Request, route_id: str, background_tasks: BackgroundTasks
) -> dict[str, object]:
    state = bridge(request)
    route = state.ledger.get_route(route_id)
    if route is None:
        state.ledger.audit(
            "WEBHOOK_ROUTE_MISSING", "warning", "Unknown route", {"route_id": route_id}
        )
        raise HTTPException(404, "Route not found")
    try:
        body = await request.json()
    except Exception as exc:
        state.ledger.audit(
            "WEBHOOK_INVALID_JSON", "warning", "Invalid JSON body", {"route_id": route_id}
        )
        raise HTTPException(400, "Invalid JSON") from exc
    secret = str(body.get("secret", ""))
    if not verify_secret(secret, route.secret_hash):
        state.ledger.audit(
            "WEBHOOK_AUTH_FAILED", "warning", "Invalid webhook secret", {"route_id": route_id}
        )
        raise HTTPException(401, "Invalid webhook secret")
    try:
        payload = BridgePayload.model_validate(body)
    except ValidationError as exc:
        sanitized = dict(body)
        sanitized.pop("secret", None)
        state.ledger.audit(
            "WEBHOOK_VALIDATION_FAILED",
            "warning",
            "TradingView payload failed validation",
            {"route_id": route_id, "payload": sanitized, "errors": exc.errors()},
        )
        raise HTTPException(422, exc.errors()) from exc
    stored_payload = payload.sanitized()
    stored_payload["secret"] = "<redacted>"
    event, duplicate = state.ledger.create_event(
        route_id,
        payload.event_id,
        payload.action.value,
        payload.symbol,
        stored_payload,
    )
    if duplicate:
        state.ledger.audit(
            "WEBHOOK_DUPLICATE",
            "info",
            "Duplicate TradingView event ignored",
            {"route_id": route_id, "event_id": payload.event_id},
        )
        return {"ok": True, "status": EventStatus.DUPLICATE.value, "event_id": payload.event_id}
    if state.executor.execution_enabled and route.enabled:
        background_tasks.add_task(state.executor.process_event, int(event["id"]))
    return {"ok": True, "status": EventStatus.QUEUED.value, "event_id": payload.event_id}


@app.get("/api/events")
async def list_events(request: Request, limit: int = 100) -> list[dict[str, object]]:
    return bridge(request).ledger.list_events(limit=limit)


@app.post("/api/events/{event_pk}/retry")
async def retry_event(request: Request, event_pk: int) -> dict[str, object]:
    state = bridge(request)
    event = state.ledger.get_event(event_pk)
    if event["status"] not in {EventStatus.FAILED.value, EventStatus.UNKNOWN.value}:
        raise HTTPException(409, "Only failed or unknown events can be retried")
    state.ledger.update_event(event_pk, EventStatus.QUEUED.value, error=None)
    asyncio.create_task(state.executor.process_event(event_pk))
    return {"ok": True, "status": EventStatus.QUEUED.value}


@app.get("/api/orders")
async def list_orders(request: Request, limit: int = 100) -> list[dict[str, object]]:
    return bridge(request).ledger.list_orders(limit=limit)


@app.get("/api/activity")
async def list_activity(request: Request, limit: int = 200) -> list[dict[str, object]]:
    return bridge(request).ledger.list_activity(limit=limit)


@app.get("/api/positions")
async def positions(request: Request, account_id: str | None = None) -> dict[str, object]:
    state = bridge(request)
    account = account_id or next(
        (route.account_id for route in state.ledger.list_routes() if route.account_id), ""
    )
    if not account:
        return {"account_id": "", "positions": []}
    try:
        values = await state.executor.refresh_positions(account)
    except WebullError as exc:
        values = state.ledger.latest_position_snapshot(account)
        return {"account_id": account, "positions": values, "stale": True, "error": exc.message}
    return {"account_id": account, "positions": values, "stale": False}


@app.get("/api/open-orders")
async def open_orders(request: Request, account_id: str | None = None) -> dict[str, object]:
    state = bridge(request)
    account = account_id or next(
        (route.account_id for route in state.ledger.list_routes() if route.account_id), ""
    )
    if not account:
        return {"account_id": "", "orders": []}
    try:
        values = await state.executor.reconcile_open_orders(account)
    except WebullError as exc:
        return {"account_id": account, "orders": [], "error": exc.message}
    return {"account_id": account, "orders": values}


@app.post("/api/emergency/pause")
async def emergency_pause(request: Request, confirmation: Confirmation) -> dict[str, object]:
    if confirmation.confirmation != "PAUSE ALL":
        raise HTTPException(400, "Type PAUSE ALL to confirm")
    state = bridge(request)
    state.executor.set_execution_enabled(False)
    for route in state.ledger.list_routes():
        state.ledger.update_route(route.route_id, RouteUpdate(enabled=False))
    state.ledger.audit("EMERGENCY_PAUSE", "warning", "All routes paused", {})
    return {"ok": True}


@app.post("/api/emergency/cancel-known")
async def emergency_cancel_known(request: Request, confirmation: Confirmation) -> dict[str, object]:
    if confirmation.confirmation != "CANCEL KNOWN OPEN ORDERS":
        raise HTTPException(400, "Type CANCEL KNOWN OPEN ORDERS to confirm")
    state = bridge(request)
    results = []
    for item in state.ledger.known_open_order_refs():
        try:
            response = await state.client.cancel_order(item["account_id"], item["client_order_id"])
            results.append(
                {"client_order_id": item["client_order_id"], "ok": True, "response": response}
            )
        except WebullError as exc:
            results.append(
                {"client_order_id": item["client_order_id"], "ok": False, "error": exc.message}
            )
    state.ledger.audit(
        "EMERGENCY_CANCEL_KNOWN", "warning", "Known open order cancellation attempted", {}
    )
    return {"ok": True, "results": results}


@app.post("/api/routes/{route_id}/flatten/{symbol}")
async def flatten_symbol(
    request: Request, route_id: str, symbol: str, quantity: str, confirmation: Confirmation
) -> dict[str, object]:
    if confirmation.confirmation != f"FLATTEN {symbol.upper()}":
        raise HTTPException(400, f"Type FLATTEN {symbol.upper()} to confirm")
    state = bridge(request)
    route = state.ledger.require_route(route_id)
    payload = {
        "secret": "<internal>",
        "event_id": f"manual-{uuid4().hex}",
        "action": "FLATTEN",
        "symbol": symbol.upper(),
        "quantity": quantity,
    }
    event, _ = state.ledger.create_event(
        route_id, payload["event_id"], "FLATTEN", symbol.upper(), payload
    )
    state.ledger.update_event(int(event["id"]), EventStatus.QUEUED.value)
    if state.executor.execution_enabled and route.enabled:
        asyncio.create_task(state.executor.process_event(int(event["id"])))
    return {"ok": True, "event_id": payload["event_id"]}


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        target = frontend_dist / path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(frontend_dist / "index.html")
