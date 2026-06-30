from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from webull_bridge.domain import (
    EventStatus,
    RouteConfig,
    RouteUpdate,
    hash_secret,
    utc_iso,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
  route_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  account_id TEXT NOT NULL,
  secret_hash TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  allowed_symbols_json TEXT NOT NULL,
  max_quantity TEXT NOT NULL,
  max_notional TEXT NOT NULL,
  accepted_order_types_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  route_id TEXT NOT NULL REFERENCES routes(route_id),
  event_id TEXT NOT NULL,
  action TEXT NOT NULL,
  symbol TEXT,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  normalized_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(route_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_updated
  ON webhook_events(updated_at DESC);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_pk INTEGER REFERENCES webhook_events(id),
  route_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  client_order_id TEXT,
  target_client_order_id TEXT,
  webull_order_id TEXT,
  action TEXT NOT NULL,
  symbol TEXT,
  side TEXT,
  status TEXT NOT NULL,
  order_json TEXT NOT NULL DEFAULT '{}',
  preview_json TEXT NOT NULL DEFAULT '{}',
  response_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, client_order_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_updated ON orders(updated_at DESC);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS position_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class Ledger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as connection:
            connection.executescript(SCHEMA)

    def bootstrap(self, *, execution_enabled: bool, route: RouteConfig, secret: str) -> None:
        self.set_setting("execution_enabled", execution_enabled, overwrite=False)
        if self.get_route(route.route_id) is None:
            route.secret_hash = hash_secret(secret) if secret else ""
            self.upsert_route(route)
            self.audit(
                "ROUTE_BOOTSTRAPPED",
                "info",
                f"Route {route.route_id} created from configuration",
                {"route_id": route.route_id, "secret_configured": bool(secret)},
            )

    def set_setting(self, key: str, value: Any, *, overwrite: bool = True) -> None:
        with self._lock, self.connect() as connection:
            if not overwrite:
                exists = connection.execute(
                    "SELECT 1 FROM settings WHERE key = ?", (key,)
                ).fetchone()
                if exists:
                    return
            connection.execute(
                """INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                   updated_at=excluded.updated_at""",
                (key, _json(value), utc_iso()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return _loads(row["value_json"], default) if row else default

    def database_stats(self) -> dict[str, Any]:
        tables: dict[str, int] = {}
        with self.connect() as connection:
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ):
                table = row["name"]
                count = connection.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()
                tables[table] = int(count["count"])
        return {
            "database": str(self.path),
            "database_exists": self.path.exists(),
            "file_size_bytes": self.path.stat().st_size if self.path.exists() else None,
            "tables": tables,
        }

    def vacuum(self) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
        return self.database_stats()

    def upsert_route(self, route: RouteConfig) -> RouteConfig:
        now = utc_iso()
        with self._lock, self.connect() as connection:
            existing = connection.execute(
                "SELECT created_at, secret_hash FROM routes WHERE route_id = ?", (route.route_id,)
            ).fetchone()
            secret_hash_value = route.secret_hash or (existing["secret_hash"] if existing else "")
            created_at = existing["created_at"] if existing else now
            connection.execute(
                """INSERT INTO routes(
                    route_id, name, account_id, secret_hash, enabled, allowed_symbols_json,
                    max_quantity, max_notional, accepted_order_types_json, created_at, updated_at
                  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(route_id) DO UPDATE SET
                    name=excluded.name, account_id=excluded.account_id,
                    secret_hash=excluded.secret_hash, enabled=excluded.enabled,
                    allowed_symbols_json=excluded.allowed_symbols_json,
                    max_quantity=excluded.max_quantity, max_notional=excluded.max_notional,
                    accepted_order_types_json=excluded.accepted_order_types_json,
                    updated_at=excluded.updated_at""",
                (
                    route.route_id,
                    route.name,
                    route.account_id,
                    secret_hash_value,
                    int(route.enabled),
                    _json(route.allowed_symbols),
                    str(route.max_quantity),
                    str(route.max_notional),
                    _json([item.value for item in route.accepted_order_types]),
                    created_at,
                    now,
                ),
            )
        return self.get_route(route.route_id) or route

    def update_route(self, route_id: str, update: RouteUpdate) -> RouteConfig:
        route = self.require_route(route_id)
        values = update.model_dump(exclude_unset=True)
        if "secret" in values:
            route.secret_hash = hash_secret(values.pop("secret"))
        for key, value in values.items():
            if value is not None:
                setattr(route, key, value)
        saved = self.upsert_route(route)
        self.audit("ROUTE_UPDATED", "info", f"Route {route_id} updated", {"route_id": route_id})
        return saved

    def get_route(self, route_id: str) -> RouteConfig | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM routes WHERE route_id = ?", (route_id,)
            ).fetchone()
        return self._route_from_row(row) if row else None

    def require_route(self, route_id: str) -> RouteConfig:
        route = self.get_route(route_id)
        if route is None:
            raise KeyError(route_id)
        return route

    def list_routes(self) -> list[RouteConfig]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM routes ORDER BY route_id").fetchall()
        return [self._route_from_row(row) for row in rows]

    @staticmethod
    def _route_from_row(row: sqlite3.Row) -> RouteConfig:
        return RouteConfig(
            route_id=row["route_id"],
            name=row["name"],
            account_id=row["account_id"],
            secret_hash=row["secret_hash"],
            enabled=bool(row["enabled"]),
            allowed_symbols=_loads(row["allowed_symbols_json"], []),
            max_quantity=Decimal(row["max_quantity"]),
            max_notional=Decimal(row["max_notional"]),
            accepted_order_types=_loads(row["accepted_order_types_json"], []),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create_event(
        self, route_id: str, event_id: str, action: str, symbol: str | None, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        now = utc_iso()
        with self._lock, self.connect() as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO webhook_events(
                         route_id, event_id, action, symbol, status, payload_json,
                         created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        route_id,
                        event_id,
                        action,
                        symbol,
                        EventStatus.QUEUED.value,
                        _json(payload),
                        now,
                        now,
                    ),
                )
                event_pk = cursor.lastrowid
                duplicate = False
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT id FROM webhook_events WHERE route_id = ? AND event_id = ?",
                    (route_id, event_id),
                ).fetchone()
                event_pk = row["id"]
                duplicate = True
        event = self.get_event(int(event_pk))
        return event, duplicate

    def get_event(self, event_pk: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM webhook_events WHERE id = ?", (event_pk,)
            ).fetchone()
        if row is None:
            raise KeyError(event_pk)
        return self._event_from_row(row)

    def list_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM webhook_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def queued_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM webhook_events
                   WHERE status = ?
                   ORDER BY id ASC LIMIT ?""",
                (EventStatus.QUEUED.value, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        item["normalized"] = _loads(item.pop("normalized_json"), {})
        return item

    def update_event(
        self,
        event_pk: int,
        status: str,
        *,
        normalized: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """UPDATE webhook_events
                   SET status = ?, normalized_json = COALESCE(?, normalized_json),
                   error = ?, updated_at = ? WHERE id = ?""",
                (
                    status,
                    _json(normalized) if normalized is not None else None,
                    error,
                    utc_iso(),
                    event_pk,
                ),
            )

    def create_order(self, event_pk: int, order: dict[str, Any]) -> int:
        now = utc_iso()
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO orders(
                    event_pk, route_id, account_id, client_order_id, target_client_order_id,
                    action, symbol, side, status, order_json, created_at, updated_at
                  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_pk,
                    order["route_id"],
                    order["account_id"],
                    order.get("client_order_id"),
                    order.get("target_client_order_id"),
                    order["action"],
                    order.get("symbol"),
                    order.get("side"),
                    EventStatus.PROCESSING.value,
                    _json(order.get("order", {})),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_order(
        self,
        order_pk: int,
        status: str,
        *,
        preview: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        webull_order_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """UPDATE orders SET status = ?, preview_json = COALESCE(?, preview_json),
                   response_json = COALESCE(?, response_json),
                   webull_order_id = COALESCE(?, webull_order_id),
                   error = ?, updated_at = ? WHERE id = ?""",
                (
                    status,
                    _json(preview) if preview is not None else None,
                    _json(response) if response is not None else None,
                    webull_order_id,
                    error,
                    utc_iso(),
                    order_pk,
                ),
            )

    def list_orders(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._order_from_row(row) for row in rows]

    def known_open_order_refs(self) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT account_id, client_order_id FROM orders
                   WHERE client_order_id IS NOT NULL
                   AND status NOT IN (?, ?, ?, ?)""",
                (
                    EventStatus.FILLED.value,
                    EventStatus.CANCELLED.value,
                    EventStatus.FAILED.value,
                    EventStatus.REJECTED.value,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _order_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["order"] = _loads(item.pop("order_json"), {})
        item["preview"] = _loads(item.pop("preview_json"), {})
        item["response"] = _loads(item.pop("response_json"), {})
        return item

    def save_position_snapshot(self, account_id: str, positions: list[dict[str, Any]]) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO position_snapshots(account_id, payload_json, created_at)
                   VALUES (?, ?, ?)""",
                (account_id, _json(positions), utc_iso()),
            )

    def latest_position_snapshot(self, account_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM position_snapshots WHERE account_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (account_id,),
            ).fetchone()
        return _loads(row["payload_json"], []) if row else []

    def audit(
        self, kind: str, level: str, message: str, payload: dict[str, Any] | None = None
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO activity(kind, level, message, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (kind, level, message, _json(payload or {}), utc_iso()),
            )

    def list_activity(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"), {})
            result.append(item)
        return result
