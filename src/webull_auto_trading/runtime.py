from __future__ import annotations

from dataclasses import asdict
from typing import Any

from webull_auto_trading.config import Settings
from webull_auto_trading.config_loader import load_strategy_instances
from webull_auto_trading.domain import QuoteState
from webull_auto_trading.market_data import QuoteBook, validate_quote
from webull_auto_trading.order_manager import PaperOrderBook
from webull_auto_trading.persistence import RuntimeRepository
from webull_auto_trading.risk import RiskController
from webull_auto_trading.strategy.day_many_bian import DayManyBianStrategy


class RuntimeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = RuntimeRepository(settings.runtime_db_path)
        self.quote_book = QuoteBook()
        self.order_book = PaperOrderBook()
        self.risk = RiskController()
        self.strategies = {"day_many_bian": DayManyBianStrategy()}

    def initialize(self, *, seed_config: bool = True) -> None:
        self.repository.init_db()
        self.risk.global_pause = bool(self.repository.get_setting("global_pause", False))
        if seed_config:
            configured = load_strategy_instances(self.settings.strategies_config_path)
            if configured:
                self.repository.seed_strategy_instances(configured)

    def health(self) -> dict[str, Any]:
        instances = self.repository.list_strategy_instances()
        quotes = self.quote_book.all()
        return {
            "ok": True,
            "mode": "paper-first",
            "global_pause": self.risk.global_pause,
            "strategy_count": len(instances),
            "active_strategy_count": len([item for item in instances if item.enabled]),
            "quote_count": len(quotes),
            "webull_configured": self.settings.production_configured,
            "database": str(self.settings.runtime_db_path),
        }

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
        self.repository.save_quote_snapshot(quote.symbol, quote.fields, quote.received_at)
        return self.evaluate_quote(quote)

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
        return messages

    def snapshot(self) -> dict[str, Any]:
        return {
            "health": self.health(),
            "strategies": [asdict(item) for item in self.repository.list_strategy_instances()],
            "quotes": self.repository.list_quote_snapshots(),
            "orders": [asdict(item) for item in self.order_book.orders],
            "fills": [asdict(item) for item in self.order_book.fills],
        }
