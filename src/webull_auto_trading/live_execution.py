from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any, Protocol

from webull_auto_trading.config import Settings
from webull_auto_trading.domain import (
    LiveIntentAction,
    LiveIntentStatus,
    LiveOrderIntent,
    OrderSide,
    RuntimeMode,
    StrategyInstance,
    new_id,
)
from webull_auto_trading.persistence import RuntimeRepository
from webull_auto_trading.webull import WebullError, WebullTradingClient

TERMINAL_LIVE_STATUSES = {
    LiveIntentStatus.FILLED,
    LiveIntentStatus.REJECTED,
    LiveIntentStatus.CANCELLED,
}


class LiveExecutionBlocked(RuntimeError):
    pass


class LiveOrderClient(Protocol):
    async def place_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]:
        ...

    async def preview_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]:
        ...

    async def order_detail(
        self,
        account_id: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        ...

    async def account_balance(self, account_id: str) -> dict[str, Any]:
        ...

    async def positions(self, account_id: str) -> list[dict[str, Any]]:
        ...


@dataclass(slots=True)
class LiveMarketOrderRequest:
    strategy: StrategyInstance
    cycle_id: str
    action: LiveIntentAction
    side: OrderSide
    quantity: float
    execution_key: str = ""
    virtual_order_ids: list[str] | None = None


def validate_live_strategy_config(
    strategy: StrategyInstance,
    settings: Settings,
) -> list[str]:
    errors: list[str] = []
    if strategy.live_execution_enabled:
        if not settings.live_execution_master_enable:
            errors.append("live execution master enable is off")
        if not strategy.account_alias:
            errors.append("account_alias is required for live execution")
        elif not settings.resolve_webull_account_alias(strategy.account_alias):
            errors.append(f"account alias is not configured: {strategy.account_alias}")
        if not strategy.webull_symbol:
            errors.append("webull_symbol is required for live execution")
        if not strategy.asset_class:
            errors.append("asset_class is required for live execution")
    return errors


def stable_client_order_id(
    *,
    strategy_instance_id: str,
    cycle_id: str,
    action: LiveIntentAction,
    side: OrderSide,
    webull_symbol: str,
    quantity: float,
    execution_key: str = "",
) -> str:
    seed = "|".join(
        [
            strategy_instance_id,
            cycle_id,
            action.value,
            side.value,
            webull_symbol,
            _quantity_string(quantity),
            execution_key,
        ]
    )
    return "wat" + sha256(seed.encode("utf-8")).hexdigest()[:29]


def build_market_order_request(
    *,
    client_order_id: str,
    webull_symbol: str,
    asset_class: str,
    side: OrderSide,
    quantity: float,
) -> dict[str, Any]:
    instrument_type = _instrument_type(asset_class)
    order = {
        "client_order_id": client_order_id,
        "combo_type": "NORMAL",
        "symbol": webull_symbol,
        "instrument_type": instrument_type,
        "market": "US",
        "order_type": "MARKET",
        "quantity": _quantity_string(quantity),
        "side": side.value,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    if instrument_type == "EQUITY":
        order["support_trading_session"] = "CORE"
    return order


class WebullLiveExecutionAdapter:
    def __init__(
        self,
        settings: Settings,
        repository: RuntimeRepository,
        client: LiveOrderClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client or WebullTradingClient(settings)

    async def submit_market_order(
        self,
        request: LiveMarketOrderRequest,
        *,
        runtime_mode: RuntimeMode,
        global_pause: bool,
    ) -> LiveOrderIntent:
        self._ensure_submission_allowed(
            request,
            runtime_mode=runtime_mode,
            global_pause=global_pause,
        )
        account_id = self.settings.resolve_webull_account_alias(request.strategy.account_alias)
        client_order_id = stable_client_order_id(
            strategy_instance_id=request.strategy.id,
            cycle_id=request.cycle_id,
            action=request.action,
            side=request.side,
            webull_symbol=request.strategy.webull_symbol,
            quantity=request.quantity,
            execution_key=request.execution_key,
        )
        order_request = build_market_order_request(
            client_order_id=client_order_id,
            webull_symbol=request.strategy.webull_symbol,
            asset_class=request.strategy.asset_class,
            side=request.side,
            quantity=request.quantity,
        )
        intent = LiveOrderIntent(
            id=new_id("loi"),
            strategy_instance_id=request.strategy.id,
            cycle_id=request.cycle_id,
            action=request.action,
            side=request.side,
            quantity=request.quantity,
            market_data_symbol=request.strategy.market_data_symbol or request.strategy.symbol,
            webull_symbol=request.strategy.webull_symbol,
            account_alias=request.strategy.account_alias,
            account_id=account_id,
            client_order_id=client_order_id,
            execution_key=request.execution_key,
            virtual_order_ids=list(request.virtual_order_ids or []),
            request=order_request,
        )
        self.repository.upsert_live_order_intent(intent)
        try:
            response = await self.client.place_order(account_id, order_request)
        except WebullError as exc:
            intent.status = LiveIntentStatus.REJECTED
            intent.error_message = exc.message
            self.repository.upsert_live_order_intent(intent)
            self.repository.log_live_reconciliation_event(
                strategy_instance_id=request.strategy.id,
                intent_id=intent.id,
                status=intent.status,
                message="Live order submission rejected by Webull",
                payload={"code": exc.code},
            )
            return intent
        except Exception as exc:
            intent.status = LiveIntentStatus.UNKNOWN
            intent.error_message = _safe_error_message(exc, self.settings, account_id)
            self.repository.upsert_live_order_intent(intent)
            self.repository.log_live_reconciliation_event(
                strategy_instance_id=request.strategy.id,
                intent_id=intent.id,
                status=intent.status,
                message="Live order submission status is unknown",
            )
            return intent
        intent.status = LiveIntentStatus.SUBMITTED
        intent.response = response
        self.repository.upsert_live_order_intent(intent)
        self.repository.log_live_reconciliation_event(
            strategy_instance_id=request.strategy.id,
            intent_id=intent.id,
            status=intent.status,
            message="Live order submitted; awaiting reconciliation",
            payload={"client_order_id": client_order_id},
        )
        return intent

    def _ensure_submission_allowed(
        self,
        request: LiveMarketOrderRequest,
        *,
        runtime_mode: RuntimeMode,
        global_pause: bool,
    ) -> None:
        if runtime_mode != RuntimeMode.LIVE:
            raise LiveExecutionBlocked("runtime mode is not live")
        if global_pause:
            raise LiveExecutionBlocked("global pause is enabled")
        if not request.strategy.enabled:
            raise LiveExecutionBlocked("strategy is paused")
        errors = validate_live_strategy_config(request.strategy, self.settings)
        if errors:
            raise LiveExecutionBlocked("; ".join(errors))
        if request.quantity <= 0:
            raise LiveExecutionBlocked("quantity must be positive")
        if (
            request.strategy.asset_class.replace("-", "_").lower() in {"future", "futures"}
            and not float(request.quantity).is_integer()
        ):
            raise LiveExecutionBlocked("futures quantity must be a positive whole contract")
        if self.repository.has_unresolved_live_intent(request.strategy.id):
            raise LiveExecutionBlocked("strategy has an unresolved live order intent")


def _instrument_type(asset_class: str) -> str:
    normalized = asset_class.replace("-", "_").lower()
    if normalized in {"stock", "stocks", "equity", "equities", "etf"}:
        return "EQUITY"
    if normalized in {"future", "futures"}:
        return "FUTURES"
    if normalized == "crypto":
        return "CRYPTO"
    raise LiveExecutionBlocked(f"unsupported live asset_class: {asset_class}")


def _quantity_string(quantity: float) -> str:
    value = Decimal(str(quantity)).normalize()
    return format(value, "f")


def _safe_error_message(
    exc: Exception,
    settings: Settings,
    account_id: str,
) -> str:
    message = str(exc).strip() or type(exc).__name__
    for secret in (
        settings.webull_prod_app_key,
        settings.webull_prod_app_secret,
        account_id,
    ):
        if secret:
            message = message.replace(secret, "<redacted>")
    return message[:500]
