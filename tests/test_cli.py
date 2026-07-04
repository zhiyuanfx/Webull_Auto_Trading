from pathlib import Path

from webull_auto_trading.cli import diagnose_payload, redact, summarize_accounts
from webull_auto_trading.config import Settings


def test_summarize_accounts_finds_unique_account_id() -> None:
    payload = {
        "data": [
            {
                "accountId": "acct-1",
                "accountType": "MARGIN",
                "status": "ACTIVE",
                "accessToken": "secret-token",
            }
        ]
    }

    result = summarize_accounts(payload)

    assert result["account_id_candidates"] == ["acct-1"]
    assert result["account_count"] == 1
    assert result["copy_this_to_webull_account_id"] == "acct-1"
    assert result["accounts"] == [
        {"account_id": "acct-1", "accountType": "MARGIN", "status": "ACTIVE"}
    ]


def test_summarize_accounts_handles_multiple_candidates() -> None:
    result = summarize_accounts(
        {"accounts": [{"account_id": "acct-2"}, {"accountId": "acct-1"}]}
    )

    assert result["account_id_candidates"] == ["acct-1", "acct-2"]
    assert result["copy_this_to_webull_account_id"] is None


def test_redact_removes_sensitive_nested_values() -> None:
    payload = {
        "appSecret": "top-secret",
        "nested": [{"access_token": "token"}, {"safe": "value"}],
        "account_id": "acct-1",
    }

    assert redact(payload) == {
        "appSecret": "<redacted>",
        "nested": [{"access_token": "<redacted>"}, {"safe": "value"}],
        "account_id": "acct-1",
    }


def test_diagnose_payload_does_not_expose_credentials(tmp_path: Path) -> None:
    settings = Settings(
        webull_prod_app_key="app-key",
        webull_prod_app_secret="app-secret",
        webull_token_dir=tmp_path,
        webull_account_id="acct-1",
        _env_file=None,
    )

    payload = diagnose_payload(settings)

    assert payload["webull_configured"] is True
    assert payload["webull_account_id_configured"] is True
    assert payload["token_dir"] == str(tmp_path / "live")
    assert "app-key" not in str(payload)
    assert "app-secret" not in str(payload)
    assert "acct-1" not in str(payload)
