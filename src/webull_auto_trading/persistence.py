from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from webull_auto_trading.domain import (
    ExecutionMode,
    OrderRole,
    OrderSide,
    OrderStatus,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperPosition,
    RuntimeMode,
    StrategyInstance,
    new_id,
    utc_now,
)

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
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    id TEXT PRIMARY KEY,
                    starting_balance REAL NOT NULL,
                    cash_balance REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    unrealized_pnl REAL NOT NULL DEFAULT 0,
                    current_equity REAL NOT NULL,
                    peak_equity REAL NOT NULL,
                    max_drawdown REAL NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    strategy_instance_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    average_price REAL NOT NULL,
                    market_price REAL,
                    unrealized_pnl REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, strategy_instance_id, symbol, side)
                );
                CREATE TABLE IF NOT EXISTS paper_account_events (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
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
        if self.get_setting("runtime_mode") is None:
            self.set_runtime_mode(RuntimeMode.TEST)
        self.ensure_paper_account()

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

    def get_runtime_mode(self) -> RuntimeMode:
        value = self.get_setting("runtime_mode", RuntimeMode.TEST.value)
        try:
            return RuntimeMode(value)
        except ValueError:
            return RuntimeMode.TEST

    def set_runtime_mode(self, mode: RuntimeMode | str) -> RuntimeMode:
        runtime_mode = RuntimeMode(mode)
        self.set_setting("runtime_mode", runtime_mode.value)
        return runtime_mode

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
        if mode != ExecutionMode.PAPER:
            raise ValueError("strategy instance mode must be paper")
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
            if current.mode != ExecutionMode.PAPER:
                raise ValueError("strategy instance mode must be paper")
        if "params" in changes:
            current.params = dict(changes["params"] or {})
        return self.upsert_strategy_instance(current)

    def list_table(self, table: str, limit: int = 200) -> list[dict[str, Any]]:
        if table not in {"orders", "fills", "cycles", "activity", "bars"}:
            raise ValueError(f"Unsupported table: {table}")
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ensure_paper_account(self, starting_balance: float = 10_000.0) -> PaperAccount:
        account = self.get_active_paper_account()
        if account is not None:
            return account
        account = PaperAccount(
            id=new_id("pa"),
            starting_balance=starting_balance,
            cash_balance=starting_balance,
            current_equity=starting_balance,
            peak_equity=starting_balance,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_accounts(
                    id, starting_balance, cash_balance, realized_pnl, unrealized_pnl,
                    current_equity, peak_equity, max_drawdown, active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    account.id,
                    account.starting_balance,
                    account.cash_balance,
                    account.realized_pnl,
                    account.unrealized_pnl,
                    account.current_equity,
                    account.peak_equity,
                    account.max_drawdown,
                    iso(account.created_at),
                    iso(account.updated_at),
                ),
            )
            conn.execute(
                """
                INSERT INTO paper_account_events(
                    id, account_id, event_type, amount, message, payload_json, created_at
                )
                VALUES (?, ?, 'reset', ?, 'Paper account created', ?, ?)
                """,
                (new_id("pae"), account.id, starting_balance, encode_json({}), iso()),
            )
        return account

    def get_active_paper_account(self) -> PaperAccount | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_accounts WHERE active = 1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._paper_account_from_row(row) if row else None

    def deposit_paper_account(self, amount: float) -> PaperAccount:
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        account = self.ensure_paper_account()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE paper_accounts
                SET starting_balance = starting_balance + ?,
                    cash_balance = cash_balance + ?,
                    current_equity = current_equity + ?,
                    peak_equity = max(peak_equity, current_equity + ?),
                    updated_at = ?
                WHERE id = ? AND active = 1
                """,
                (amount, amount, amount, amount, iso(), account.id),
            )
            conn.execute(
                """
                INSERT INTO paper_account_events(
                    id, account_id, event_type, amount, message, payload_json, created_at
                )
                VALUES (?, ?, 'deposit', ?, 'Paper account deposit', ?, ?)
                """,
                (new_id("pae"), account.id, amount, encode_json({}), iso()),
            )
        return self.recompute_paper_account()

    def reset_paper_account(self, starting_balance: float = 10_000.0) -> PaperAccount:
        with self.connect() as conn:
            conn.execute("DELETE FROM fills")
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM cycles")
            conn.execute("DELETE FROM paper_positions")
            conn.execute("DELETE FROM paper_account_events")
            conn.execute("DELETE FROM paper_accounts")
            conn.execute(
                """
                DELETE FROM activity
                WHERE event_type LIKE 'Paper%' OR event_type IN (?, ?, ?, ?, ?)
                """,
                (
                    "PlaceVirtualStop",
                    "CancelVirtualOrder",
                    "MoveStop",
                    "OpenAddOn",
                    "CloseCycle",
                ),
            )
        return self.ensure_paper_account(starting_balance)

    def list_paper_positions(self) -> list[PaperPosition]:
        account = self.ensure_paper_account()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE account_id = ? ORDER BY symbol, side",
                (account.id,),
            ).fetchall()
        return [self._paper_position_from_row(row) for row in rows]

    def list_paper_history(self, limit: int = 500) -> list[dict[str, Any]]:
        account = self.ensure_paper_account()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM paper_account_events
                WHERE account_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (account.id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "event_type": row["event_type"],
                "amount": row["amount"],
                "message": row["message"],
                "payload": decode_json(row["payload_json"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_paper_orders(self) -> list[PaperOrder]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY updated_at, id").fetchall()
        return [self._paper_order_from_row(row) for row in rows]

    def list_paper_fills(self) -> list[PaperFill]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM fills ORDER BY filled_at, id").fetchall()
        return [self._paper_fill_from_row(row) for row in rows]

    def sync_paper_state(
        self,
        orders: Iterable[PaperOrder],
        fills: Iterable[PaperFill],
        *,
        market_prices: dict[str, float] | None = None,
    ) -> None:
        order_list = list(orders)
        fill_list = list(fills)
        with self.connect() as conn:
            for order in order_list:
                conn.execute(
                    """
                    INSERT INTO orders(
                        id, strategy_instance_id, cycle_id, symbol, side, role, status, quantity,
                        stop_price, fill_price, stop_loss, take_profit, parent_order_id,
                        opened_at, closed_at, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status,
                        fill_price=excluded.fill_price,
                        stop_loss=excluded.stop_loss,
                        take_profit=excluded.take_profit,
                        opened_at=excluded.opened_at,
                        closed_at=excluded.closed_at,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        order.id,
                        order.strategy_instance_id,
                        order.cycle_id,
                        order.symbol,
                        order.side.value,
                        order.role.value if isinstance(order.role, OrderRole) else str(order.role),
                        order.status.value,
                        order.quantity,
                        order.stop_price,
                        order.fill_price,
                        order.stop_loss,
                        order.take_profit,
                        order.parent_order_id,
                        iso(order.opened_at) if order.opened_at else None,
                        iso(order.closed_at) if order.closed_at else None,
                        encode_json(order.metadata),
                        iso(),
                    ),
                )
            for fill in fill_list:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO fills(
                        id, order_id, strategy_instance_id, cycle_id, symbol, side,
                        quantity, price, filled_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.id,
                        fill.order_id,
                        fill.strategy_instance_id,
                        fill.cycle_id,
                        fill.symbol,
                        fill.side.value,
                        fill.quantity,
                        fill.price,
                        iso(fill.filled_at),
                    ),
                )
            self._sync_cycles(conn, order_list)
        self.recompute_paper_account(market_prices=market_prices)

    def recompute_paper_account(
        self,
        *,
        market_prices: dict[str, float] | None = None,
    ) -> PaperAccount:
        account = self.ensure_paper_account()
        orders = self.list_paper_orders()
        prices = market_prices or {}
        cash = account.starting_balance
        realized = 0.0
        open_positions: dict[tuple[str, str, str], dict[str, Any]] = {}
        for order in orders:
            if (
                order.status not in (OrderStatus.FILLED, OrderStatus.CLOSED)
                or order.fill_price is None
            ):
                continue
            multiplier = 1 if order.side == OrderSide.BUY else -1
            cash -= multiplier * order.quantity * order.fill_price
            if order.status == OrderStatus.CLOSED:
                close_price = _float_or_none(order.metadata.get("close_price")) or order.fill_price
                cash += multiplier * order.quantity * close_price
                realized += multiplier * order.quantity * (close_price - order.fill_price)
                continue
            key = (order.strategy_instance_id, order.symbol, order.side.value)
            position = open_positions.setdefault(
                key,
                {
                    "quantity": 0.0,
                    "cost": 0.0,
                    "side": order.side,
                    "strategy_instance_id": order.strategy_instance_id,
                    "symbol": order.symbol,
                },
            )
            position["quantity"] += order.quantity
            position["cost"] += order.quantity * order.fill_price
        positions: list[PaperPosition] = []
        unrealized = 0.0
        with self.connect() as conn:
            conn.execute("DELETE FROM paper_positions WHERE account_id = ?", (account.id,))
            for position in open_positions.values():
                quantity = float(position["quantity"])
                average_price = float(position["cost"]) / quantity if quantity else 0.0
                market_price = prices.get(position["symbol"])
                side = position["side"]
                pnl = 0.0
                if market_price is not None:
                    multiplier = 1 if side == OrderSide.BUY else -1
                    pnl = multiplier * quantity * (market_price - average_price)
                unrealized += pnl
                paper_position = PaperPosition(
                    id=new_id("pp"),
                    account_id=account.id,
                    strategy_instance_id=position["strategy_instance_id"],
                    symbol=position["symbol"],
                    side=side,
                    quantity=quantity,
                    average_price=average_price,
                    market_price=market_price,
                    unrealized_pnl=pnl,
                )
                positions.append(paper_position)
                conn.execute(
                    """
                    INSERT INTO paper_positions(
                        id, account_id, strategy_instance_id, symbol, side, quantity,
                        average_price, market_price, unrealized_pnl, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_position.id,
                        paper_position.account_id,
                        paper_position.strategy_instance_id,
                        paper_position.symbol,
                        paper_position.side.value,
                        paper_position.quantity,
                        paper_position.average_price,
                        paper_position.market_price,
                        paper_position.unrealized_pnl,
                        iso(paper_position.updated_at),
                    ),
                )
            current_equity = cash + sum(
                position.quantity
                * (1 if position.side == OrderSide.BUY else -1)
                * (position.market_price or position.average_price)
                for position in positions
            )
            peak_equity = max(account.peak_equity, current_equity)
            max_drawdown = max(account.max_drawdown, peak_equity - current_equity)
            conn.execute(
                """
                UPDATE paper_accounts
                SET cash_balance = ?, realized_pnl = ?, unrealized_pnl = ?,
                    current_equity = ?, peak_equity = ?, max_drawdown = ?, updated_at = ?
                WHERE id = ? AND active = 1
                """,
                (
                    cash,
                    realized,
                    unrealized,
                    current_equity,
                    peak_equity,
                    max_drawdown,
                    iso(),
                    account.id,
                ),
            )
        updated = self.get_active_paper_account()
        assert updated is not None
        return updated

    def storage_stats(self) -> dict[str, Any]:
        tables = [
            "settings",
            "strategy_instances",
            "quote_snapshots",
            "bars",
            "orders",
            "fills",
            "cycles",
            "paper_accounts",
            "paper_positions",
            "paper_account_events",
            "activity",
        ]
        with self.connect() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
                for table in tables
            }
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return {"tables": counts, "database_bytes": page_count * page_size}

    def cleanup(
        self,
        *,
        dry_run: bool = True,
        activity_days: int = 30,
        paper_history_days: int = 365,
        closed_cycle_days: int = 365,
        vacuum: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        activity_cutoff = iso(now - timedelta(days=activity_days))
        paper_cutoff = iso(now - timedelta(days=paper_history_days))
        cycle_cutoff = iso(now - timedelta(days=closed_cycle_days))
        summary: dict[str, int | bool] = {"dry_run": dry_run, "vacuum": vacuum}
        statements = {
            "quote_snapshots": ("DELETE FROM quote_snapshots", ()),
            "bars": ("DELETE FROM bars", ()),
            "activity_old": (
                """
                DELETE FROM activity
                WHERE ts < ?
                AND id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 50000)
                """,
                (activity_cutoff,),
            ),
            "paper_history_old": (
                "DELETE FROM paper_account_events WHERE created_at < ?",
                (paper_cutoff,),
            ),
            "closed_orders_old": (
                "DELETE FROM orders WHERE status IN ('CANCELLED', 'CLOSED') AND closed_at < ?",
                (paper_cutoff,),
            ),
            "fills_old": (
                """
                DELETE FROM fills
                WHERE filled_at < ?
                AND order_id NOT IN (SELECT id FROM orders WHERE status IN ('PENDING', 'FILLED'))
                """,
                (paper_cutoff,),
            ),
            "closed_cycles_old": (
                "DELETE FROM cycles WHERE status != 'OPEN' AND closed_at < ?",
                (cycle_cutoff,),
            ),
        }
        with self.connect() as conn:
            for name, (sql, params) in statements.items():
                if dry_run:
                    count_sql = (
                        "SELECT COUNT(*) AS count FROM ("
                        + sql.replace("DELETE FROM", "SELECT * FROM", 1)
                        + ")"
                    )
                    summary[name] = conn.execute(count_sql, params).fetchone()["count"]
                else:
                    before = conn.total_changes
                    conn.execute(sql, params)
                    summary[name] = conn.total_changes - before
            if not dry_run:
                conn.execute(
                    """
                    INSERT INTO activity(
                        ts, level, strategy_instance_id, symbol, event_type, message, payload_json
                    )
                    VALUES (?, 'info', NULL, NULL, 'StorageCleanup', 'Storage cleanup completed', ?)
                    """,
                    (iso(), encode_json(summary)),
                )
        if vacuum and not dry_run:
            with self.connect() as conn:
                conn.execute("VACUUM")
        return summary

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

    def replace_strategy_instances(self, instances: Iterable[StrategyInstance]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM strategy_instances")
        self.seed_strategy_instances(instances)

    def _sync_cycles(self, conn: sqlite3.Connection, orders: list[PaperOrder]) -> None:
        by_cycle: dict[str, list[PaperOrder]] = {}
        for order in orders:
            by_cycle.setdefault(order.cycle_id, []).append(order)
        for cycle_id, cycle_orders in by_cycle.items():
            statuses = {order.status for order in cycle_orders}
            status = "OPEN" if statuses & {OrderStatus.PENDING, OrderStatus.FILLED} else "COMPLETED"
            opened_at = min(
                (
                    order.opened_at or order.closed_at or utc_now()
                    for order in cycle_orders
                ),
                default=utc_now(),
            )
            closed_at = max(
                (
                    order.closed_at
                    for order in cycle_orders
                    if order.closed_at is not None
                ),
                default=None,
            )
            realized = 0.0
            for order in cycle_orders:
                if (
                    order.status == OrderStatus.CLOSED
                    and order.fill_price is not None
                    and _float_or_none(order.metadata.get("close_price")) is not None
                ):
                    close_price = _float_or_none(order.metadata.get("close_price"))
                    assert close_price is not None
                    multiplier = 1 if order.side == OrderSide.BUY else -1
                    realized += multiplier * order.quantity * (close_price - order.fill_price)
            sample = cycle_orders[0]
            conn.execute(
                """
                INSERT INTO cycles(
                    id, strategy_instance_id, symbol, status, opened_at, closed_at,
                    realized_pnl, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    closed_at=excluded.closed_at,
                    realized_pnl=excluded.realized_pnl,
                    metadata_json=excluded.metadata_json
                """,
                (
                    cycle_id,
                    sample.strategy_instance_id,
                    sample.symbol,
                    status,
                    iso(opened_at),
                    iso(closed_at) if closed_at else None,
                    realized,
                    encode_json({"order_count": len(cycle_orders)}),
                ),
            )

    @staticmethod
    def _paper_order_from_row(row: sqlite3.Row) -> PaperOrder:
        return PaperOrder(
            id=row["id"],
            strategy_instance_id=row["strategy_instance_id"],
            cycle_id=row["cycle_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            role=OrderRole(row["role"]),
            quantity=row["quantity"],
            status=OrderStatus(row["status"]),
            stop_price=row["stop_price"],
            fill_price=row["fill_price"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            parent_order_id=row["parent_order_id"],
            opened_at=parse_dt(row["opened_at"]) if row["opened_at"] else None,
            closed_at=parse_dt(row["closed_at"]) if row["closed_at"] else None,
            metadata=decode_json(row["metadata_json"], {}),
        )

    @staticmethod
    def _paper_fill_from_row(row: sqlite3.Row) -> PaperFill:
        return PaperFill(
            id=row["id"],
            order_id=row["order_id"],
            strategy_instance_id=row["strategy_instance_id"],
            cycle_id=row["cycle_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            quantity=row["quantity"],
            price=row["price"],
            filled_at=parse_dt(row["filled_at"]),
        )

    @staticmethod
    def _paper_account_from_row(row: sqlite3.Row) -> PaperAccount:
        return PaperAccount(
            id=row["id"],
            starting_balance=row["starting_balance"],
            cash_balance=row["cash_balance"],
            realized_pnl=row["realized_pnl"],
            unrealized_pnl=row["unrealized_pnl"],
            current_equity=row["current_equity"],
            peak_equity=row["peak_equity"],
            max_drawdown=row["max_drawdown"],
            created_at=parse_dt(row["created_at"]),
            updated_at=parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _paper_position_from_row(row: sqlite3.Row) -> PaperPosition:
        return PaperPosition(
            id=row["id"],
            account_id=row["account_id"],
            strategy_instance_id=row["strategy_instance_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            quantity=row["quantity"],
            average_price=row["average_price"],
            market_price=row["market_price"],
            unrealized_pnl=row["unrealized_pnl"],
            updated_at=parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _instance_from_row(row: sqlite3.Row) -> StrategyInstance:
        try:
            mode = ExecutionMode(row["mode"])
        except ValueError:
            mode = ExecutionMode.PAPER
        return StrategyInstance(
            id=row["id"],
            strategy_name=row["strategy_name"],
            symbol=row["symbol"],
            account_id=row["account_id"],
            enabled=bool(row["enabled"]),
            mode=mode,
            params=decode_json(row["params_json"], {}),
            created_at=parse_dt(row["created_at"]),
            updated_at=parse_dt(row["updated_at"]),
        )


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
