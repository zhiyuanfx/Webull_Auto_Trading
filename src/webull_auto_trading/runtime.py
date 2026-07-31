from __future__ import annotations

import asyncio
import importlib
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import combinations
from threading import RLock
from typing import Any

from webull_auto_trading.config import Settings
from webull_auto_trading.config_loader import load_strategy_instances
from webull_auto_trading.domain import (
    LiveAccountState,
    LiveIntentAction,
    LiveIntentStatus,
    OrderSide,
    OrderStatus,
    PaperOrder,
    QuoteState,
    RuntimeMode,
)
from webull_auto_trading.live_execution import (
    LiveExecutionBlocked,
    LiveMarketOrderRequest,
    LiveOrderClient,
    WebullLiveExecutionAdapter,
    validate_live_strategy_config,
)
from webull_auto_trading.market_data import (
    MarketStreamBuffer,
    QuoteBook,
    parse_market_message,
    parse_series_bars,
    validate_quote,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.persistence import RuntimeRepository
from webull_auto_trading.risk import RiskController
from webull_auto_trading.strategy.base import (
    Strategy,
    StrategyStateResetUnsupported,
)
from webull_auto_trading.strategy.recycle_buy import RecycleBuyStrategy
from webull_auto_trading.webull import WebullError


class StrategyResetBlocked(RuntimeError):
    pass


class StrategyResetBrokerError(RuntimeError):
    pass


class RuntimeService:
    def __init__(
        self,
        settings: Settings,
        *,
        live_order_client: LiveOrderClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = RuntimeRepository(settings.runtime_db_path)
        self.quote_book = QuoteBook()
        self.stream_buffer = MarketStreamBuffer(max_messages=20)
        self.order_book = PaperOrderBook()
        self.live_virtual_book = PaperOrderBook()
        self.live_execution = WebullLiveExecutionAdapter(
            settings,
            self.repository,
            client=live_order_client,
        )
        self.risk = RiskController()
        self.strategies: dict[str, Strategy] = {"recycle_buy": RecycleBuyStrategy()}
        self._restored_strategy_instances: set[str] = set()
        self._strategy_operation_lock = RLock()

    def initialize(self, *, seed_config: bool = True) -> None:
        self.repository.init_db()
        self.risk.global_pause = bool(self.repository.get_setting("global_pause", False))
        self.order_book.orders = self.repository.list_paper_orders()
        self.order_book.fills = self.repository.list_paper_fills()
        self.live_virtual_book.orders = self.repository.list_live_virtual_orders()
        if seed_config:
            self.reload_strategy_config()

    def active_mode(self) -> RuntimeMode:
        return self.repository.get_runtime_mode()

    def mode_config_path(self, mode: RuntimeMode | None = None):
        runtime_mode = mode or self.active_mode()
        if runtime_mode == RuntimeMode.LIVE:
            return self.settings.strategies_live_config_path
        return self.settings.strategies_test_config_path

    def reload_strategy_config(self) -> None:
        path = self.mode_config_path()
        configured = load_strategy_instances(path)
        self.repository.replace_strategy_instances(configured)

    def set_runtime_mode(self, mode: RuntimeMode | str) -> dict[str, Any]:
        with self._strategy_operation_lock:
            runtime_mode = self.repository.set_runtime_mode(mode)
            self.reload_strategy_config()
            self.repository.log_activity(
                "RuntimeModeChanged",
                f"Runtime mode changed to {runtime_mode.value}",
                payload={"mode": runtime_mode.value},
            )
            return {
                "mode": runtime_mode.value,
                "config_path": str(self.mode_config_path(runtime_mode)),
            }

    def health(self) -> dict[str, Any]:
        instances = self.repository.list_strategy_instances()
        quotes = self.quote_book.all()
        return {
            "ok": True,
            "mode": self.active_mode().value,
            "mode_message": self.mode_message(),
            "global_pause": self.risk.global_pause,
            "strategy_count": len(instances),
            "active_strategy_count": len([item for item in instances if item.enabled]),
            "quote_count": len(quotes),
            "webull_configured": self.settings.production_configured,
            "database": str(self.settings.runtime_db_path),
        }

    def mode_message(self) -> str:
        if self.active_mode() == RuntimeMode.LIVE:
            return ""
        return "Test mode: paper trading with real InsightSentry market data"

    def set_global_pause(self, paused: bool) -> dict[str, bool]:
        self.risk.set_global_pause(paused)
        self.repository.set_setting("global_pause", paused)
        self.repository.log_activity(
            "LockGlobal" if paused else "UnlockGlobal",
            "Global pause enabled" if paused else "Global pause disabled",
        )
        return {"global_pause": paused}

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> dict[str, Any]:
        with self._strategy_operation_lock:
            runtime_mode = self.active_mode()
            instance = self.repository.update_strategy_instance(
                strategy_id,
                {"enabled": enabled},
            )
            cancelled_pending = 0
            if not enabled:
                book = (
                    self.live_virtual_book
                    if runtime_mode == RuntimeMode.LIVE
                    else self.order_book
                )
                cancelled_pending = book.cancel_pending(strategy_id)
                if runtime_mode == RuntimeMode.LIVE:
                    self.repository.sync_live_virtual_state(book.orders)
                else:
                    self.repository.sync_paper_state(
                        book.orders,
                        book.fills,
                        market_prices=self.current_market_prices(),
                    )
            self.repository.log_activity(
                "UnlockInstance" if enabled else "LockInstance",
                f"Strategy {strategy_id} {'resumed' if enabled else 'paused'}",
                strategy_instance_id=instance.id,
                symbol=instance.symbol,
            )
            if not enabled:
                cancellation_payload = {
                    "strategy_id": instance.id,
                    "symbol": instance.symbol,
                    "mode": runtime_mode.value,
                    "cancelled_pending": cancelled_pending,
                }
                self.repository.log_activity(
                    "VirtualEntriesCancelledOnPause",
                    (
                        f"Cancelled {cancelled_pending} pending virtual "
                        f"{'entry' if cancelled_pending == 1 else 'entries'} on pause"
                    ),
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                    payload=cancellation_payload,
                )
            return {
                **asdict(instance),
                "cancelled_pending": cancelled_pending,
            }

    def set_strategy_live_execution(
        self,
        strategy_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        with self._strategy_operation_lock:
            if self.active_mode() != RuntimeMode.LIVE:
                raise PermissionError("Live execution can only be changed in live mode")
            instance = self.repository.update_strategy_instance(
                strategy_id,
                {"live_execution_enabled": enabled},
            )
            errors = (
                validate_live_strategy_config(instance, self.settings)
                if enabled
                else []
            )
            self.repository.log_activity(
                "LiveExecutionEnabled" if enabled else "LiveExecutionDisabled",
                f"Live execution {'enabled' if enabled else 'disabled'} for {strategy_id}",
                level="warning" if enabled else "info",
                strategy_instance_id=instance.id,
                symbol=instance.symbol,
                payload={"blocked": bool(errors), "errors": errors},
            )
            return {**asdict(instance), "live_execution_errors": errors}

    async def reset_strategy(self, strategy_id: str) -> dict[str, Any]:
        if self.active_mode() == RuntimeMode.TEST:
            return self._reset_test_strategy(strategy_id)
        return await self._reset_live_strategy(strategy_id)

    def _reset_test_strategy(self, strategy_id: str) -> dict[str, Any]:
        with self._strategy_operation_lock:
            instance = self._require_paused_strategy(strategy_id)
            if self.active_mode() != RuntimeMode.TEST:
                raise StrategyResetBlocked("Runtime mode changed; try reset again")
            target_orders = [
                order
                for order in self.order_book.orders
                if order.strategy_instance_id == strategy_id
            ]
            invalid = [
                order
                for order in target_orders
                if order.status in {OrderStatus.OPENING, OrderStatus.CLOSING}
            ]
            if invalid:
                raise StrategyResetBlocked(
                    "Cannot reset: paper allocation has an in-flight state"
                )
            open_orders = [
                order
                for order in target_orders
                if order.status in {OrderStatus.FILLED, OrderStatus.OPEN}
            ]
            close_prices: dict[str, float] = {}
            for order in open_orders:
                quote = self.quote_book.get(order.symbol)
                close_price = None
                if quote is not None:
                    close_price = quote.bid if order.side == OrderSide.BUY else quote.ask
                if close_price is None:
                    raise StrategyResetBlocked(
                        f"Cannot reset: no current paper close quote for {order.symbol}"
                    )
                close_prices[order.id] = close_price

            strategy = self._strategy_for_instance(instance)
            if strategy is None:
                raise StrategyResetBlocked(
                    f"Cannot reset: strategy implementation is unavailable: "
                    f"{instance.strategy_name}"
                )
            previous_state = strategy.export_state(instance)
            candidate = PaperOrderBook(
                orders=deepcopy(self.order_book.orders),
                fills=deepcopy(self.order_book.fills),
            )
            cancelled_pending = candidate.cancel_pending(strategy_id)
            closed_at = datetime.now(UTC)
            cycle_ids: set[str] = set()
            for order in candidate.orders:
                if order.id not in close_prices:
                    continue
                order.status = OrderStatus.CLOSED
                order.closed_at = closed_at
                order.metadata["close_reason"] = "strategy_reset"
                order.metadata["close_price"] = close_prices[order.id]
                order.metadata["state_reset_terminal"] = True
                cycle_ids.add(order.cycle_id)
            try:
                strategy.reset_state(instance)
            except StrategyStateResetUnsupported as exc:
                raise StrategyResetBlocked(f"Cannot reset: {exc}") from exc
            reset_count = (
                self.repository.count_activity_events(
                    "StrategyStateReset",
                    strategy_instance_id=strategy_id,
                )
                + 1
            )
            summary = self._reset_summary(
                instance=instance,
                mode=RuntimeMode.TEST,
                cancelled_pending=cancelled_pending,
                reconciled_allocations=len(open_orders),
                completed_cycles=len(cycle_ids),
                reset_count=reset_count,
            )
            try:
                self.repository.sync_paper_state(
                    candidate.orders,
                    candidate.fills,
                    market_prices=self.current_market_prices(),
                )
                self.repository.delete_strategy_runtime_state(strategy_id)
                self.repository.log_activity(
                    "StrategyStateReset",
                    f"Strategy {strategy_id} local state reset",
                    strategy_instance_id=strategy_id,
                    symbol=instance.symbol,
                    payload=summary,
                )
            except Exception:
                strategy.import_state(instance, previous_state)
                raise
            self.order_book = candidate
            return summary

    async def _reset_live_strategy(self, strategy_id: str) -> dict[str, Any]:
        with self._strategy_operation_lock:
            instance = self._require_paused_strategy(strategy_id)
            if self.active_mode() != RuntimeMode.LIVE:
                raise StrategyResetBlocked("Runtime mode changed; try reset again")
            context = self._live_reset_context(instance)
            fingerprint = self._live_reset_fingerprint(instance)

        client = self.live_execution.client
        try:
            positions, open_orders, history = await asyncio.gather(
                client.positions(context["account_id"]),
                client.open_orders(context["account_id"]),
                client.order_history(context["account_id"]),
            )
        except WebullError as exc:
            raise StrategyResetBrokerError(
                f"Webull reconciliation reads failed: {exc.message}"
            ) from exc
        except Exception as exc:
            raise StrategyResetBrokerError(
                f"Webull reconciliation reads failed: {_safe_reset_error(exc)}"
            ) from exc
        if not all(
            isinstance(value, (list, dict))
            for value in (positions, open_orders, history)
        ):
            raise StrategyResetBrokerError(
                "Webull reconciliation reads returned an invalid response"
            )

        observed = _signed_reset_position(positions, instance.webull_symbol)
        expected_after = float(context["expected_after"])
        working_rows = _broker_order_rows(open_orders)
        for row in working_rows:
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol == instance.webull_symbol:
                raise StrategyResetBlocked(
                    "Cannot reset: a broker order is still working"
                )
        if abs(observed - expected_after) > 1e-9:
            raise StrategyResetBlocked(
                f"Cannot reset: Webull still reports {observed:+g} "
                f"{instance.webull_symbol}; expected {expected_after:+g}"
            )

        manual_close = None
        if context["allocation_quantity"] > 0:
            matched_history = _match_manual_close_orders(
                history,
                webull_symbol=instance.webull_symbol,
                close_side=context["close_side"],
                quantity=float(context["allocation_quantity"]),
                opened_after=context["opened_after"],
                known_client_order_ids=context["known_client_order_ids"],
            )
            details: list[dict[str, Any]] = []
            try:
                for row in matched_history:
                    client_order_id = str(row.get("client_order_id") or "")
                    if not client_order_id:
                        raise StrategyResetBlocked(
                            "Cannot reset: close fill has no client order ID for "
                            "Order Detail confirmation"
                        )
                    detail = await client.order_detail(
                        context["account_id"],
                        client_order_id,
                    )
                    detail_rows = [
                        item
                        for item in _broker_order_rows(detail)
                        if str(item.get("client_order_id") or "") == client_order_id
                    ]
                    if len(detail_rows) != 1:
                        raise StrategyResetBlocked(
                            "Cannot reset: close fill could not be matched unambiguously"
                        )
                    details.append(detail_rows[0])
            except StrategyResetBlocked:
                raise
            except WebullError as exc:
                raise StrategyResetBrokerError(
                    f"Webull Order Detail read failed: {exc.message}"
                ) from exc
            except Exception as exc:
                raise StrategyResetBrokerError(
                    f"Webull Order Detail read failed: {_safe_reset_error(exc)}"
                ) from exc
            manual_close = _confirmed_manual_close(
                details,
                webull_symbol=instance.webull_symbol,
                close_side=context["close_side"],
                quantity=float(context["allocation_quantity"]),
                opened_after=context["opened_after"],
                known_client_order_ids=context["known_client_order_ids"],
            )

        with self._strategy_operation_lock:
            current = self._require_paused_strategy(strategy_id)
            if (
                self.active_mode() != RuntimeMode.LIVE
                or self._live_reset_fingerprint(current) != fingerprint
            ):
                raise StrategyResetBlocked(
                    "Cannot reset: local state changed during Webull reads; try again"
                )
            strategy = self._strategy_for_instance(current)
            if strategy is None:
                raise StrategyResetBlocked(
                    f"Cannot reset: strategy implementation is unavailable: "
                    f"{current.strategy_name}"
                )
            previous_state = strategy.export_state(current)
            candidate = PaperOrderBook(
                orders=deepcopy(self.live_virtual_book.orders),
                fills=deepcopy(self.live_virtual_book.fills),
            )
            cancelled_pending = candidate.cancel_pending(strategy_id)
            target_active = [
                order
                for order in candidate.orders
                if order.strategy_instance_id == strategy_id
                and order.status in {OrderStatus.FILLED, OrderStatus.OPEN}
            ]
            cycle_ids = {order.cycle_id for order in target_active}
            if manual_close is not None:
                for order in target_active:
                    order.status = OrderStatus.CLOSED
                    order.closed_at = manual_close["filled_at"]
                    order.metadata.update(
                        {
                            "close_reason": "manual_broker_close",
                            "manual_reconciliation": True,
                            "broker_close_fill_confirmed": True,
                            "state_reset_terminal": True,
                            "close_price": manual_close["filled_price"],
                            "close_quantity": order.quantity,
                            "manual_close_filled_at": manual_close[
                                "filled_at"
                            ].isoformat(),
                            "manual_close_quantity": order.quantity,
                            "manual_close_fill_price": manual_close[
                                "filled_price"
                            ],
                            "manual_broker_order_ids": manual_close["order_ids"],
                            "manual_broker_client_order_ids": manual_close[
                                "client_order_ids"
                            ],
                        }
                    )
            try:
                strategy.reset_state(current)
            except StrategyStateResetUnsupported as exc:
                raise StrategyResetBlocked(f"Cannot reset: {exc}") from exc
            reset_count = (
                self.repository.count_activity_events(
                    "StrategyStateReset",
                    strategy_instance_id=strategy_id,
                )
                + 1
            )
            summary = self._reset_summary(
                instance=current,
                mode=RuntimeMode.LIVE,
                cancelled_pending=cancelled_pending,
                reconciled_allocations=len(target_active),
                completed_cycles=len(cycle_ids),
                reset_count=reset_count,
                broker_position_observed=observed,
                expected_broker_position=expected_after,
            )
            manual_payload = None
            if manual_close is not None:
                manual_payload = {
                    "strategy_id": strategy_id,
                    "symbol": current.symbol,
                    "webull_symbol": current.webull_symbol,
                    "quantity": manual_close["quantity"],
                    "fill_price": manual_close["filled_price"],
                    "filled_at": manual_close["filled_at"].isoformat(),
                    "broker_order_ids": manual_close["order_ids"],
                    "broker_client_order_ids": manual_close["client_order_ids"],
                    "reset_count": reset_count,
                }
            reconciliation = context["reconciliation"]
            try:
                self.repository.commit_live_strategy_reset(
                    orders=candidate.orders,
                    strategy_instance_id=strategy_id,
                    symbol=current.symbol,
                    account_alias=current.account_alias,
                    webull_symbol=current.webull_symbol,
                    external_baseline_quantity=float(
                        reconciliation["external_baseline_quantity"]
                    ),
                    observed_position=observed,
                    expected_position=expected_after,
                    activity_payload=summary,
                    manual_close_payload=manual_payload,
                )
            except Exception:
                strategy.import_state(current, previous_state)
                raise
            self.live_virtual_book = candidate
            return summary

    def _require_paused_strategy(self, strategy_id: str):
        instances = {
            instance.id: instance
            for instance in self.repository.list_strategy_instances()
        }
        if strategy_id not in instances:
            raise KeyError(strategy_id)
        instance = instances[strategy_id]
        if instance.enabled:
            raise StrategyResetBlocked("Cannot reset: pause the strategy first")
        return instance

    def _live_reset_context(self, instance) -> dict[str, Any]:
        if not instance.account_alias or not instance.webull_symbol:
            raise StrategyResetBlocked(
                "Cannot reset: live account alias and Webull symbol are required"
            )
        account_id = self.settings.resolve_webull_account_alias(instance.account_alias)
        if not account_id:
            raise StrategyResetBlocked(
                f"Cannot reset: account alias is not configured: {instance.account_alias}"
            )
        if self.repository.has_unresolved_live_intent(instance.id):
            intent = next(
                item
                for item in self.repository.list_live_order_intents(
                    strategy_instance_id=instance.id
                )
                if item.status
                in {
                    LiveIntentStatus.PENDING_SUBMIT,
                    LiveIntentStatus.SUBMITTED,
                    LiveIntentStatus.ACCEPTED,
                    LiveIntentStatus.PARTIAL_FILLED,
                    LiveIntentStatus.UNKNOWN,
                    LiveIntentStatus.DESYNCED,
                }
            )
            raise StrategyResetBlocked(
                f"Cannot reset: live intent is still {intent.status.value.lower()}"
            )
        same_symbol_instances = [
            item
            for item in self.repository.list_strategy_instances()
            if item.account_alias == instance.account_alias
            and item.webull_symbol == instance.webull_symbol
        ]
        same_symbol_ids = {item.id for item in same_symbol_instances}
        configured_ids = {
            item.id for item in self.repository.list_strategy_instances()
        }
        if any(
            order.strategy_instance_id not in configured_ids
            and order.symbol == instance.symbol
            and order.status
            in {
                OrderStatus.PENDING,
                OrderStatus.OPENING,
                OrderStatus.FILLED,
                OrderStatus.OPEN,
                OrderStatus.CLOSING,
            }
            for order in self.live_virtual_book.orders
        ):
            raise StrategyResetBlocked(
                "Cannot reset: an active same-symbol allocation has no configured owner"
            )
        for intent in self.repository.list_live_order_intents():
            if (
                intent.strategy_instance_id in same_symbol_ids
                and intent.status
                in {
                    LiveIntentStatus.PENDING_SUBMIT,
                    LiveIntentStatus.SUBMITTED,
                    LiveIntentStatus.ACCEPTED,
                    LiveIntentStatus.PARTIAL_FILLED,
                    LiveIntentStatus.UNKNOWN,
                    LiveIntentStatus.DESYNCED,
                }
            ):
                raise StrategyResetBlocked(
                    "Cannot reset: same-account/same-symbol broker activity "
                    f"is still {intent.status.value.lower()}"
                )
        active_statuses = {
            OrderStatus.PENDING,
            OrderStatus.OPENING,
            OrderStatus.FILLED,
            OrderStatus.OPEN,
            OrderStatus.CLOSING,
        }
        target_active = [
            order
            for order in self.live_virtual_book.orders
            if order.strategy_instance_id == instance.id
            and order.status in active_statuses
        ]
        if any(
            order.status in {OrderStatus.OPENING, OrderStatus.CLOSING}
            for order in target_active
        ):
            raise StrategyResetBlocked(
                "Cannot reset: a live allocation is still opening or closing"
            )
        allocations = [
            order
            for order in target_active
            if order.status in {OrderStatus.FILLED, OrderStatus.OPEN}
        ]
        if allocations:
            sides = {order.side for order in allocations}
            if len(sides) != 1:
                raise StrategyResetBlocked(
                    "Cannot reset: target allocations have mixed sides"
                )
            for order in allocations:
                if (
                    not order.metadata.get("broker_fill_confirmed")
                    or order.fill_price is None
                    or order.opened_at is None
                ):
                    raise StrategyResetBlocked(
                        "Cannot reset: target opening fill is not fully confirmed"
                    )
        for order in self.live_virtual_book.orders:
            if (
                order.strategy_instance_id in same_symbol_ids
                and order.strategy_instance_id != instance.id
                and order.status in {OrderStatus.OPENING, OrderStatus.CLOSING}
            ):
                raise StrategyResetBlocked(
                    "Cannot reset: same-account/same-symbol allocation is in flight"
                )
            if (
                order.strategy_instance_id in same_symbol_ids
                and order.strategy_instance_id != instance.id
                and order.status in {OrderStatus.FILLED, OrderStatus.OPEN}
                and not order.metadata.get("broker_fill_confirmed")
            ):
                raise StrategyResetBlocked(
                    "Cannot reset: another same-account/same-symbol allocation "
                    "does not have a confirmed broker fill"
                )
        reconciliation = self.repository.get_live_symbol_reconciliation(
            instance.account_alias,
            instance.webull_symbol,
        )
        if reconciliation is None:
            raise StrategyResetBlocked(
                "Cannot reset: no established external position baseline"
            )
        other_quantity = _signed_reset_allocations(
            self.live_virtual_book.orders,
            same_symbol_ids - {instance.id},
        )
        expected_after = (
            float(reconciliation["external_baseline_quantity"]) + other_quantity
        )
        quantity = sum(order.quantity for order in allocations)
        side = allocations[0].side if allocations else OrderSide.BUY
        opened_after = (
            max(order.opened_at for order in allocations if order.opened_at is not None)
            if allocations
            else None
        )
        return {
            "account_id": account_id,
            "allocation_quantity": quantity,
            "close_side": _opposite_side(side),
            "opened_after": opened_after,
            "expected_after": expected_after,
            "reconciliation": reconciliation,
            "known_client_order_ids": {
                intent.client_order_id
                for intent in self.repository.list_live_order_intents()
                if intent.account_alias == instance.account_alias
            },
        }

    def _live_reset_fingerprint(self, instance) -> str:
        same_symbol_ids = {
            item.id
            for item in self.repository.list_strategy_instances()
            if item.account_alias == instance.account_alias
            and item.webull_symbol == instance.webull_symbol
        }
        orders = [
            asdict(order)
            for order in self.live_virtual_book.orders
            if order.strategy_instance_id in same_symbol_ids
        ]
        intents = [
            asdict(intent)
            for intent in self.repository.list_live_order_intents()
            if intent.strategy_instance_id in same_symbol_ids
        ]
        reconciliation = self.repository.get_live_symbol_reconciliation(
            instance.account_alias,
            instance.webull_symbol,
        )
        return repr(
            (
                self.active_mode().value,
                asdict(instance),
                sorted(orders, key=lambda item: item["id"]),
                sorted(intents, key=lambda item: item["id"]),
                reconciliation,
            )
        )

    @staticmethod
    def _reset_summary(
        *,
        instance,
        mode: RuntimeMode,
        cancelled_pending: int,
        reconciled_allocations: int,
        completed_cycles: int,
        reset_count: int,
        broker_position_observed: float | None = None,
        expected_broker_position: float | None = None,
    ) -> dict[str, Any]:
        return {
            "strategy_id": instance.id,
            "mode": mode.value,
            "enabled": False,
            "reset": True,
            "completed": True,
            "cancelled_pending": cancelled_pending,
            "reconciled_allocations": reconciled_allocations,
            "completed_cycles": completed_cycles,
            "strategy_state_cleared": True,
            "broker_position_observed": broker_position_observed,
            "expected_broker_position": expected_broker_position,
            "reset_count": reset_count,
            "warnings": [],
            "blocking_reason": None,
        }

    def live_reconciliation_snapshot(self) -> dict[str, Any]:
        intents: list[dict[str, Any]] = []
        for item in self.repository.list_live_order_intents():
            payload = asdict(item)
            if payload.get("account_id"):
                payload["account_id"] = "<redacted>"
            intents.append(payload)
        return {
            "mode": self.active_mode().value,
            "master_enabled": self.settings.live_execution_master_enable,
            "intents": intents,
            "events": self.repository.list_table("live_reconciliation_events"),
            "symbols": self.repository.list_table("live_symbol_reconciliation"),
        }

    def flatten_strategy(self, strategy_id: str) -> dict[str, Any]:
        if self.active_mode() == RuntimeMode.LIVE:
            raise PermissionError("Flatten is paper-only; live flatten is not available")
        instance = self.repository.update_strategy_instance(strategy_id, {"enabled": False})
        result = self.order_book.flatten_instance(
            strategy_id,
            quotes=self.current_quotes(),
        )
        self.repository.sync_paper_state(
            self.order_book.orders,
            self.order_book.fills,
            market_prices=self.current_market_prices(),
        )
        summary = {
            "strategy_id": strategy_id,
            "global_pause": self.risk.global_pause,
            "paused_strategy_count": 1,
            "cancelled_pending": result.cancelled_pending,
            "closed_positions": result.closed_positions,
            "warnings": result.warnings,
        }
        self.repository.log_activity(
            "FlattenInstance",
            self._flatten_message(summary),
            level="warning" if result.warnings else "info",
            strategy_instance_id=instance.id,
            symbol=instance.symbol,
            payload=summary,
        )
        return summary

    def flatten_all_strategies(self) -> dict[str, Any]:
        if self.active_mode() == RuntimeMode.LIVE:
            raise PermissionError("Flatten is paper-only; live flatten is not available")
        self.set_global_pause(True)
        instances = self.repository.list_strategy_instances()
        quotes = self.current_quotes()
        cancelled_pending = 0
        closed_positions = 0
        warnings: list[str] = []
        for instance in instances:
            result = self.order_book.flatten_instance(instance.id, quotes=quotes)
            cancelled_pending += result.cancelled_pending
            closed_positions += result.closed_positions
            warnings.extend(result.warnings)
        self.repository.sync_paper_state(
            self.order_book.orders,
            self.order_book.fills,
            market_prices=self.current_market_prices(),
        )
        summary = {
            "strategy_id": None,
            "global_pause": self.risk.global_pause,
            "paused_strategy_count": len(instances),
            "cancelled_pending": cancelled_pending,
            "closed_positions": closed_positions,
            "warnings": warnings,
        }
        self.repository.log_activity(
            "FlattenGlobal",
            self._flatten_message(summary),
            level="warning" if warnings else "info",
            payload=summary,
        )
        return summary

    def _flatten_message(self, summary: dict[str, Any]) -> str:
        message = (
            f"Flattened paper state: cancelled {summary['cancelled_pending']} pending orders, "
            f"closed {summary['closed_positions']} positions"
        )
        if summary["warnings"]:
            message += f"; {len(summary['warnings'])} warnings"
        return message

    def ingest_quote_item(
        self,
        item: dict[str, Any],
        *,
        received_at: datetime | None = None,
    ) -> list[str]:
        quote = self.quote_book.merge_quote_item(item, received_at=received_at)
        self.record_stream_message(quote.symbol, "quote", item)
        return self.evaluate_quote(quote)

    def ingest_market_message(
        self,
        message: str,
        *,
        received_at: datetime | None = None,
    ) -> list[str]:
        parsed = parse_market_message(message)
        if parsed.kind == "quote" and isinstance(parsed.payload, dict):
            messages: list[str] = []
            if "data" not in parsed.payload:
                return self.ingest_quote_item(parsed.payload, received_at=received_at)
            parent_last_update = parsed.payload.get("last_update")
            for item in parsed.payload.get("data", []):
                if isinstance(item, dict):
                    quote_item = dict(item)
                    if parent_last_update is not None and quote_item.get("last_update") is None:
                        quote_item["last_update"] = parent_last_update
                    messages.extend(self.ingest_quote_item(quote_item, received_at=received_at))
            return messages
        if parsed.kind == "series" and isinstance(parsed.payload, dict):
            symbol = str(parsed.payload.get("code") or "")
            self.record_stream_message(symbol, "series", parsed.payload)
            return self.ingest_series_payload(parsed.payload)
        return []

    def ingest_series_payload(self, payload: dict[str, Any]) -> list[str]:
        with self._strategy_operation_lock:
            return self._ingest_series_payload(payload)

    def _ingest_series_payload(self, payload: dict[str, Any]) -> list[str]:
        bars = parse_series_bars(payload)
        if not bars:
            return []
        symbol = bars[0].symbol
        messages: list[str] = []
        book = (
            self.live_virtual_book
            if self.active_mode() == RuntimeMode.LIVE
            else self.order_book
        )
        for instance in self.repository.list_strategy_instances():
            if not instance.enabled or instance.symbol != symbol:
                continue
            strategy = self._strategy_for_instance(instance)
            if strategy is None:
                continue
            produced = strategy.on_series(instance, bars, book)
            messages.extend(self._record_strategy_messages(instance, produced))
            self._persist_strategy_state(instance, strategy)
        if self.active_mode() == RuntimeMode.LIVE:
            self.repository.sync_live_virtual_state(self.live_virtual_book.orders)
        return messages

    def record_stream_message(self, symbol: str, message_type: str, raw: dict[str, Any]) -> None:
        if not symbol:
            return
        enabled_instances = [
            instance
            for instance in self.repository.list_strategy_instances()
            if instance.enabled and instance.symbol == symbol
        ]
        if enabled_instances:
            self.stream_buffer.append_symbol(
                symbol=symbol,
                message_type=message_type,
                raw=raw,
            )
        for instance in enabled_instances:
            self.stream_buffer.append(
                strategy_instance_id=instance.id,
                symbol=symbol,
                message_type=message_type,
                raw=raw,
            )

    def evaluate_quote(self, quote: QuoteState) -> list[str]:
        with self._strategy_operation_lock:
            return self._evaluate_quote(quote)

    def _evaluate_quote(self, quote: QuoteState) -> list[str]:
        validation = validate_quote(
            quote,
            max_staleness_seconds=self.settings.quote_max_staleness_seconds,
            allow_delayed=self.settings.allow_delayed_quotes,
        )
        if not validation.ok:
            self.repository.log_activity(
                "QuoteRejected",
                validation.reason,
                level="warning",
                symbol=quote.symbol,
            )
            return [validation.reason]
        if self.active_mode() == RuntimeMode.LIVE:
            return self._evaluate_live_quote(quote)
        messages: list[str] = []
        for instance in self.repository.list_strategy_instances():
            if not instance.enabled or instance.symbol != quote.symbol:
                continue
            decision = self.risk.allow_instance(instance.id)
            if not decision.allowed:
                messages.append(decision.reason)
                continue
            strategy = self._strategy_for_instance(instance)
            if strategy is None:
                message = f"Unknown strategy: {instance.strategy_name}"
                self.repository.log_activity(
                    "StrategySkipped",
                    message,
                    level="warning",
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                )
                messages.append(message)
                continue
            for message in strategy.on_quote(instance, quote, self.order_book):
                self.repository.log_activity(
                    message,
                    message,
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                )
                messages.append(message)
            self._persist_strategy_state(instance, strategy)
        self.repository.sync_paper_state(
            self.order_book.orders,
            self.order_book.fills,
            market_prices=self.current_market_prices(),
        )
        return messages

    def evaluate_live_quote(self, quote: QuoteState) -> list[str]:
        with self._strategy_operation_lock:
            return self._evaluate_live_quote(quote)

    def _evaluate_live_quote(self, quote: QuoteState) -> list[str]:
        self._hydrate_live_intent_outcomes()
        messages: list[str] = []
        for instance in self.repository.list_strategy_instances():
            if not instance.enabled or instance.symbol != quote.symbol:
                continue
            decision = self.risk.allow_instance(instance.id)
            if not decision.allowed:
                messages.append(decision.reason)
                continue
            if not instance.live_execution_enabled:
                continue
            errors = validate_live_strategy_config(instance, self.settings)
            if errors:
                message = "; ".join(errors)
                self.repository.log_activity(
                    "LiveExecutionBlocked",
                    message,
                    level="warning",
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                )
                messages.append(message)
                continue
            strategy = self._strategy_for_instance(instance)
            if strategy is None:
                message = f"Unknown strategy: {instance.strategy_name}"
                self.repository.log_activity(
                    "StrategySkipped",
                    message,
                    level="warning",
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                )
                messages.append(message)
                continue
            before = {
                order.id: (order.status, dict(order.metadata))
                for order in self.live_virtual_book.orders
            }
            for message in strategy.on_quote(instance, quote, self.live_virtual_book):
                self.repository.log_activity(
                    message,
                    message,
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                )
                messages.append(message)
            self._persist_strategy_state(instance, strategy)
            self.repository.sync_live_virtual_state(self.live_virtual_book.orders)
            self._process_live_order_transitions(instance, before)
        return messages

    def _hydrate_live_intent_outcomes(self) -> None:
        intents = {
            intent.id: intent
            for intent in self.repository.list_live_order_intents()
        }
        changed = False
        for order in self.live_virtual_book.orders:
            intent_id = str(order.metadata.get("live_intent_id") or "")
            intent = intents.get(intent_id)
            if intent is None or intent.status != LiveIntentStatus.FILLED:
                continue
            if order.status == OrderStatus.OPENING:
                fill_price = _intent_fill_price(intent.response)
                if fill_price is None:
                    continue
                order.status = OrderStatus.OPEN
                order.fill_price = fill_price
                stop_distance = float(
                    order.metadata.get("stop_loss_distance_price") or 0
                )
                take_distance = float(
                    order.metadata.get("take_profit_distance_price") or 0
                )
                if stop_distance > 0:
                    order.stop_loss = (
                        fill_price - stop_distance
                        if order.side == OrderSide.BUY
                        else fill_price + stop_distance
                    )
                if take_distance > 0:
                    order.take_profit = (
                        fill_price + take_distance
                        if order.side == OrderSide.BUY
                        else fill_price - take_distance
                    )
                order.metadata["broker_fill_confirmed"] = True
                order.metadata["risk_from_fill"] = True
                order.metadata["live_status"] = LiveIntentStatus.FILLED.value
                changed = True
            elif order.status == OrderStatus.CLOSING:
                order.status = OrderStatus.CLOSED
                order.metadata["broker_close_fill_confirmed"] = True
                order.metadata["live_status"] = LiveIntentStatus.FILLED.value
                changed = True
        if changed:
            self.repository.sync_live_virtual_state(self.live_virtual_book.orders)

    def ingest_live_account_state(self, account: LiveAccountState) -> list[str]:
        with self._strategy_operation_lock:
            return self._ingest_live_account_state(account)

    def _ingest_live_account_state(self, account: LiveAccountState) -> list[str]:
        messages: list[str] = []
        for instance in self.repository.list_strategy_instances():
            if (
                not instance.enabled
                or not instance.live_execution_enabled
                or instance.account_alias != account.account_alias
                or (
                    account.strategy_instance_id
                    and instance.id != account.strategy_instance_id
                )
            ):
                continue
            strategy = self._strategy_for_instance(instance)
            if strategy is None:
                continue
            produced = strategy.on_account_snapshot(
                instance,
                account,
                self.live_virtual_book,
            )
            messages.extend(self._record_strategy_messages(instance, produced))
            self._persist_strategy_state(instance, strategy)
        self.repository.sync_live_virtual_state(self.live_virtual_book.orders)
        return messages

    def evaluate_timers(self, *, now: datetime) -> list[str]:
        with self._strategy_operation_lock:
            return self._evaluate_timers(now=now)

    def _evaluate_timers(self, *, now: datetime) -> list[str]:
        messages: list[str] = []
        live = self.active_mode() == RuntimeMode.LIVE
        book = self.live_virtual_book if live else self.order_book
        for instance in self.repository.list_strategy_instances():
            if not instance.enabled:
                continue
            if live and not instance.live_execution_enabled:
                continue
            strategy = self._strategy_for_instance(instance)
            if strategy is None:
                continue
            before = {
                order.id: (order.status, dict(order.metadata))
                for order in book.orders
            }
            quote = self.quote_book.get(instance.symbol)
            produced = strategy.on_timer(instance, now, quote, book)
            messages.extend(self._record_strategy_messages(instance, produced))
            self._persist_strategy_state(instance, strategy)
            if live:
                self._process_live_order_transitions(instance, before)
        if live:
            self.repository.sync_live_virtual_state(book.orders)
        else:
            self.repository.sync_paper_state(
                book.orders,
                book.fills,
                market_prices=self.current_market_prices(),
            )
        return messages

    def market_subscription_requirements(self) -> list[dict[str, Any]]:
        requirements: dict[str, dict[str, Any]] = {}
        for instance in self.repository.list_strategy_instances():
            if not instance.enabled:
                continue
            strategy = self._strategy_for_instance(instance)
            items = (
                strategy.subscription_requirements(instance)
                if strategy is not None
                else [{"code": instance.symbol, "type": "quote"}]
            )
            for item in items:
                normalized = dict(item)
                key = repr(sorted(normalized.items()))
                requirements[key] = normalized
        return [requirements[key] for key in sorted(requirements)]

    def _process_live_order_transitions(
        self,
        instance,
        before: dict[str, tuple[OrderStatus, dict[str, Any]]],
    ) -> None:
        openings: list[PaperOrder] = []
        closing_groups: dict[tuple[str, OrderSide], list[PaperOrder]] = {}
        for order in self.live_virtual_book.orders:
            if order.strategy_instance_id != instance.id:
                continue
            previous = before.get(order.id)
            previous_status = previous[0] if previous is not None else None
            if order.status == OrderStatus.FILLED and previous_status in {
                None,
                OrderStatus.PENDING,
            }:
                openings.append(order)
            elif order.status == OrderStatus.CLOSED and previous_status in {
                OrderStatus.FILLED,
                OrderStatus.OPEN,
            }:
                key = (order.cycle_id, _opposite_side(order.side))
                closing_groups.setdefault(key, []).append(order)
        for order in openings:
            order.status = OrderStatus.OPENING
            self._submit_live_virtual_orders(
                instance=instance,
                orders=[order],
                action=LiveIntentAction.OPEN_MARKET,
                side=order.side,
            )
        for (_cycle_id, side), orders in closing_groups.items():
            for order in orders:
                order.status = OrderStatus.CLOSING
            reasons = {
                str(order.metadata.get("close_reason") or "strategy")
                for order in orders
            }
            action = (
                LiveIntentAction.FLATTEN_MARKET
                if reasons & {"session_close", "contract_force_exit", "daily_loss"}
                else LiveIntentAction.CLOSE_MARKET
            )
            self._submit_live_virtual_orders(
                instance=instance,
                orders=orders,
                action=action,
                side=side,
            )
        self.repository.sync_live_virtual_state(self.live_virtual_book.orders)

    def _submit_live_virtual_orders(
        self,
        *,
        instance,
        orders: list[PaperOrder],
        action: LiveIntentAction,
        side: OrderSide,
    ) -> None:
        if not orders:
            return
        execution_key = ":".join(sorted(order.id for order in orders))
        request = LiveMarketOrderRequest(
            strategy=instance,
            cycle_id=orders[0].cycle_id,
            action=action,
            side=side,
            quantity=sum(order.quantity for order in orders),
            execution_key=execution_key,
            virtual_order_ids=[order.id for order in orders],
        )

        async def submit() -> None:
            try:
                intent = await self.live_execution.submit_market_order(
                    request,
                    runtime_mode=self.active_mode(),
                    global_pause=self.risk.global_pause,
                )
            except LiveExecutionBlocked as exc:
                self._handle_live_submission_problem(
                    instance,
                    orders,
                    action,
                    str(exc),
                    "BLOCKED",
                )
                return
            for order in orders:
                order.metadata["live_intent_id"] = intent.id
                order.metadata["client_order_id"] = intent.client_order_id
                order.metadata["execution_key"] = execution_key
                order.metadata["live_status"] = intent.status.value
            self.repository.sync_live_virtual_state(self.live_virtual_book.orders)
            if intent.status in {
                LiveIntentStatus.REJECTED,
                LiveIntentStatus.UNKNOWN,
                LiveIntentStatus.DESYNCED,
            }:
                self._handle_live_submission_problem(
                    instance,
                    orders,
                    action,
                    intent.error_message or f"live order status {intent.status.value}",
                    intent.status.value,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(submit())
        else:
            loop.create_task(submit())

    def _handle_live_submission_problem(
        self,
        instance,
        orders: list[PaperOrder],
        action: LiveIntentAction,
        message: str,
        status: str,
    ) -> None:
        for order in orders:
            if action == LiveIntentAction.OPEN_MARKET:
                order.status = OrderStatus.ERROR
            else:
                order.status = OrderStatus.OPEN
                order.closed_at = None
            order.metadata["live_status"] = status
            order.metadata["live_error"] = message
        self.repository.sync_live_virtual_state(self.live_virtual_book.orders)
        self.repository.update_strategy_instance(instance.id, {"enabled": False})
        self.repository.log_activity(
            "LiveExecutionPaused",
            f"Strategy paused after live order problem: {message}",
            level="warning",
            strategy_instance_id=instance.id,
            symbol=instance.symbol,
        )

    def resolve_strategy(self, strategy_name: str) -> Strategy | None:
        strategy = self.strategies.get(strategy_name)
        if strategy is not None:
            return strategy
        module_name = f"webull_auto_trading.strategy.{strategy_name}"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return None
        strategy_type = getattr(module, _strategy_class_name(strategy_name), None)
        if strategy_type is None:
            return None
        try:
            strategy = strategy_type()
        except Exception:
            return None
        if not isinstance(strategy, Strategy):
            return None
        self.strategies[strategy_name] = strategy
        return strategy

    def _strategy_for_instance(self, instance) -> Strategy | None:
        strategy = self.resolve_strategy(instance.strategy_name)
        if strategy is None:
            return None
        if instance.id not in self._restored_strategy_instances:
            strategy.import_state(
                instance,
                self.repository.get_strategy_runtime_state(instance.id),
            )
            self._restored_strategy_instances.add(instance.id)
        return strategy

    def _persist_strategy_state(self, instance, strategy: Strategy) -> None:
        state = strategy.export_state(instance)
        if state:
            self.repository.upsert_strategy_runtime_state(instance.id, state)

    def _record_strategy_messages(self, instance, produced: list[str]) -> list[str]:
        for message in produced:
            self.repository.log_activity(
                message,
                message,
                strategy_instance_id=instance.id,
                symbol=instance.symbol,
            )
        return list(produced)

    def current_market_prices(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        for quote in self.quote_book.all():
            price = quote.last_price or quote.mid_price or quote.bid or quote.ask
            if price is not None:
                prices[quote.symbol] = price
        return prices

    def current_quotes(self) -> dict[str, QuoteState]:
        return {quote.symbol: quote for quote in self.quote_book.all()}

    def enabled_symbol_streams(self) -> list[dict[str, Any]]:
        symbols: dict[str, list[str]] = {}
        for instance in self.repository.list_strategy_instances():
            if instance.enabled:
                symbols.setdefault(instance.symbol, []).append(instance.id)
        return [
            {
                "symbol": symbol,
                "strategy_ids": strategy_ids,
                "strategy_count": len(strategy_ids),
            }
            for symbol, strategy_ids in sorted(symbols.items())
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "health": self.health(),
            "strategies": [asdict(item) for item in self.repository.list_strategy_instances()],
            "quotes": [asdict(item) for item in self.quote_book.all()],
            "orders": [asdict(item) for item in self.order_book.orders],
            "fills": [asdict(item) for item in self.order_book.fills],
        }


def _opposite_side(side: OrderSide) -> OrderSide:
    return OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY


def _strategy_class_name(strategy_name: str) -> str:
    return "".join(part.capitalize() for part in strategy_name.split("_")) + "Strategy"


def _intent_fill_price(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("filled_price", "avg_filled_price", "average_price"):
            raw = value.get(key)
            if raw not in (None, ""):
                try:
                    price = float(raw)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    return price
        for nested in value.values():
            price = _intent_fill_price(nested)
            if price is not None:
                return price
    elif isinstance(value, list):
        for nested in value:
            price = _intent_fill_price(nested)
            if price is not None:
                return price
    return None


def _signed_reset_allocations(
    orders: list[PaperOrder],
    strategy_ids: set[str],
) -> float:
    quantity = 0.0
    for order in orders:
        if order.strategy_instance_id not in strategy_ids:
            continue
        direction = 1.0 if order.side == OrderSide.BUY else -1.0
        if order.status in {OrderStatus.FILLED, OrderStatus.OPEN}:
            allocated = order.quantity
        elif order.status == OrderStatus.CLOSING:
            allocated = max(
                order.quantity
                - _reset_float(order.metadata.get("close_filled_quantity")),
                0.0,
            )
        elif order.status == OrderStatus.OPENING:
            allocated = _reset_float(order.metadata.get("broker_filled_quantity"))
        else:
            allocated = 0.0
        quantity += direction * allocated
    return quantity


def _signed_reset_position(value: Any, webull_symbol: str) -> float:
    total = 0.0
    for position in _broker_position_rows(value):
        symbol = str(position.get("symbol") or "")
        ticker = position.get("ticker")
        if not symbol and isinstance(ticker, dict):
            symbol = str(ticker.get("symbol") or "")
        if symbol != webull_symbol:
            continue
        quantity = _reset_float(position.get("quantity"))
        side = str(
            position.get("side") or position.get("position_side") or ""
        ).upper()
        if side in {"SHORT", "SELL"}:
            quantity = -abs(quantity)
        total += quantity
    return total


def _broker_position_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("positions", "data", "items"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return [value] if "symbol" in value else []


def _broker_order_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for nested in item:
                visit(nested)
            return
        if not isinstance(item, dict):
            return
        nested_orders = item.get("orders")
        if isinstance(nested_orders, (list, dict)):
            visit(nested_orders)
            return
        for key in ("data", "items"):
            nested = item.get(key)
            if isinstance(nested, (list, dict)):
                visit(nested)
                return
        if any(
            key in item
            for key in ("symbol", "client_order_id", "order_id", "status")
        ):
            rows.append(item)

    visit(value)
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("client_order_id") or ""),
            str(row.get("order_id") or ""),
            str(row.get("symbol") or ""),
            str(row.get("filled_time_at") or row.get("filled_time") or ""),
        )
        unique[key] = row
    return list(unique.values())


def _match_manual_close_orders(
    history: Any,
    *,
    webull_symbol: str,
    close_side: OrderSide,
    quantity: float,
    opened_after: datetime | None,
    known_client_order_ids: set[str],
) -> list[dict[str, Any]]:
    candidates = _manual_close_candidates(
        history,
        webull_symbol=webull_symbol,
        close_side=close_side,
        opened_after=opened_after,
        known_client_order_ids=known_client_order_ids,
    )
    if len(candidates) > 16:
        raise StrategyResetBlocked(
            "Cannot reset: close fill could not be matched unambiguously"
        )
    matches: list[tuple[dict[str, Any], ...]] = []
    for size in range(1, len(candidates) + 1):
        for subset in combinations(candidates, size):
            if abs(sum(_filled_quantity(row) for row in subset) - quantity) <= 1e-9:
                matches.append(subset)
                if len(matches) > 1:
                    raise StrategyResetBlocked(
                        "Cannot reset: close fill could not be matched unambiguously"
                    )
    if len(matches) != 1:
        raise StrategyResetBlocked(
            "Cannot reset: close fill could not be matched unambiguously"
        )
    return list(matches[0])


def _confirmed_manual_close(
    details: list[dict[str, Any]],
    *,
    webull_symbol: str,
    close_side: OrderSide,
    quantity: float,
    opened_after: datetime | None,
    known_client_order_ids: set[str],
) -> dict[str, Any]:
    candidates = _manual_close_candidates(
        details,
        webull_symbol=webull_symbol,
        close_side=close_side,
        opened_after=opened_after,
        known_client_order_ids=known_client_order_ids,
    )
    if len(candidates) != len(details):
        raise StrategyResetBlocked(
            "Cannot reset: close fill could not be matched unambiguously"
        )
    total = sum(_filled_quantity(row) for row in candidates)
    if abs(total - quantity) > 1e-9 or total <= 0:
        raise StrategyResetBlocked(
            "Cannot reset: close fill quantity does not match the local allocation"
        )
    filled_at_values = [_broker_fill_time(row) for row in candidates]
    if any(value is None for value in filled_at_values):
        raise StrategyResetBlocked(
            "Cannot reset: close fill timestamp is unavailable"
        )
    weighted_price = sum(
        _filled_quantity(row) * _filled_price(row)
        for row in candidates
    ) / total
    return {
        "quantity": total,
        "filled_price": weighted_price,
        "filled_at": max(value for value in filled_at_values if value is not None),
        "order_ids": sorted(
            {
                str(row.get("order_id"))
                for row in candidates
                if row.get("order_id")
            }
        ),
        "client_order_ids": sorted(
            {
                str(row.get("client_order_id"))
                for row in candidates
                if row.get("client_order_id")
            }
        ),
    }


def _manual_close_candidates(
    value: Any,
    *,
    webull_symbol: str,
    close_side: OrderSide,
    opened_after: datetime | None,
    known_client_order_ids: set[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in _broker_order_rows(value):
        client_order_id = str(row.get("client_order_id") or "")
        filled_at = _broker_fill_time(row)
        if (
            str(row.get("symbol") or "") != webull_symbol
            or str(row.get("side") or "").upper() != close_side.value
            or str(row.get("status") or "").upper() != "FILLED"
            or client_order_id in known_client_order_ids
            or client_order_id.startswith("wat")
            or _filled_quantity(row) <= 0
            or _filled_price(row) <= 0
            or filled_at is None
            or (opened_after is not None and filled_at < opened_after)
        ):
            continue
        candidates.append(row)
    return candidates


def _filled_quantity(row: dict[str, Any]) -> float:
    return _reset_float(
        row.get("filled_quantity")
        or row.get("filled_qty")
        or row.get("quantity")
        or row.get("total_quantity")
    )


def _filled_price(row: dict[str, Any]) -> float:
    return _reset_float(
        row.get("filled_price")
        or row.get("avg_filled_price")
        or row.get("average_price")
    )


def _broker_fill_time(row: dict[str, Any]) -> datetime | None:
    value = row.get("filled_time_at") or row.get("filled_time")
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.isdigit()
    ):
        try:
            raw = float(value)
            seconds = raw / 1000.0 if raw > 10_000_000_000 else raw
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _reset_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_reset_error(exc: Exception) -> str:
    return (str(exc).strip() or type(exc).__name__)[:500]
