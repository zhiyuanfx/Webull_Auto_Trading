from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from webull_auto_trading.config import get_settings
from webull_auto_trading.execution import AccountSnapshot, WebullReadService
from webull_auto_trading.runtime import RuntimeService


class GlobalPausePayload(BaseModel):
    paused: bool


class StrategyPayload(BaseModel):
    id: str | None = None
    strategy_name: str = "day_many_bian"
    symbol: str
    account_id: str = ""
    enabled: bool = True
    mode: str = Field(default="paper", pattern="^(paper|preview)$")
    params: dict[str, Any] = Field(default_factory=dict)


class QuotePayload(BaseModel):
    code: str
    bid: float | None = None
    ask: float | None = None
    last_price: float | None = None
    lp_time: float | None = None
    delay_seconds: int | None = None
    status: str | None = None


@lru_cache
def get_runtime() -> RuntimeService:
    runtime = RuntimeService(get_settings())
    runtime.initialize()
    return runtime


def create_app() -> FastAPI:
    app = FastAPI(title="Webull Auto Trading Runtime")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return get_runtime().health()

    @app.get("/api/settings/global-pause")
    def get_global_pause() -> dict[str, bool]:
        runtime = get_runtime()
        return {"global_pause": runtime.risk.global_pause}

    @app.put("/api/settings/global-pause")
    def put_global_pause(payload: GlobalPausePayload) -> dict[str, bool]:
        return get_runtime().set_global_pause(payload.paused)

    @app.get("/api/strategies")
    def list_strategies() -> list[dict[str, Any]]:
        return [asdict(item) for item in get_runtime().repository.list_strategy_instances()]

    @app.post("/api/strategies", status_code=201)
    def create_strategy(payload: StrategyPayload) -> dict[str, Any]:
        instance = get_runtime().repository.create_strategy_instance(payload.model_dump())
        return asdict(instance)

    @app.put("/api/strategies/{strategy_id}")
    def update_strategy(strategy_id: str, payload: StrategyPayload) -> dict[str, Any]:
        try:
            instance = get_runtime().repository.update_strategy_instance(
                strategy_id,
                payload.model_dump(exclude_unset=True),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        return asdict(instance)

    @app.get("/api/market/quotes")
    def list_quotes() -> list[dict[str, Any]]:
        return get_runtime().repository.list_quote_snapshots()

    @app.post("/api/market/quotes")
    def ingest_quote(payload: QuotePayload) -> dict[str, Any]:
        messages = get_runtime().ingest_quote_item(payload.model_dump(exclude_none=True))
        return {"messages": messages}

    @app.get("/api/market/bars")
    def list_bars() -> list[dict[str, Any]]:
        return get_runtime().repository.list_table("bars")

    @app.get("/api/orders")
    def list_orders() -> list[dict[str, Any]]:
        return [asdict(item) for item in get_runtime().order_book.orders]

    @app.get("/api/cycles")
    def list_cycles() -> list[dict[str, Any]]:
        return get_runtime().repository.list_table("cycles")

    @app.get("/api/account")
    async def account(refresh: bool = False) -> dict[str, Any]:
        settings = get_settings()
        if not refresh:
            snapshot = AccountSnapshot(
                configured=settings.production_configured,
                account_id=settings.webull_account_id,
                error=None if settings.production_configured else "Webull credentials missing",
            )
            return asdict(snapshot)
        snapshot = await WebullReadService(settings).snapshot()
        return asdict(snapshot)

    @app.get("/api/activity")
    def activity() -> list[dict[str, Any]]:
        return get_runtime().repository.list_table("activity")

    return app


app = create_app()
