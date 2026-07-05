from webull_auto_trading.domain import ExecutionMode
from webull_auto_trading.persistence import RuntimeRepository


def test_sqlite_strategy_instances_survive_repository_restart(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repo = RuntimeRepository(db_path)
    repo.init_db()
    created = repo.create_strategy_instance(
        {
            "id": "st-1",
            "strategy_name": "day_many_bian",
            "symbol": "NASDAQ:AAPL",
            "mode": "paper",
            "params": {"lots": 0.5},
        }
    )

    restarted = RuntimeRepository(db_path)
    instances = restarted.list_strategy_instances()

    assert created.id == "st-1"
    assert len(instances) == 1
    assert instances[0].mode == ExecutionMode.PAPER
    assert instances[0].params == {"lots": 0.5}
