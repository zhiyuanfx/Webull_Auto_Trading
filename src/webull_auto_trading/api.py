from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from webull_auto_trading.config import get_settings
from webull_auto_trading.domain import RuntimeMode
from webull_auto_trading.execution import AccountSnapshot, LiveOrdersSnapshot, WebullReadService
from webull_auto_trading.insightsentry_stream import InsightSentryQuoteStreamService
from webull_auto_trading.live_reconciliation import LiveRuntimeCoordinator
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


@lru_cache
def get_stream_service() -> InsightSentryQuoteStreamService:
    return InsightSentryQuoteStreamService(get_runtime())


@lru_cache
def get_live_coordinator() -> LiveRuntimeCoordinator:
    return LiveRuntimeCoordinator(get_runtime())


LIVE_ACCOUNT_CACHE: dict[str, AccountSnapshot] = {}
LIVE_ORDERS_CACHE: dict[str, LiveOrdersSnapshot] = {}


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stream_service = get_stream_service()
        live_coordinator = get_live_coordinator()
        stream_service.start()
        live_coordinator.start()
        try:
            yield
        finally:
            await live_coordinator.stop()
            await stream_service.stop()

    app = FastAPI(title="Webull Auto Trading Runtime", lifespan=lifespan)

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

    @app.post("/api/live/strategies/{strategy_id}/enable-execution")
    def enable_live_execution(strategy_id: str) -> dict[str, Any]:
        try:
            return get_runtime().set_strategy_live_execution(strategy_id, True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/live/strategies/{strategy_id}/disable-execution")
    def disable_live_execution(strategy_id: str) -> dict[str, Any]:
        try:
            return get_runtime().set_strategy_live_execution(strategy_id, False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/live/reconciliation")
    def live_reconciliation() -> dict[str, Any]:
        return get_runtime().live_reconciliation_snapshot()

    @app.get("/api/live/orders")
    def live_orders() -> list[dict[str, Any]]:
        return [
            _safe_live_intent(asdict(item))
            for item in get_runtime().repository.list_live_order_intents()
        ]

    @app.get("/api/orders-view")
    async def orders_view(
        refresh: bool = False,
        account_alias: str | None = None,
    ) -> dict[str, Any]:
        runtime = get_runtime()
        if runtime.active_mode() == RuntimeMode.TEST:
            return {
                "mode": RuntimeMode.TEST.value,
                "paper_orders": [asdict(item) for item in runtime.repository.list_paper_orders()],
                "paper_fills": [asdict(item) for item in runtime.repository.list_paper_fills()],
                "paper_cycles": runtime.repository.list_cycles(RuntimeMode.TEST),
            }

        settings = get_settings()
        selected_alias = account_alias or settings.webull_account_default_alias or ""
        account_id = settings.resolve_webull_account_alias(selected_alias)
        if selected_alias and not account_id:
            raise HTTPException(
                status_code=404,
                detail=f"account alias is not configured: {selected_alias}",
            )
        snapshot = LIVE_ORDERS_CACHE.get(selected_alias)
        if refresh or snapshot is None:
            snapshot = await WebullReadService(settings).live_orders(account_id)
            if not snapshot.error or snapshot.open_orders or snapshot.order_history:
                LIVE_ORDERS_CACHE[selected_alias] = snapshot
        secret_values = {account_id} if account_id else set()
        return {
            "mode": RuntimeMode.LIVE.value,
            "account_alias": selected_alias,
            **_safe_live_orders_snapshot(snapshot, secret_values),
            "live_intents": [
                _safe_live_intent(asdict(item), secret_values)
                for item in runtime.repository.list_live_order_intents()
            ],
            "live_virtual_orders": [
                asdict(item) for item in runtime.repository.list_live_virtual_orders()
            ],
            "live_cycles": runtime.repository.list_cycles(RuntimeMode.LIVE),
            "reconciliation_events": [
                _redact_sensitive_values(row, secret_values)
                for row in runtime.repository.list_table("live_reconciliation_events")
            ],
        }

    @app.post("/api/live/strategies/{strategy_id}/flatten")
    def live_flatten(strategy_id: str) -> dict[str, Any]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Live market flatten for {strategy_id} is not wired until "
                "submit/reconciliation controls are completed"
            ),
        )

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

    @app.get("/api/market/stream-status")
    def market_stream_status() -> dict[str, Any]:
        return get_stream_service().status()

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
        runtime = get_runtime()
        return runtime.repository.list_cycles(runtime.active_mode())

    @app.get("/api/account")
    async def account(refresh: bool = False, account_alias: str | None = None) -> dict[str, Any]:
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
        selected_alias = account_alias or settings.webull_account_default_alias
        account_id = settings.resolve_webull_account_alias(selected_alias)
        if selected_alias and not account_id:
            raise HTTPException(
                status_code=404,
                detail=f"account alias is not configured: {selected_alias}",
            )
        if not refresh:
            snapshot = LIVE_ACCOUNT_CACHE.get(selected_alias) or AccountSnapshot(
                configured=settings.production_configured,
                account_id=account_id,
                error=None if settings.production_configured else "Webull credentials missing",
            )
            return {
                "mode": RuntimeMode.LIVE.value,
                "message": runtime.mode_message(),
                "account_alias": selected_alias,
                **_safe_account_snapshot(snapshot),
            }
        snapshot = await WebullReadService(settings).snapshot(account_id)
        if not snapshot.error:
            LIVE_ACCOUNT_CACHE[selected_alias] = snapshot
        return {
            "mode": RuntimeMode.LIVE.value,
            "message": runtime.mode_message(),
            "account_alias": selected_alias,
            **_safe_account_snapshot(snapshot),
        }

    @app.get("/api/webull/account-aliases")
    async def webull_account_aliases(refresh: bool = False) -> dict[str, Any]:
        settings = get_settings()
        metadata: dict[str, dict[str, Any]] = {}
        if refresh and settings.production_configured:
            from webull_auto_trading.cli import fetch_accounts
            from webull_auto_trading.webull import WebullError

            try:
                summary = await fetch_accounts(raw=False)
            except WebullError as exc:
                raise HTTPException(status_code=502, detail=exc.message) from exc
            metadata = _safe_alias_metadata(settings.webull_account_aliases(), summary)
        return {
            "default_alias": settings.webull_account_default_alias,
            "aliases": [
                {"alias": alias, "configured": True, **metadata.get(alias, {})}
                for alias in sorted(settings.webull_account_aliases())
            ],
            "legacy_account_id_configured": bool(settings.webull_account_id),
        }

    @app.get("/api/webull/accounts")
    async def webull_accounts(raw: bool = False) -> dict[str, Any]:
        from webull_auto_trading.cli import fetch_accounts
        from webull_auto_trading.webull import WebullError

        try:
            return _safe_accounts_summary(await fetch_accounts(raw=raw))
        except WebullError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc

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


def _safe_account_snapshot(snapshot: AccountSnapshot) -> dict[str, Any]:
    return {
        "configured": snapshot.configured,
        "account_id_configured": bool(snapshot.account_id),
        "balance": snapshot.balance,
        "positions": snapshot.positions,
        "open_orders": snapshot.open_orders,
        "error": snapshot.error,
    }


def _safe_live_orders_snapshot(
    snapshot: LiveOrdersSnapshot,
    secret_values: set[str],
) -> dict[str, Any]:
    return {
        "configured": snapshot.configured,
        "account_id_configured": bool(snapshot.account_id),
        "broker_open_orders": _redact_sensitive_values(snapshot.open_orders, secret_values),
        "broker_order_history": _redact_sensitive_values(snapshot.order_history, secret_values),
        "error": _redact_sensitive_values(snapshot.error, secret_values),
    }


def _safe_live_intent(
    row: dict[str, Any],
    secret_values: set[str] | None = None,
) -> dict[str, Any]:
    safe = dict(row)
    account_id = safe.pop("account_id", "")
    values = set(secret_values or set())
    if account_id:
        values.add(str(account_id))
    return _redact_sensitive_values(safe, values)


def _redact_sensitive_values(value: Any, secret_values: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_values(item, secret_values)
            for key, item in value.items()
            if key not in {"account_id", "accountId", "accountID"}
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item, secret_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            if secret:
                redacted = redacted.replace(secret, "<redacted>")
        return redacted
    return value


def _safe_alias_metadata(
    aliases: dict[str, str],
    account_summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    accounts = account_summary.get("accounts") or []
    by_id = {
        str(account.get("account_id") or ""): account
        for account in accounts
        if isinstance(account, dict)
    }
    safe: dict[str, dict[str, Any]] = {}
    for alias, account_id in aliases.items():
        account = by_id.get(account_id)
        if not account:
            continue
        safe[alias] = {
            key: account[key]
            for key in ("account_type", "accountType", "account_label", "account_class")
            if key in account
        }
    return safe


def _safe_accounts_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_count": summary.get("account_count", 0),
        "accounts": [
            {
                key: value
                for key, value in account.items()
                if key
                not in {
                    "account_id",
                    "accountId",
                    "account_number",
                    "accountNumber",
                    "user_id",
                    "userId",
                }
            }
            for account in summary.get("accounts", [])
            if isinstance(account, dict)
        ],
    }


app = create_app()
