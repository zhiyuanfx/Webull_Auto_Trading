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

PRUNABLE_TABLES: dict[str, tuple[str, ...]] = {
    "strategy_instances": ("updated_at",),
    "strategy_runs": ("COALESCE(ended_at, started_at)",),
    "orders": ("updated_at",),
    "fills": ("filled_at",),
    "virtual_positions": ("updated_at",),
    "virtual_lots": ("opened_at",),
    "checkpoints": ("updated_at",),
    "audit_events": ("created_at",),
}
ARCHIVE_TABLE_ORDER = tuple(PRUNABLE_TABLES)
DELETE_TABLE_ORDER = (
    "audit_events",
    "checkpoints",
    "virtual_lots",
    "virtual_positions",
    "fills",
    "orders",
    "strategy_runs",
    "strategy_instances",
)

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

    @staticmethod
    def _quote_identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    @staticmethod
    def _file_size(path: Path) -> int | None:
        return path.stat().st_size if path.exists() else None

    @property
    def wal_path(self) -> Path:
        return Path(f"{self.path}-wal")

    @property
    def shm_path(self) -> Path:
        return Path(f"{self.path}-shm")

    def database_stats(self) -> dict[str, object]:
        stats: dict[str, object] = {
            "database": str(self.path),
            "database_exists": self.path.exists(),
            "file_size_bytes": self._file_size(self.path),
            "wal_size_bytes": self._file_size(self.wal_path),
            "shm_size_bytes": self._file_size(self.shm_path),
            "tables": {},
        }
        if not self.path.exists():
            return stats
        with self.connect() as connection:
            tables = [
                row["name"]
                for row in connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table' AND name NOT LIKE 'sqlite_%'
                       ORDER BY name"""
                )
            ]
            stats["tables"] = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) AS count FROM {self._quote_identifier(table)}"
                    ).fetchone()["count"]
                )
                for table in tables
            }
        stats["file_size_bytes"] = self._file_size(self.path)
        stats["wal_size_bytes"] = self._file_size(self.wal_path)
        stats["shm_size_bytes"] = self._file_size(self.shm_path)
        return stats

    def prune_database(
        self,
        cutoff: datetime,
        *,
        execute: bool = False,
        archive_path: Path | None = None,
    ) -> dict[str, object]:
        cutoff = cutoff.astimezone(UTC)
        result: dict[str, object] = {
            "database": str(self.path),
            "database_exists": self.path.exists(),
            "cutoff": cutoff.isoformat(),
            "dry_run": not execute,
            "scope": "local_runtime_tables",
            "tables": {
                table: {"matched": 0, "archived": 0, "deleted": 0}
                for table in PRUNABLE_TABLES
            },
            "archive": None,
            "limitations": [
                "Rows older than the cutoff are pruned from local runtime tables.",
                "Simulator account configuration is preserved.",
            ],
        }
        if archive_path is not None:
            result["archive"] = {
                "path": str(archive_path),
                "created": False,
                "rows_archived": 0,
            }
        if not self.path.exists():
            return result

        cutoff_text = cutoff.isoformat()
        with self._lock, self.connect() as connection:
            rows_by_table = {
                table: self._prunable_rows(connection, table, cutoff_text)
                for table in ARCHIVE_TABLE_ORDER
            }
            total_matched = sum(len(rows) for rows in rows_by_table.values())
            for table, rows in rows_by_table.items():
                result["tables"][table]["matched"] = len(rows)
            if not execute or total_matched == 0:
                return result
            if archive_path is not None:
                self._archive_rows(archive_path, rows_by_table)
                result["archive"] = {
                    "path": str(archive_path),
                    "created": True,
                    "rows_archived": total_matched,
                }
                for table, rows in rows_by_table.items():
                    result["tables"][table]["archived"] = len(rows)
            for table in DELETE_TABLE_ORDER:
                result["tables"][table]["deleted"] = self._delete_rows(
                    connection,
                    table,
                    rows_by_table[table],
                )
        return result

    def _prunable_rows(
        self,
        connection: sqlite3.Connection,
        table: str,
        cutoff_text: str,
    ) -> list[sqlite3.Row]:
        timestamp_expr = PRUNABLE_TABLES[table][0]
        return connection.execute(
            f"""SELECT * FROM {self._quote_identifier(table)}
                WHERE {timestamp_expr} < ?
                ORDER BY rowid""",
            (cutoff_text,),
        ).fetchall()

    def _delete_rows(
        self,
        connection: sqlite3.Connection,
        table: str,
        rows: list[sqlite3.Row],
    ) -> int:
        if not rows:
            return 0
        if table in {"audit_events", "virtual_lots"}:
            return connection.executemany(
                f"DELETE FROM {self._quote_identifier(table)} WHERE id=?",
                [(row["id"],) for row in rows],
            ).rowcount
        if table == "checkpoints":
            return connection.executemany(
                """DELETE FROM checkpoints
                   WHERE strategy_instance_id=? AND key=?""",
                [(row["strategy_instance_id"], row["key"]) for row in rows],
            ).rowcount
        if table == "virtual_positions":
            return connection.executemany(
                """DELETE FROM virtual_positions
                   WHERE strategy_instance_id=? AND symbol=?""",
                [(row["strategy_instance_id"], row["symbol"]) for row in rows],
            ).rowcount
        primary_keys = {
            "strategy_instances": "id",
            "strategy_runs": "id",
            "orders": "ticket_id",
            "fills": "id",
        }
        key = primary_keys[table]
        return connection.executemany(
            f"DELETE FROM {self._quote_identifier(table)} WHERE {self._quote_identifier(key)}=?",
            [(row[key],) for row in rows],
        ).rowcount

    def _archive_rows(
        self,
        archive_path: Path,
        rows_by_table: dict[str, list[sqlite3.Row]],
    ) -> None:
        archive_path = archive_path.expanduser()
        if archive_path.resolve() == self.path.resolve():
            raise ValueError("Archive path must be different from the active database")
        if archive_path.exists():
            raise FileExistsError(f"Refusing to overwrite archive {archive_path}")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive = sqlite3.connect(archive_path)
        try:
            archive.executescript(SCHEMA)
            archive.execute("PRAGMA foreign_keys=OFF")
            for table in ARCHIVE_TABLE_ORDER:
                rows = rows_by_table[table]
                if rows:
                    columns = rows[0].keys()
                    column_sql = ", ".join(self._quote_identifier(column) for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    archive.executemany(
                        f"""INSERT INTO {self._quote_identifier(table)}({column_sql})
                            VALUES ({placeholders})""",
                        [tuple(row[column] for column in columns) for row in rows],
                    )
            archive.commit()
            archive.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            archive.execute("PRAGMA journal_mode=DELETE")
        finally:
            archive.close()

    def vacuum(self) -> dict[str, object]:
        if not self.path.exists():
            return {
                "database": str(self.path),
                "database_exists": False,
                "vacuumed": False,
                "wal_checkpoint": None,
                "stats": self.database_stats(),
            }
        with self._lock, self.connect() as connection:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            connection.execute("VACUUM")
        return {
            "database": str(self.path),
            "database_exists": True,
            "vacuumed": True,
            "wal_checkpoint": {
                "busy": int(checkpoint[0]),
                "log_frames": int(checkpoint[1]),
                "checkpointed_frames": int(checkpoint[2]),
            },
            "stats": self.database_stats(),
        }

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
