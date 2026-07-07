from __future__ import annotations

from pathlib import Path
from typing import Any

from webull_auto_trading.domain import ExecutionMode, StrategyInstance, new_id


def load_strategy_instances(path: Path) -> list[StrategyInstance]:
    if not path.exists():
        return []
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install PyYAML to load strategy config files") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = payload.get("strategies") if isinstance(payload, dict) else payload
    if not items:
        return []
    if not isinstance(items, list):
        raise ValueError("strategies config must contain a list")
    return [_instance_from_config(item) for item in items]


def _instance_from_config(item: dict[str, Any]) -> StrategyInstance:
    market_data_symbol = str(item.get("market_data_symbol") or item.get("symbol") or "")
    return StrategyInstance(
        id=str(item.get("id") or new_id("st")),
        strategy_name=str(item.get("strategy_name") or "day_many_bian"),
        symbol=market_data_symbol,
        account_id=str(item.get("account_id") or ""),
        enabled=bool(item.get("enabled", True)),
        mode=ExecutionMode.PAPER,
        market_data_symbol=market_data_symbol,
        webull_symbol=str(item.get("webull_symbol") or ""),
        account_alias=str(item.get("account_alias") or ""),
        asset_class=str(item.get("asset_class") or ""),
        live_execution_enabled=bool(item.get("live_execution_enabled", False)),
        params=dict(item.get("params") or {}),
    )
