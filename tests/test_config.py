from pathlib import Path

from webull_auto_trading.config import Settings


def test_settings_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)

    assert settings.webull_region == "us"
    assert settings.webull_prod_app_key == ""
    assert settings.webull_prod_app_secret == ""
    assert settings.webull_token_dir == Path(".runtime/webull_tokens")
    assert settings.webull_prod_token_wait_seconds == 300
    assert settings.webull_account_id == ""
    assert settings.runtime_db_path == Path(".runtime/webull_auto_trading.sqlite3")
    assert settings.strategies_config_path == Path("config/strategies.yml")
    assert settings.quote_max_staleness_seconds == 30
    assert settings.allow_delayed_quotes is False
    assert settings.production_configured is False


def test_settings_reports_production_configured() -> None:
    settings = Settings(
        webull_prod_app_key="key",
        webull_prod_app_secret="secret",
        _env_file=None,
    )

    assert settings.production_configured is True
