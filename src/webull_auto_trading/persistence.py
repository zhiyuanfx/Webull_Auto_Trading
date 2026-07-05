from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from webull_auto_trading.domain import ExecutionMode, StrategyInstance, new_id, utc_now

SCHEMA_VERSION = 1


def encode_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(UTC).isoformat()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class RuntimeRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_instances (
                    id TEXT PRIMARY KEY,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    mode TEXT NOT NULL DEFAULT 'paper',
                    params_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quote_snapshots (
                    symbol TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    bar_type TEXT NOT NULL,
                    bar_interval INTEGER NOT NULL,
                    bar_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (symbol, bar_type, bar_interval, bar_time)
                );
                CREATE TABLE IF NOT EXISTS cycles (
                    id TEXT PRIMARY KEY,
                    strategy_instance_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    strategy_instance_id TEXT NOT NULL,
                    cycle_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    stop_price REAL,
                    fill_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    parent_order_id TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fills (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    strategy_instance_id TEXT NOT NULL,
                    cycle_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    filled_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    strategy_instance_id TEXT,
                    symbol TEXT,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS risk_state (
                    scope TEXT NOT NULL,
                    ref_id TEXT NOT NULL,
                    locked INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scope, ref_id)
                );
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return decode_json(row["value_json"], default) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (key, encode_json(value), iso()),
            )

    def list_strategy_instances(self) -> list[StrategyInstance]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_instances ORDER BY symbol, strategy_name, id"
            ).fetchall()
        return [self._instance_from_row(row) for row in rows]

    def upsert_strategy_instance(self, instance: StrategyInstance) -> StrategyInstance:
        instance.updated_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_instances(
                    id, strategy_name, symbol, account_id, enabled, mode,
                    params_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    strategy_name=excluded.strategy_name,
                    symbol=excluded.symbol,
                    account_id=excluded.account_id,
                    enabled=excluded.enabled,
                    mode=excluded.mode,
                    params_json=excluded.params_json,
                    updated_at=excluded.updated_at
                """,
                (
                    instance.id,
                    instance.strategy_name,
                    instance.symbol,
                    instance.account_id,
                    int(instance.enabled),
                    instance.mode.value,
                    encode_json(instance.params),
                    iso(instance.created_at),
                    iso(instance.updated_at),
                ),
            )
        return instance

    def create_strategy_instance(self, data: dict[str, Any]) -> StrategyInstance:
        mode = ExecutionMode(data.get("mode", ExecutionMode.PAPER))
        if mode not in (ExecutionMode.PAPER, ExecutionMode.PREVIEW):
            raise ValueError("strategy mode must be paper or preview")
        instance = StrategyInstance(
            id=str(data.get("id") or new_id("st")),
            strategy_name=str(data.get("strategy_name") or "day_many_bian"),
            symbol=str(data["symbol"]),
            account_id=str(data.get("account_id") or ""),
            enabled=bool(data.get("enabled", True)),
            mode=mode,
            params=dict(data.get("params") or {}),
        )
        return self.upsert_strategy_instance(instance)

    def update_strategy_instance(
        self,
        instance_id: str,
        changes: dict[str, Any],
    ) -> StrategyInstance:
        instances = {item.id: item for item in self.list_strategy_instances()}
        if instance_id not in instances:
            raise KeyError(instance_id)
        current = instances[instance_id]
        for field_name in ("strategy_name", "symbol", "account_id", "enabled"):
            if field_name in changes:
                setattr(current, field_name, changes[field_name])
        if "mode" in changes:
            current.mode = ExecutionMode(changes["mode"])
        if "params" in changes:
            current.params = dict(changes["params"] or {})
        return self.upsert_strategy_instance(current)

    def save_quote_snapshot(
        self,
        symbol: str,
        fields: dict[str, Any],
        received_at: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO quote_snapshots(symbol, payload_json, received_at)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    received_at=excluded.received_at
                """,
                (symbol, encode_json(fields), iso(received_at)),
            )

    def list_quote_snapshots(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM quote_snapshots ORDER BY symbol").fetchall()
        return [
            {
                "symbol": row["symbol"],
                "fields": decode_json(row["payload_json"], {}),
                "received_at": row["received_at"],
            }
            for row in rows
        ]

    def list_table(self, table: str, limit: int = 200) -> list[dict[str, Any]]:
        if table not in {"orders", "fills", "cycles", "activity", "bars"}:
            raise ValueError(f"Unsupported table: {table}")
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_activity(
        self,
        event_type: str,
        message: str,
        *,
        level: str = "info",
        strategy_instance_id: str | None = None,
        symbol: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO activity(
                    ts, level, strategy_instance_id, symbol, event_type, message, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iso(),
                    level,
                    strategy_instance_id,
                    symbol,
                    event_type,
                    message,
                    encode_json(payload or {}),
                ),
            )

    def seed_strategy_instances(self, instances: Iterable[StrategyInstance]) -> None:
        for instance in instances:
            self.upsert_strategy_instance(instance)

    @staticmethod
    def _instance_from_row(row: sqlite3.Row) -> StrategyInstance:
        return StrategyInstance(
            id=row["id"],
            strategy_name=row["strategy_name"],
            symbol=row["symbol"],
            account_id=row["account_id"],
            enabled=bool(row["enabled"]),
            mode=ExecutionMode(row["mode"]),
            params=decode_json(row["params_json"], {}),
            created_at=parse_dt(row["created_at"]),
            updated_at=parse_dt(row["updated_at"]),
        )
