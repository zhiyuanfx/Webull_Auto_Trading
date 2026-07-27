import asyncio

from webull_auto_trading.config import Settings
from webull_auto_trading.webull import WebullError, WebullTradingClient


def test_safe_error_message_redacts_webull_credentials() -> None:
    settings = Settings(
        webull_prod_app_key="app-key",
        webull_prod_app_secret="app-secret",
        _env_file=None,
    )
    client = WebullTradingClient(settings)

    message = client.safe_error_message(Exception("failed for app-key with app-secret"))

    assert message == "failed for <redacted> with <redacted>"


def test_require_success_raises_safe_webull_error() -> None:
    class Response:
        status_code = 400

        @staticmethod
        def json() -> dict[str, str]:
            return {"error_code": "BAD_REQUEST", "message": "Request failed"}

    try:
        WebullTradingClient._require_success(Response())
    except WebullError as exc:
        assert exc.code == "BAD_REQUEST"
        assert exc.message == "Request failed"
    else:
        raise AssertionError("Expected WebullError")


def test_account_balance_uses_official_sdk_account_method() -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"total_net_liquidation_value": "100.00"}

    class AccountV2:
        def get_account_balance(self, account_id: str) -> Response:
            assert account_id == "acct-1"
            return Response()

    client = WebullTradingClient(Settings(_env_file=None))
    client._client = type("TradeClient", (), {"account_v2": AccountV2()})()

    result = asyncio.run(client.account_balance("acct-1"))

    assert result == {"total_net_liquidation_value": "100.00"}


def test_order_history_uses_official_sdk_order_method() -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json() -> list[dict[str, str]]:
            return [{"client_order_id": "client-1"}]

    class OrderV3:
        def get_order_history(self, account_id: str, page_size: int) -> Response:
            assert account_id == "acct-1"
            assert page_size == 100
            return Response()

    client = WebullTradingClient(Settings(_env_file=None))
    client._client = type("TradeClient", (), {"order_v3": OrderV3()})()

    result = asyncio.run(client.order_history("acct-1"))

    assert result == [{"client_order_id": "client-1"}]


def test_preview_and_order_detail_use_official_sdk_methods() -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "FILLED"}

    class OrderV3:
        def preview_order(self, account_id: str, orders: list[dict]) -> Response:
            assert account_id == "acct-1"
            assert orders[0]["symbol"] == "MGCQ6"
            return Response()

        def get_order_detail(
            self,
            account_id: str,
            client_order_id: str,
        ) -> Response:
            assert account_id == "acct-1"
            assert client_order_id == "client-1"
            return Response()

    client = WebullTradingClient(Settings(_env_file=None))
    client._client = type("TradeClient", (), {"order_v3": OrderV3()})()

    preview = asyncio.run(client.preview_order("acct-1", {"symbol": "MGCQ6"}))
    detail = asyncio.run(client.order_detail("acct-1", "client-1"))

    assert preview == {"status": "FILLED"}
    assert detail == {"status": "FILLED"}
