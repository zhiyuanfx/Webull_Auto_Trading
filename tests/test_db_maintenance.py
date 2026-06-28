from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

import pytest

from strategy_desk.cli import main, parse_retention_window
from strategy_desk.config import get_settings
from strategy_desk.persistence import PRUNABLE_TABLES, Ledger


def insert_old_and_recent_runtime_rows(ledger: Ledger) -> None:
    old = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with ledger.connect() as connection:
        for suffix, timestamp in (("old", old), ("recent", recent)):
            connection.execute(
                """INSERT INTO strategy_instances(
                     id, plugin_id, plugin_version, plugin_source_hash, mode, account_id,
                     feed_source, state, config_json, created_at, updated_at
                   ) VALUES (?, 'plugin', '1', 'hash', 'LOCAL_SIM', 'SIM-1',
                     'REPLAY', 'STOPPED', '{}', ?, ?)""",
                (f"instance-{suffix}", timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO strategy_runs(
                     id, strategy_instance_id, mode, account_id, feed_source, status,
                     started_at, ended_at
                   ) VALUES (?, ?, 'LOCAL_SIM', 'SIM-1', 'REPLAY', 'STOPPED', ?, ?)""",
                (f"run-{suffix}", f"instance-{suffix}", timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO orders(
                     ticket_id, strategy_instance_id, account_id, client_order_id,
                     broker_order_id, symbol, status, origin, command_json, ticket_json,
                     created_at, updated_at
                   ) VALUES (?, ?, 'SIM-1', ?, NULL, 'AAPL', 'FILLED', 'STRATEGY',
                     '{}', '{}', ?, ?)""",
                (
                    f"ticket-{suffix}",
                    f"instance-{suffix}",
                    f"client-{suffix}",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO fills(
                     id, ticket_id, strategy_instance_id, symbol, quantity, price,
                     commission, payload_json, filled_at
                   ) VALUES (?, ?, ?, 'AAPL', '1', '100', '0', '{}', ?)""",
                (f"fill-{suffix}", f"ticket-{suffix}", f"instance-{suffix}", timestamp),
            )
            connection.execute(
                """INSERT INTO virtual_positions(
                     strategy_instance_id, symbol, quantity, average_price,
                     realized_pnl, updated_at
                   ) VALUES (?, 'AAPL', '0', '0', '0', ?)""",
                (f"instance-{suffix}", timestamp),
            )
            connection.execute(
                """INSERT INTO virtual_lots(
                     strategy_instance_id, symbol, signed_quantity, entry_price, opened_at
                   ) VALUES (?, 'AAPL', '1', '100', ?)""",
                (f"instance-{suffix}", timestamp),
            )
            connection.execute(
                """INSERT INTO checkpoints(strategy_instance_id, key, value_json, updated_at)
                   VALUES (?, 'state', '{}', ?)""",
                (f"instance-{suffix}", timestamp),
            )
            connection.execute(
                """INSERT INTO audit_events(kind, strategy_instance_id, payload_json, created_at)
                   VALUES (?, ?, '{}', ?)""",
                (f"EVENT_{suffix.upper()}", f"instance-{suffix}", timestamp),
            )


def test_database_stats_does_not_create_missing_database(tmp_path):
    database = tmp_path / "missing.sqlite3"

    stats = Ledger(database).database_stats()

    assert stats["database"] == str(database)
    assert stats["database_exists"] is False
    assert stats["file_size_bytes"] is None
    assert stats["tables"] == {}
    assert not database.exists()


def test_database_stats_includes_row_counts_and_wal_sizes(tmp_path):
    database = tmp_path / "stats.sqlite3"
    ledger = Ledger(database)
    ledger.initialize()
    ledger.audit("OLD_EVENT", {"detail": "redacted"}, "strategy-a")

    stats = ledger.database_stats()

    assert stats["database_exists"] is True
    assert stats["file_size_bytes"] is not None
    assert "wal_size_bytes" in stats
    assert "shm_size_bytes" in stats
    assert stats["tables"]["audit_events"] == 1
    assert stats["tables"]["orders"] == 0


def test_prune_database_defaults_to_dry_run(tmp_path):
    database = tmp_path / "dry-run.sqlite3"
    ledger = Ledger(database)
    ledger.initialize()
    insert_old_and_recent_runtime_rows(ledger)

    result = ledger.prune_database(datetime.now(UTC) - timedelta(days=90))

    assert result["dry_run"] is True
    assert result["scope"] == "local_runtime_tables"
    assert set(result["tables"]) == set(PRUNABLE_TABLES)
    assert all(
        item == {"matched": 1, "archived": 0, "deleted": 0}
        for item in result["tables"].values()
    )
    stats = ledger.database_stats()["tables"]
    assert all(stats[table] == 2 for table in PRUNABLE_TABLES)


def test_prune_database_can_archive_before_delete(tmp_path):
    database = tmp_path / "archive.sqlite3"
    archive = tmp_path / "archives" / "strategy_desk_20260628.sqlite3"
    ledger = Ledger(database)
    ledger.initialize()
    insert_old_and_recent_runtime_rows(ledger)

    result = ledger.prune_database(
        datetime.now(UTC) - timedelta(days=90),
        execute=True,
        archive_path=archive,
    )

    assert result["dry_run"] is False
    assert all(
        item == {"matched": 1, "archived": 1, "deleted": 1}
        for item in result["tables"].values()
    )
    assert result["archive"] == {
        "path": str(archive),
        "created": True,
        "rows_archived": len(PRUNABLE_TABLES),
    }
    stats = ledger.database_stats()["tables"]
    assert all(stats[table] == 1 for table in PRUNABLE_TABLES)
    with sqlite3.connect(archive) as connection:
        archived_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in PRUNABLE_TABLES
        }
        archived_order = connection.execute("SELECT ticket_id FROM orders").fetchone()[0]
    assert all(count == 1 for count in archived_counts.values())
    assert archived_order == "ticket-old"


def test_parse_retention_window_accepts_hours_and_days():
    assert parse_retention_window("12h") == timedelta(hours=12)
    assert parse_retention_window("90d") == timedelta(days=90)
    with pytest.raises(Exception, match="retention window"):
        parse_retention_window("3w")


def test_vacuum_runs_explicit_checkpoint_and_returns_stats(tmp_path):
    database = tmp_path / "vacuum.sqlite3"
    ledger = Ledger(database)
    ledger.initialize()
    ledger.audit("VACUUM_EVENT", {}, "strategy-a")

    result = ledger.vacuum()

    assert result["database"] == str(database)
    assert result["database_exists"] is True
    assert result["vacuumed"] is True
    assert result["wal_checkpoint"]["busy"] == 0
    assert result["stats"]["tables"]["audit_events"] == 1


def test_db_stats_cli_outputs_json(tmp_path, monkeypatch, capsys):
    database = tmp_path / "cli.sqlite3"
    ledger = Ledger(database)
    ledger.initialize()
    ledger.audit("CLI_EVENT", {}, "strategy-a")
    monkeypatch.setenv("STRATEGY_DESK_DB_PATH", str(database))
    monkeypatch.setattr(sys, "argv", ["strategy-desk", "db-stats"])
    get_settings.cache_clear()

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["database"] == str(database)
    assert payload["tables"]["audit_events"] == 1
    get_settings.cache_clear()
