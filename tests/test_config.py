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
    assert settings.webull_account_default_alias == ""
    assert settings.webull_account_aliases() == {}
    assert settings.live_execution_master_enable is False
    assert settings.runtime_db_path == Path(".runtime/webull_auto_trading.sqlite3")
    assert settings.strategies_test_config_path == Path("config/strategies.test.yml")
    assert settings.strategies_live_config_path == Path("config/strategies.live.yml")
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


def test_settings_resolves_named_webull_account_alias() -> None:
    settings = Settings(
        webull_account_default_alias="stock_margin",
        webull_account_stock_cash_id="cash-id",
        webull_account_stock_margin_id="margin-id",
        _env_file=None,
    )

    assert settings.webull_account_aliases() == {
        "stock_cash": "cash-id",
        "stock_margin": "margin-id",
    }
    assert settings.resolve_webull_account_alias("stock_cash") == "cash-id"
    assert settings.resolve_webull_account_alias("") == "margin-id"


def test_settings_falls_back_to_legacy_account_id_when_default_alias_missing() -> None:
    settings = Settings(
        webull_account_default_alias="stock_margin",
        webull_account_id="legacy-id",
        _env_file=None,
    )

    assert settings.resolve_webull_account_alias("") == "legacy-id"
