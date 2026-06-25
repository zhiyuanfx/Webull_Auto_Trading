from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from strategy_desk.domain import Fill, OrderStatus, OrderTicket

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS strategy_instances (
  id TEXT PRIMARY KEY,
  plugin_id TEXT NOT NULL,
  plugin_version TEXT NOT NULL,
  plugin_source_hash TEXT NOT NULL,
  mode TEXT NOT NULL,
  account_id TEXT NOT NULL,
  feed_source TEXT NOT NULL,
  state TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_runs (
  id TEXT PRIMARY KEY,
  strategy_instance_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  account_id TEXT NOT NULL,
  feed_source TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_instance
  ON strategy_runs(strategy_instance_id, started_at DESC);

CREATE TABLE IF NOT EXISTS orders (
  ticket_id TEXT PRIMARY KEY,
  strategy_instance_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  broker_order_id TEXT,
  symbol TEXT NOT NULL,
  status TEXT NOT NULL,
  origin TEXT NOT NULL,
  command_json TEXT NOT NULL,
  ticket_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, client_order_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_instance_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS fills (
  id TEXT PRIMARY KEY,
  ticket_id TEXT NOT NULL REFERENCES orders(ticket_id),
  strategy_instance_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  quantity TEXT NOT NULL,
  price TEXT NOT NULL,
  commission TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  filled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS simulator_accounts (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  initial_cash TEXT NOT NULL,
  cash TEXT NOT NULL,
  commission_per_unit TEXT NOT NULL,
  slippage_bps TEXT NOT NULL,
  latency_ms INTEGER NOT NULL,
  partial_fills INTEGER NOT NULL,
  leverage TEXT NOT NULL,
  futures_margin_per_contract TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS virtual_positions (
  strategy_instance_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  quantity TEXT NOT NULL,
  average_price TEXT NOT NULL,
  realized_pnl TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(strategy_instance_id, symbol)
);

CREATE TABLE IF NOT EXISTS virtual_lots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_instance_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  signed_quantity TEXT NOT NULL,
  entry_price TEXT NOT NULL,
  opened_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_virtual_lots_strategy_symbol
  ON virtual_lots(strategy_instance_id, symbol, id);

CREATE TABLE IF NOT EXISTS checkpoints (
  strategy_instance_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(strategy_instance_id, key)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  strategy_instance_id TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


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
            self._ensure_column(
                connection,
                "strategy_instances",
                "plugin_source_hash",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection, "simulator_accounts", "leverage", "TEXT NOT NULL DEFAULT '1'"
            )
            self._ensure_column(
                connection,
                "simulator_accounts",
                "futures_margin_per_contract",
                "TEXT NOT NULL DEFAULT '0'",
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_instance(self, instance: dict[str, object]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_instances(
                  id, plugin_id, plugin_version, plugin_source_hash, mode, account_id, feed_source,
                  state, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  state=excluded.state,
                  config_json=excluded.config_json,
                  updated_at=excluded.updated_at
                """,
                (
                    instance["id"],
                    instance["plugin_id"],
                    instance["plugin_version"],
                    instance["plugin_source_hash"],
                    instance["mode"],
                    instance["account_id"],
                    instance["feed_source"],
                    instance["state"],
                    json.dumps(instance.get("config", {})),
                    str(instance.get("created_at", now)),
                    now,
                ),
            )

    def instances(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_instances ORDER BY created_at DESC"
            ).fetchall()
        values: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["config"] = json.loads(str(item.pop("config_json")))
            values.append(item)
        return values

    def instance(self, instance_id: str) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_instances WHERE id=?", (instance_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["config"] = json.loads(str(item.pop("config_json")))
        return item

    def save_ticket(self, ticket: OrderTicket) -> None:
        payload = ticket.model_dump_json()
        command = ticket.command
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (
                  ticket_id, strategy_instance_id, account_id, client_order_id,
                  broker_order_id, symbol, status, origin, command_json, ticket_json,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                  broker_order_id=excluded.broker_order_id,
                  status=excluded.status,
                  ticket_json=excluded.ticket_json,
                  updated_at=excluded.updated_at
                """,
                (
                    ticket.id,
                    command.strategy_instance_id,
                    command.account_id,
                    command.client_order_id,
                    ticket.broker_order_id,
                    command.symbol,
                    ticket.status,
                    command.origin,
                    command.model_dump_json(),
                    payload,
                    command.submitted_at.isoformat(),
                    ticket.updated_at.isoformat(),
                ),
            )

    def start_run(
        self,
        run_id: str,
        strategy_instance_id: str,
        mode: str,
        account_id: str,
        feed_source: str,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO strategy_runs(
                     id, strategy_instance_id, mode, account_id, feed_source, status, started_at
                   ) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)""",
                (
                    run_id,
                    strategy_instance_id,
                    mode,
                    account_id,
                    feed_source,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def end_active_run(self, strategy_instance_id: str, status: str) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """UPDATE strategy_runs SET status=?, ended_at=?
                   WHERE id=(SELECT id FROM strategy_runs
                     WHERE strategy_instance_id=? AND ended_at IS NULL
                     ORDER BY started_at DESC LIMIT 1)""",
                (status, datetime.now(UTC).isoformat(), strategy_instance_id),
            )

    def runs(self, strategy_instance_id: str) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_runs WHERE strategy_instance_id=? ORDER BY started_at DESC",
                (strategy_instance_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ticket_by_id(self, ticket_id: str) -> OrderTicket | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT ticket_json FROM orders WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
        return OrderTicket.model_validate_json(row["ticket_json"]) if row else None

    def ticket_by_client_order_id(
        self, account_id: str, client_order_id: str
    ) -> OrderTicket | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT ticket_json FROM orders
                   WHERE account_id=? AND client_order_id=?""",
                (account_id, client_order_id),
            ).fetchone()
        return OrderTicket.model_validate_json(row["ticket_json"]) if row else None

    def tickets_for_strategy(self, strategy_instance_id: str) -> list[OrderTicket]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT ticket_json FROM orders WHERE strategy_instance_id = ? ORDER BY updated_at",
                (strategy_instance_id,),
            ).fetchall()
        return [OrderTicket.model_validate_json(row["ticket_json"]) for row in rows]

    def all_tickets(self) -> list[OrderTicket]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT ticket_json FROM orders ORDER BY updated_at"
            ).fetchall()
        return [OrderTicket.model_validate_json(row["ticket_json"]) for row in rows]

    def open_ticket_count(self, strategy_instance_id: str) -> int:
        terminal = (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.FAILED,
            OrderStatus.REJECTED,
        )
        placeholders = ",".join("?" for _ in terminal)
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM orders WHERE strategy_instance_id = ? "
                f"AND status NOT IN ({placeholders})",
                (strategy_instance_id, *terminal),
            ).fetchone()
        return int(row["count"])

    def save_fill(self, fill: Fill) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO fills (
                  id, ticket_id, strategy_instance_id, symbol, quantity, price,
                  commission, payload_json, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.id,
                    fill.ticket_id,
                    fill.strategy_instance_id,
                    fill.symbol,
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.commission),
                    fill.model_dump_json(),
                    fill.filled_at.isoformat(),
                ),
            )

    def fills_for_strategy(self, strategy_instance_id: str) -> list[Fill]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM fills WHERE strategy_instance_id=? ORDER BY filled_at",
                (strategy_instance_id,),
            ).fetchall()
        return [Fill.model_validate_json(row["payload_json"]) for row in rows]

    def save_position(
        self,
        strategy_instance_id: str,
        symbol: str,
        quantity: Decimal,
        average_price: Decimal,
        realized_pnl: Decimal,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO virtual_positions VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_instance_id, symbol) DO UPDATE SET
                  quantity=excluded.quantity,
                  average_price=excluded.average_price,
                  realized_pnl=excluded.realized_pnl,
                  updated_at=excluded.updated_at
                """,
                (
                    strategy_instance_id,
                    symbol,
                    str(quantity),
                    str(average_price),
                    str(realized_pnl),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def lots(self, strategy_instance_id: str, symbol: str) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, signed_quantity, entry_price, opened_at FROM virtual_lots
                   WHERE strategy_instance_id=? AND symbol=? ORDER BY id""",
                (strategy_instance_id, symbol),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "signed_quantity": Decimal(row["signed_quantity"]),
                "entry_price": Decimal(row["entry_price"]),
                "opened_at": row["opened_at"],
            }
            for row in rows
        ]

    def replace_lots(
        self,
        strategy_instance_id: str,
        symbol: str,
        lots: list[dict[str, object]],
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                "DELETE FROM virtual_lots WHERE strategy_instance_id=? AND symbol=?",
                (strategy_instance_id, symbol),
            )
            connection.executemany(
                """INSERT INTO virtual_lots(
                     strategy_instance_id, symbol, signed_quantity, entry_price, opened_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        strategy_instance_id,
                        symbol,
                        str(lot["signed_quantity"]),
                        str(lot["entry_price"]),
                        str(lot["opened_at"]),
                    )
                    for lot in lots
                ],
            )

    def position(self, strategy_instance_id: str, symbol: str) -> dict[str, Decimal]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT quantity, average_price, realized_pnl FROM virtual_positions
                   WHERE strategy_instance_id=? AND symbol=?""",
                (strategy_instance_id, symbol),
            ).fetchone()
        if not row:
            return {
                "quantity": Decimal("0"),
                "average_price": Decimal("0"),
                "realized_pnl": Decimal("0"),
            }
        return {key: Decimal(row[key]) for key in ("quantity", "average_price", "realized_pnl")}

    def positions(self, strategy_instance_id: str | None = None) -> list[dict[str, object]]:
        sql = "SELECT * FROM virtual_positions"
        args: tuple[object, ...] = ()
        if strategy_instance_id:
            sql += " WHERE strategy_instance_id=?"
            args = (strategy_instance_id,)
        sql += " ORDER BY strategy_instance_id, symbol"
        with self.connect() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def save_checkpoint(self, strategy_instance_id: str, key: str, value: object) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints(strategy_instance_id, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(strategy_instance_id, key) DO UPDATE SET
                  value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (strategy_instance_id, key, json.dumps(value), now),
            )

    def load_checkpoint(self, strategy_instance_id: str, key: str) -> object | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM checkpoints WHERE strategy_instance_id=? AND key=?",
                (strategy_instance_id, key),
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def create_simulator_account(
        self,
        account_id: str,
        name: str,
        initial_cash: Decimal,
        commission_per_unit: Decimal,
        slippage_bps: Decimal,
        latency_ms: int,
        partial_fills: bool,
        leverage: Decimal,
        futures_margin_per_contract: Decimal,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO simulator_accounts(
                  id, name, initial_cash, cash, commission_per_unit, slippage_bps,
                  latency_ms, partial_fills, leverage, futures_margin_per_contract, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    name,
                    str(initial_cash),
                    str(initial_cash),
                    str(commission_per_unit),
                    str(slippage_bps),
                    latency_ms,
                    int(partial_fills),
                    str(leverage),
                    str(futures_margin_per_contract),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def simulator_accounts(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM simulator_accounts ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def adjust_simulator_cash(self, account_id: str, amount: Decimal) -> None:
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT cash FROM simulator_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE simulator_accounts SET cash=? WHERE id=?",
                    (str(Decimal(row["cash"]) + amount), account_id),
                )

    def audit(
        self, kind: str, payload: dict[str, object], strategy_instance_id: str | None = None
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_events(kind, strategy_instance_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (kind, strategy_instance_id, json.dumps(payload), datetime.now(UTC).isoformat()),
            )

    def audit_events(self, strategy_instance_id: str, limit: int = 200) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, kind, payload_json, created_at FROM audit_events
                   WHERE strategy_instance_id=? ORDER BY id DESC LIMIT ?""",
                (strategy_instance_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
