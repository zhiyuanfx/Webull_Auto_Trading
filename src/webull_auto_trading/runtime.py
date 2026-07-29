from __future__ import annotations

import asyncio
import importlib
from dataclasses import asdict
from datetime import datetime
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
from webull_auto_trading.strategy.base import Strategy
from webull_auto_trading.strategy.recycle_buy import RecycleBuyStrategy


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
        runtime_mode = self.repository.set_runtime_mode(mode)
        self.reload_strategy_config()
        self.repository.log_activity(
            "RuntimeModeChanged",
            f"Runtime mode changed to {runtime_mode.value}",
            payload={"mode": runtime_mode.value},
        )
        return {"mode": runtime_mode.value, "config_path": str(self.mode_config_path(runtime_mode))}

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
        if self.active_mode() != RuntimeMode.LIVE:
            raise PermissionError("Live execution can only be changed in live mode")
        instance = self.repository.update_strategy_instance(
            strategy_id,
            {"live_execution_enabled": enabled},
        )
        errors = validate_live_strategy_config(instance, self.settings) if enabled else []
        self.repository.log_activity(
            "LiveExecutionEnabled" if enabled else "LiveExecutionDisabled",
            f"Live execution {'enabled' if enabled else 'disabled'} for {strategy_id}",
            level="warning" if enabled else "info",
            strategy_instance_id=instance.id,
            symbol=instance.symbol,
            payload={"blocked": bool(errors), "errors": errors},
        )
        return {**asdict(instance), "live_execution_errors": errors}

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
