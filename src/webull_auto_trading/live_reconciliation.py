from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from webull_auto_trading.domain import (
    LiveAccountState,
    LiveIntentAction,
    LiveIntentStatus,
    LiveOrderIntent,
    OrderSide,
    OrderStatus,
    RuntimeMode,
    StrategyInstance,
    utc_now,
)
from webull_auto_trading.live_execution import (
    LiveOrderClient,
    build_market_order_request,
    stable_client_order_id,
)
from webull_auto_trading.runtime import RuntimeService
from webull_auto_trading.webull import WebullError

POLLABLE_STATUSES = {
    LiveIntentStatus.PENDING_SUBMIT,
    LiveIntentStatus.SUBMITTED,
    LiveIntentStatus.ACCEPTED,
    LiveIntentStatus.PARTIAL_FILLED,
}


class LiveRuntimeCoordinator:
    def __init__(
        self,
        runtime: RuntimeService,
        *,
        client: LiveOrderClient | None = None,
        tick_seconds: float = 1.0,
        account_poll_seconds: float = 5.0,
    ) -> None:
        self.runtime = runtime
        self.client = client or runtime.live_execution.client
        self.tick_seconds = tick_seconds
        self.account_poll_seconds = account_poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._preview_ready: dict[str, bool] = {}
        self._last_account_poll = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self.run(), name="live-runtime-coordinator")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run(self) -> None:
        while not self._stop.is_set():
            now = utc_now()
            try:
                self.runtime.evaluate_timers(now=now)
                if self.runtime.active_mode() == RuntimeMode.LIVE:
                    await self.reconcile_one_order()
                    if (
                        self._last_account_poll is None
                        or now - self._last_account_poll
                        >= timedelta(seconds=self.account_poll_seconds)
                    ):
                        await self.refresh_accounts()
                        self._last_account_poll = now
            except Exception as exc:
                self.runtime.repository.log_activity(
                    "LiveCoordinatorError",
                    _safe_error(exc),
                    level="warning",
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except TimeoutError:
                pass

    async def reconcile_one_order(self) -> LiveOrderIntent | None:
        now = utc_now()
        intents = [
            intent
            for intent in reversed(self.runtime.repository.list_live_order_intents())
            if intent.status in POLLABLE_STATUSES
            and (
                intent.status != LiveIntentStatus.PENDING_SUBMIT
                or now - intent.created_at >= timedelta(seconds=5)
            )
        ]
        if not intents:
            return None
        intent = intents[0]
        try:
            response = await self.client.order_detail(
                intent.account_id,
                intent.client_order_id,
            )
        except WebullError as exc:
            with self.runtime._strategy_operation_lock:
                current = self._current_intent(intent.id)
                if current is not None and current.status == intent.status:
                    self._pause_intent(current, LiveIntentStatus.UNKNOWN, exc.message)
                return current or intent
        except Exception as exc:
            with self.runtime._strategy_operation_lock:
                current = self._current_intent(intent.id)
                if current is not None and current.status == intent.status:
                    self._pause_intent(
                        current,
                        LiveIntentStatus.UNKNOWN,
                        _safe_error(exc),
                    )
                return current or intent
        with self.runtime._strategy_operation_lock:
            current = self._current_intent(intent.id)
            if current is None or current.status != intent.status:
                return current or intent
            return self._apply_order_detail(current, response)

    def _apply_order_detail(
        self,
        intent: LiveOrderIntent,
        response: dict[str, Any],
    ) -> LiveOrderIntent:
        detail = _extract_order_detail(response)
        broker_status = str(detail.get("status") or "").upper()
        mapped = {
            "PENDING": LiveIntentStatus.ACCEPTED,
            "SUBMITTED": LiveIntentStatus.ACCEPTED,
            "PARTIAL_FILLED": LiveIntentStatus.PARTIAL_FILLED,
            "FILLED": LiveIntentStatus.FILLED,
            "FAILED": LiveIntentStatus.REJECTED,
            "CANCELLED": LiveIntentStatus.CANCELLED,
        }.get(broker_status)
        if mapped is None:
            self._pause_intent(
                intent,
                LiveIntentStatus.UNKNOWN,
                f"Unknown Webull order status: {broker_status or 'missing'}",
            )
            return intent
        intent.status = mapped
        intent.response = response
        self.runtime.repository.upsert_live_order_intent(intent)
        if mapped == LiveIntentStatus.PARTIAL_FILLED:
            self._apply_partial_fill(intent, detail)
        elif mapped == LiveIntentStatus.FILLED:
            self._apply_final_fill(intent, detail)
        elif mapped in {LiveIntentStatus.REJECTED, LiveIntentStatus.CANCELLED}:
            self._pause_intent(
                intent,
                mapped,
                str(detail.get("message") or f"Webull order {broker_status.lower()}"),
            )
        else:
            self._tag_orders(intent, live_status=mapped.value)
        self.runtime.repository.log_live_reconciliation_event(
            strategy_instance_id=intent.strategy_instance_id,
            intent_id=intent.id,
            status=mapped,
            message=f"Order Detail reconciled as {mapped.value}",
            payload={"client_order_id": intent.client_order_id},
        )
        self.runtime.repository.sync_live_virtual_state(
            self.runtime.live_virtual_book.orders
        )
        return intent

    def _current_intent(self, intent_id: str) -> LiveOrderIntent | None:
        return next(
            (
                item
                for item in self.runtime.repository.list_live_order_intents()
                if item.id == intent_id
            ),
            None,
        )

    async def refresh_accounts(self) -> None:
        instances = [
            item
            for item in self.runtime.repository.list_strategy_instances()
            if item.account_alias and item.webull_symbol
        ]
        by_alias: dict[str, list[StrategyInstance]] = {}
        for instance in instances:
            by_alias.setdefault(instance.account_alias, []).append(instance)
        for alias, alias_instances in by_alias.items():
            account_id = self.runtime.settings.resolve_webull_account_alias(alias)
            if not account_id:
                continue
            for instance in alias_instances:
                if (
                    instance.enabled
                    and instance.live_execution_enabled
                    and instance.id not in self._preview_ready
                ):
                    self._preview_ready[instance.id] = await self._preview_both_sides(
                        account_id,
                        instance,
                    )
            try:
                balance, positions = await asyncio.gather(
                    self.client.account_balance(account_id),
                    self.client.positions(account_id),
                )
            except Exception as exc:
                for instance in alias_instances:
                    self.runtime.ingest_live_account_state(
                        LiveAccountState(
                            account_alias=alias,
                            total_net_liquidation_value=0.0,
                            observed_at=utc_now(),
                            strategy_instance_id=instance.id,
                            positions=[],
                            previews_ready=self._preview_ready.get(instance.id, False),
                            reconciliation_ready=False,
                            reconciliation_error=_safe_error(exc),
                        )
                    )
                continue
            observed_at = utc_now()
            position_rows = _as_rows(positions)
            equity = _find_float(balance, "total_net_liquidation_value")
            with self.runtime._strategy_operation_lock:
                for symbol in sorted({item.webull_symbol for item in alias_instances}):
                    self._reconcile_symbol(
                        alias,
                        symbol,
                        position_rows,
                        alias_instances,
                    )
                for instance in alias_instances:
                    reconciliation = (
                        self.runtime.repository.get_live_symbol_reconciliation(
                            alias,
                            instance.webull_symbol,
                        )
                    )
                    ready = bool(
                        reconciliation and reconciliation["status"] == "READY"
                    )
                    error = (
                        str(reconciliation["error_message"])
                        if reconciliation
                        else "pending"
                    )
                    self.runtime.ingest_live_account_state(
                        LiveAccountState(
                            account_alias=alias,
                            total_net_liquidation_value=equity,
                            observed_at=observed_at,
                            strategy_instance_id=instance.id,
                            positions=position_rows,
                            previews_ready=self._preview_ready.get(
                                instance.id,
                                False,
                            ),
                            reconciliation_ready=ready,
                            reconciliation_error="" if ready else error,
                        )
                    )

    async def _preview_both_sides(
        self,
        account_id: str,
        instance: StrategyInstance,
    ) -> bool:
        if not instance.webull_symbol or not instance.asset_class:
            return False
        try:
            for side in (OrderSide.BUY, OrderSide.SELL):
                client_order_id = stable_client_order_id(
                    strategy_instance_id=instance.id,
                    cycle_id="preview",
                    action=LiveIntentAction.OPEN_MARKET,
                    side=side,
                    webull_symbol=instance.webull_symbol,
                    quantity=1,
                    execution_key=f"preview-{side.value.lower()}",
                )
                order = build_market_order_request(
                    client_order_id=client_order_id,
                    webull_symbol=instance.webull_symbol,
                    asset_class=instance.asset_class,
                    side=side,
                    quantity=1,
                )
                await self.client.preview_order(account_id, order)
        except Exception as exc:
            self.runtime.repository.log_activity(
                "LivePreviewBlocked",
                f"Read-only BUY/SELL preview failed: {_safe_error(exc)}",
                level="warning",
                strategy_instance_id=instance.id,
                symbol=instance.symbol,
            )
            return False
        self.runtime.repository.log_activity(
            "LivePreviewReady",
            "Read-only one-contract BUY and SELL previews succeeded",
            strategy_instance_id=instance.id,
            symbol=instance.symbol,
        )
        return True

    def _reconcile_symbol(
        self,
        account_alias: str,
        webull_symbol: str,
        positions: list[dict[str, Any]],
        instances: list[StrategyInstance],
    ) -> None:
        relevant = [
            item
            for item in instances
            if item.account_alias == account_alias and item.webull_symbol == webull_symbol
        ]
        observed = _signed_broker_position(positions, webull_symbol)
        local = self._signed_local_allocation(relevant)
        current = self.runtime.repository.get_live_symbol_reconciliation(
            account_alias,
            webull_symbol,
        )
        if current is None:
            baseline = observed - local
        else:
            baseline = float(current["external_baseline_quantity"])
        expected = baseline + local
        unresolved = any(
            intent.account_alias == account_alias
            and intent.webull_symbol == webull_symbol
            and intent.status
            in {
                LiveIntentStatus.PENDING_SUBMIT,
                LiveIntentStatus.SUBMITTED,
                LiveIntentStatus.ACCEPTED,
                LiveIntentStatus.PARTIAL_FILLED,
            }
            for intent in self.runtime.repository.list_live_order_intents()
        )
        mismatch = abs(observed - expected) > 1e-9
        if unresolved and mismatch:
            status = "PENDING"
            error = "broker mutation is still reconciling"
        elif mismatch:
            status = "MISMATCH"
            error = (
                f"unexplained {webull_symbol} aggregate position mismatch "
                f"(observed={observed:g}, expected={expected:g})"
            )
            for instance in relevant:
                self.runtime.repository.update_strategy_instance(
                    instance.id,
                    {"enabled": False},
                )
                self.runtime.repository.log_activity(
                    "LivePositionMismatch",
                    error,
                    level="error",
                    strategy_instance_id=instance.id,
                    symbol=instance.symbol,
                )
        else:
            status = "READY"
            error = ""
        self.runtime.repository.upsert_live_symbol_reconciliation(
            account_alias=account_alias,
            webull_symbol=webull_symbol,
            external_baseline_quantity=baseline,
            observed_position=observed,
            expected_position=expected,
            status=status,
            error_message=error,
        )

    def _signed_local_allocation(self, instances: list[StrategyInstance]) -> float:
        ids = {item.id for item in instances}
        quantity = 0.0
        for order in self.runtime.live_virtual_book.orders:
            if order.strategy_instance_id not in ids:
                continue
            direction = 1.0 if order.side == OrderSide.BUY else -1.0
            if order.status in {OrderStatus.FILLED, OrderStatus.OPEN}:
                allocated = order.quantity
            elif order.status == OrderStatus.CLOSING:
                allocated = max(
                    order.quantity - float(order.metadata.get("close_filled_quantity") or 0),
                    0.0,
                )
            elif order.status == OrderStatus.OPENING:
                allocated = float(order.metadata.get("broker_filled_quantity") or 0)
            else:
                allocated = 0.0
            quantity += direction * allocated
        return quantity

    def _apply_partial_fill(
        self,
        intent: LiveOrderIntent,
        detail: dict[str, Any],
    ) -> None:
        filled_quantity = _find_float(detail, "filled_quantity")
        fill_price = _fill_price(detail)
        orders = self._orders_for_intent(intent)
        remaining_close_fill = filled_quantity
        for order in orders:
            order.metadata["live_status"] = LiveIntentStatus.PARTIAL_FILLED.value
            if intent.action == LiveIntentAction.OPEN_MARKET:
                order.status = OrderStatus.OPENING
                order.metadata["broker_filled_quantity"] = filled_quantity
                if fill_price > 0:
                    order.metadata["broker_partial_fill_price"] = fill_price
            else:
                order.status = OrderStatus.CLOSING
                allocated = min(remaining_close_fill, order.quantity)
                order.metadata["close_filled_quantity"] = allocated
                remaining_close_fill = max(remaining_close_fill - allocated, 0.0)

    def _apply_final_fill(
        self,
        intent: LiveOrderIntent,
        detail: dict[str, Any],
    ) -> None:
        fill_price = _fill_price(detail)
        now = utc_now()
        for order in self._orders_for_intent(intent):
            order.metadata["live_status"] = LiveIntentStatus.FILLED.value
            if intent.action == LiveIntentAction.OPEN_MARKET:
                order.status = OrderStatus.OPEN
                order.fill_price = fill_price
                order.opened_at = now
                order.metadata["broker_fill_confirmed"] = True
                order.metadata["broker_filled_quantity"] = order.quantity
                stop_distance = float(
                    order.metadata.get("stop_loss_distance_price") or 0
                )
                take_distance = float(
                    order.metadata.get("take_profit_distance_price") or 0
                )
                if fill_price > 0 and stop_distance > 0:
                    order.stop_loss = (
                        fill_price - stop_distance
                        if order.side == OrderSide.BUY
                        else fill_price + stop_distance
                    )
                if fill_price > 0 and take_distance > 0:
                    order.take_profit = (
                        fill_price + take_distance
                        if order.side == OrderSide.BUY
                        else fill_price - take_distance
                    )
                order.metadata["risk_from_fill"] = True
            else:
                order.status = OrderStatus.CLOSED
                order.closed_at = now
                order.metadata["broker_close_fill_confirmed"] = True

    def _pause_intent(
        self,
        intent: LiveOrderIntent,
        status: LiveIntentStatus,
        message: str,
    ) -> None:
        intent.status = status
        intent.error_message = message[:500]
        self.runtime.repository.upsert_live_order_intent(intent)
        orders = self._orders_for_intent(intent)
        for order in orders:
            order.metadata["live_status"] = status.value
            order.metadata["live_error"] = message[:500]
            if intent.action == LiveIntentAction.OPEN_MARKET:
                order.status = OrderStatus.ERROR
            else:
                order.status = OrderStatus.OPEN
                order.closed_at = None
        try:
            self.runtime.repository.update_strategy_instance(
                intent.strategy_instance_id,
                {"enabled": False},
            )
        except KeyError:
            pass
        self.runtime.repository.log_live_reconciliation_event(
            strategy_instance_id=intent.strategy_instance_id,
            intent_id=intent.id,
            status=status,
            message=f"Strategy paused after live mutation problem: {message[:300]}",
        )
        self.runtime.repository.sync_live_virtual_state(
            self.runtime.live_virtual_book.orders
        )

    def _tag_orders(self, intent: LiveOrderIntent, *, live_status: str) -> None:
        for order in self._orders_for_intent(intent):
            order.metadata["live_status"] = live_status

    def _orders_for_intent(self, intent: LiveOrderIntent):
        ids = set(intent.virtual_order_ids)
        return [
            order
            for order in self.runtime.live_virtual_book.orders
            if order.id in ids or order.metadata.get("live_intent_id") == intent.id
        ]


def _extract_order_detail(response: dict[str, Any]) -> dict[str, Any]:
    rows = _as_rows(response)
    if rows:
        return rows[0]
    return response if isinstance(response, dict) else {}


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("orders", "data", "positions", "items"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return [nested]
    return [value]


def _find_float(value: Any, key: str) -> float:
    if isinstance(value, dict):
        if key in value:
            try:
                return float(value[key])
            except (TypeError, ValueError):
                return 0.0
        for nested in value.values():
            found = _find_float(nested, key)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_float(nested, key)
            if found:
                return found
    return 0.0


def _fill_price(detail: dict[str, Any]) -> float:
    for key in ("avg_filled_price", "filled_price", "average_price", "price"):
        price = _find_float(detail, key)
        if price > 0:
            return price
    return 0.0


def _signed_broker_position(
    positions: list[dict[str, Any]],
    webull_symbol: str,
) -> float:
    total = 0.0
    for position in positions:
        symbol = str(position.get("symbol") or "")
        ticker = position.get("ticker")
        if not symbol and isinstance(ticker, dict):
            symbol = str(ticker.get("symbol") or "")
        if symbol != webull_symbol:
            continue
        quantity = _find_float(position, "quantity")
        side = str(position.get("side") or position.get("position_side") or "").upper()
        if side in {"SHORT", "SELL"}:
            quantity = -abs(quantity)
        total += quantity
    return total


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message[:500]
