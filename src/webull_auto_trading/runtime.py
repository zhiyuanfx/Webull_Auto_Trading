from __future__ import annotations

from dataclasses import asdict
from typing import Any

from webull_auto_trading.config import Settings
from webull_auto_trading.config_loader import load_strategy_instances
from webull_auto_trading.domain import QuoteState, RuntimeMode
from webull_auto_trading.market_data import (
    MarketStreamBuffer,
    QuoteBook,
    parse_market_message,
    validate_quote,
)
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.persistence import RuntimeRepository
from webull_auto_trading.risk import RiskController
from webull_auto_trading.strategy.day_many_bian import DayManyBianStrategy


class RuntimeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = RuntimeRepository(settings.runtime_db_path)
        self.quote_book = QuoteBook()
        self.stream_buffer = MarketStreamBuffer(max_messages=20)
        self.order_book = PaperOrderBook()
        self.risk = RiskController()
        self.strategies = {"day_many_bian": DayManyBianStrategy()}

    def initialize(self, *, seed_config: bool = True) -> None:
        self.repository.init_db()
        self.risk.global_pause = bool(self.repository.get_setting("global_pause", False))
        self.order_book.orders = self.repository.list_paper_orders()
        self.order_book.fills = self.repository.list_paper_fills()
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
        if not configured and not path.exists():
            configured = load_strategy_instances(self.settings.strategies_config_path)
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
            return "Live mode: Webull reads enabled, live execution disabled"
        return "Test mode: paper trading with real InsightSentry market data"

    def set_global_pause(self, paused: bool) -> dict[str, bool]:
        self.risk.set_global_pause(paused)
        self.repository.set_setting("global_pause", paused)
        self.repository.log_activity(
            "LockGlobal" if paused else "UnlockGlobal",
            "Global pause enabled" if paused else "Global pause disabled",
        )
        return {"global_pause": paused}

    def ingest_quote_item(self, item: dict[str, Any]) -> list[str]:
        quote = self.quote_book.merge_quote_item(item)
        self.record_stream_message(quote.symbol, "quote", item)
        return self.evaluate_quote(quote)

    def ingest_market_message(self, message: str) -> list[str]:
        parsed = parse_market_message(message)
        if parsed.kind == "quote" and isinstance(parsed.payload, dict):
            messages: list[str] = []
            for item in parsed.payload.get("data", []):
                if isinstance(item, dict):
                    messages.extend(self.ingest_quote_item(item))
            return messages
        if parsed.kind == "series" and isinstance(parsed.payload, dict):
            symbol = str(parsed.payload.get("code") or "")
            self.record_stream_message(symbol, "series", parsed.payload)
        return []

    def record_stream_message(self, symbol: str, message_type: str, raw: dict[str, Any]) -> None:
        for instance in self.repository.list_strategy_instances():
            if instance.enabled and instance.symbol == symbol:
                self.stream_buffer.append(
                    strategy_instance_id=instance.id,
                    symbol=symbol,
                    message_type=message_type,
                    raw=raw,
                )

    def evaluate_quote(self, quote: QuoteState) -> list[str]:
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
            return []
        messages: list[str] = []
        for instance in self.repository.list_strategy_instances():
            if not instance.enabled or instance.symbol != quote.symbol:
                continue
            decision = self.risk.allow_instance(instance.id)
            if not decision.allowed:
                messages.append(decision.reason)
                continue
            strategy = self.strategies.get(instance.strategy_name)
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
        self.repository.sync_paper_state(
            self.order_book.orders,
            self.order_book.fills,
            market_prices=self.current_market_prices(),
        )
        return messages

    def current_market_prices(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        for quote in self.quote_book.all():
            price = quote.last_price or quote.mid_price or quote.bid or quote.ask
            if price is not None:
                prices[quote.symbol] = price
        return prices

    def snapshot(self) -> dict[str, Any]:
        return {
            "health": self.health(),
            "strategies": [asdict(item) for item in self.repository.list_strategy_instances()],
            "quotes": [asdict(item) for item in self.quote_book.all()],
            "orders": [asdict(item) for item in self.order_book.orders],
            "fills": [asdict(item) for item in self.order_book.fills],
        }
