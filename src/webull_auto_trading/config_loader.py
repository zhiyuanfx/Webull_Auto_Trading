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
        raise RuntimeError("Install PyYAML to load config/strategies.yml") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = payload.get("strategies") if isinstance(payload, dict) else payload
    if not items:
        return []
    if not isinstance(items, list):
        raise ValueError("strategies config must contain a list")
    return [_instance_from_config(item) for item in items]


def _instance_from_config(item: dict[str, Any]) -> StrategyInstance:
    mode = ExecutionMode(item.get("mode", "paper"))
    if mode not in (ExecutionMode.PAPER, ExecutionMode.PREVIEW):
        raise ValueError("strategy mode must be paper or preview")
    return StrategyInstance(
        id=str(item.get("id") or new_id("st")),
        strategy_name=str(item.get("strategy_name") or "day_many_bian"),
        symbol=str(item["symbol"]),
        account_id=str(item.get("account_id") or ""),
        enabled=bool(item.get("enabled", True)),
        mode=mode,
        params=dict(item.get("params") or {}),
    )
