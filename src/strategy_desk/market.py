from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from strategy_desk.domain import MarketQuote
from strategy_desk.gateway import ExecutionGateway


class MarketDataHub:
    def __init__(self, gateway: ExecutionGateway) -> None:
        self.gateway = gateway
        self.last_quotes: dict[str, MarketQuote] = {}
        self._subscribers: set[asyncio.Queue[MarketQuote]] = set()

    async def publish(self, quote: MarketQuote) -> None:
        self.last_quotes[quote.symbol] = quote
        await self.gateway.update_quote(quote)
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            await queue.put(quote)

    async def subscribe(self) -> AsyncIterator[MarketQuote]:
        queue: asyncio.Queue[MarketQuote] = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def replay(self, path: Path, speed: float = 1.0, on_quote=None) -> None:
        previous = None
        contents = await asyncio.to_thread(path.read_text)
        for line in contents.splitlines():
            quote = MarketQuote.model_validate(json.loads(line)).model_copy(
                update={"received_at": datetime.now(UTC)}
            )
            if previous is not None:
                delay = (quote.source_at - previous).total_seconds() / max(speed, 0.001)
                await asyncio.sleep(max(0, delay))
            await self.publish(quote)
            if on_quote:
                await on_quote(quote)
            previous = quote.source_at
