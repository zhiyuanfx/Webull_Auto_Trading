from __future__ import annotations

from typing import Any, Protocol

from strategy_desk.order_session import OrderSession


class StrategyContext(Protocol):
    """Credential-free contract implemented by the worker's IPC context."""

    instance_id: str
    orders: OrderSession

    async def checkpoint(self, key: str, value: object) -> None: ...

    async def restore(self, key: str, default: object = None) -> object: ...

    async def log(self, level: str, message: str, **fields: Any) -> None: ...


class BaseStrategy:
    async def on_start(self, context: StrategyContext) -> None:  # noqa: B027
        pass

    async def on_tick(self, event: object) -> None:  # noqa: B027
        pass

    async def on_quote(self, event: object) -> None:  # noqa: B027
        pass

    async def on_timer(self, event: object) -> None:  # noqa: B027
        pass

    async def on_order_update(self, event: object) -> None:  # noqa: B027
        pass

    async def on_window_open(self, event: object) -> None:  # noqa: B027
        pass

    async def on_window_close(self, event: object) -> None:  # noqa: B027
        pass

    async def on_contract_expiring(self, event: object) -> None:  # noqa: B027
        pass

    async def on_contract_rolled(self, event: object) -> None:  # noqa: B027
        pass

    async def on_stop(self, reason: str) -> None:  # noqa: B027
        pass
