from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from webull_auto_trading.config import get_settings
from webull_auto_trading.domain import RuntimeMode
from webull_auto_trading.execution import AccountSnapshot, WebullReadService
from webull_auto_trading.runtime import RuntimeService


class GlobalPausePayload(BaseModel):
    paused: bool


class RuntimeModePayload(BaseModel):
    mode: RuntimeMode


class StrategyUpdatePayload(BaseModel):
    enabled: bool


class PaperDepositPayload(BaseModel):
    amount: float = Field(gt=0)


class CleanupPayload(BaseModel):
    dry_run: bool = True
    activity_days: int = Field(default=30, ge=1)
    paper_history_days: int = Field(default=365, ge=1)
    closed_cycle_days: int = Field(default=365, ge=1)
    vacuum: bool = False


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

    @app.get("/api/settings/runtime-mode")
    def get_runtime_mode() -> dict[str, Any]:
        runtime = get_runtime()
        return {
            "mode": runtime.active_mode().value,
            "message": runtime.mode_message(),
            "config_path": str(runtime.mode_config_path()),
        }

    @app.put("/api/settings/runtime-mode")
    def put_runtime_mode(payload: RuntimeModePayload) -> dict[str, Any]:
        return get_runtime().set_runtime_mode(payload.mode)

    @app.get("/api/strategies")
    def list_strategies() -> list[dict[str, Any]]:
        return [asdict(item) for item in get_runtime().repository.list_strategy_instances()]

    @app.put("/api/strategies/{strategy_id}")
    def update_strategy(strategy_id: str, payload: StrategyUpdatePayload) -> dict[str, Any]:
        try:
            return get_runtime().set_strategy_enabled(strategy_id, payload.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc

    @app.post("/api/strategies/flatten")
    def flatten_all_strategies() -> dict[str, Any]:
        try:
            return get_runtime().flatten_all_strategies()
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/strategies/{strategy_id}/flatten")
    def flatten_strategy(strategy_id: str) -> dict[str, Any]:
        try:
            return get_runtime().flatten_strategy(strategy_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/market/streams/strategies")
    def list_stream_strategies() -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "strategy_name": item.strategy_name,
                "symbol": item.symbol,
                "enabled": item.enabled,
            }
            for item in get_runtime().repository.list_strategy_instances()
            if item.enabled
        ]

    @app.get("/api/market/streams/symbols")
    def list_stream_symbols() -> list[dict[str, Any]]:
        return get_runtime().enabled_symbol_streams()

    @app.get("/api/market/streams/by-symbol")
    def list_market_stream_by_symbol(symbol: str, since: int | None = None) -> list[dict[str, Any]]:
        return [
            asdict(item)
            for item in get_runtime().stream_buffer.list_for_symbol(symbol, since=since)
        ]

    @app.get("/api/market/streams/{strategy_id}")
    def list_market_stream(strategy_id: str, since: int | None = None) -> list[dict[str, Any]]:
        return [
            asdict(item)
            for item in get_runtime().stream_buffer.list_for_strategy(strategy_id, since=since)
        ]

    @app.get("/api/orders")
    def list_orders() -> list[dict[str, Any]]:
        return [asdict(item) for item in get_runtime().order_book.orders]

    @app.get("/api/cycles")
    def list_cycles() -> list[dict[str, Any]]:
        return get_runtime().repository.list_table("cycles")

    @app.get("/api/account")
    async def account(refresh: bool = False) -> dict[str, Any]:
        runtime = get_runtime()
        if runtime.active_mode() == RuntimeMode.TEST:
            paper = runtime.repository.recompute_paper_account(
                market_prices=runtime.current_market_prices()
            )
            return {
                "mode": RuntimeMode.TEST.value,
                "message": runtime.mode_message(),
                "paper_account": asdict(paper),
                "positions": [asdict(item) for item in runtime.repository.list_paper_positions()],
                "history": runtime.repository.list_paper_history(limit=20),
            }
        settings = get_settings()
        if not refresh:
            snapshot = AccountSnapshot(
                configured=settings.production_configured,
                account_id=settings.webull_account_id,
                error=None if settings.production_configured else "Webull credentials missing",
            )
            return asdict(snapshot)
        snapshot = await WebullReadService(settings).snapshot()
        return {
            "mode": RuntimeMode.LIVE.value,
            "message": runtime.mode_message(),
            **asdict(snapshot),
        }

    @app.get("/api/paper-account")
    def paper_account() -> dict[str, Any]:
        runtime = get_runtime()
        account = runtime.repository.recompute_paper_account(
            market_prices=runtime.current_market_prices()
        )
        return {
            "account": asdict(account),
            "positions": [asdict(item) for item in runtime.repository.list_paper_positions()],
        }

    @app.post("/api/paper-account/deposit")
    def paper_deposit(payload: PaperDepositPayload) -> dict[str, Any]:
        account = get_runtime().repository.deposit_paper_account(payload.amount)
        return {"account": asdict(account)}

    @app.post("/api/paper-account/reset")
    def paper_reset() -> dict[str, Any]:
        runtime = get_runtime()
        account = runtime.repository.reset_paper_account()
        runtime.order_book.orders = []
        runtime.order_book.fills = []
        return {"account": asdict(account)}

    @app.get("/api/paper-account/history")
    def paper_history() -> list[dict[str, Any]]:
        return get_runtime().repository.list_paper_history()

    @app.get("/api/storage/stats")
    def storage_stats() -> dict[str, Any]:
        return get_runtime().repository.storage_stats()

    @app.post("/api/storage/cleanup")
    def storage_cleanup(payload: CleanupPayload) -> dict[str, Any]:
        return get_runtime().repository.cleanup(**payload.model_dump())

    @app.get("/api/activity")
    def activity() -> list[dict[str, Any]]:
        return get_runtime().repository.list_table("activity")

    return app


app = create_app()
