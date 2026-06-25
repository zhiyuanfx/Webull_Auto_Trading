# Strategy Plugin Agent Guide

This guide applies to every plugin under `strategies/`.

## Contract

Each plugin has `strategy.yaml`, `strategy.py`, optional `requirements.lock`, and tests.
The manifest ID is stable, the version changes for every behavioral edit, and parameter
defaults/schema are source-controlled. Running instances never hot-reload.

Subclass `BaseStrategy` and use only the injected context. Strategies actively manage their
own tickets with `context.orders.place`, `replace`, and `cancel`. They must not import
`strategy_desk.gateway`, the Webull SDK, persistence code, or read credential environment
variables. They never inspect another strategy's orders or virtual positions.

```python
ticket = await self.context.orders.place(
    symbol="MGCQ6",
    asset_class=AssetClass.FUTURES,
    side=Side.BUY,
    quantity=Decimal("1"),
    order_type=OrderType.LIMIT,
    limit_price=Decimal("2412.30"),
)
await self.context.orders.cancel(ticket["id"])
```

Worker responses are JSON-compatible ticket snapshots. Treat every acknowledgement as
asynchronous: submission is not a fill, partial fills can repeat, and unknown state requires
reconciliation. Call `await context.orders.tickets()` during startup when restoring local
ticket coordination. Generate no credential, broker, or database side effects.

## Time, numbers, and lifecycle

- Use `Decimal` for prices, quantities, money, and P&L; never binary floats.
- Persist timestamps in UTC and declare schedules with an IANA timezone.
- A schedule blocks new exposure outside its window but still permits closing and cancelling.
- A logical futures product and concrete monthly contract are different values. Orders always
  use the concrete symbol delivered by the runtime.
- Implement `on_order_update` idempotently. Store durable strategy state with checkpoints.
- On shutdown, stop producing commands promptly; do not assume working orders are cancelled.
- Never bypass expiry, risk, session, idempotency, or emergency gateway decisions.

## Acceptance checklist

- Manifest and entrypoint validate with `uv run strategy-desk validate-strategies`.
- Default parameters do not accidentally submit orders.
- Unit/replay tests cover signals, partial fills, rejection, restart, schedule boundaries, and
  fixed/auto-roll events where applicable.
- English and Simplified Chinese names/descriptions are present.
- Logs contain decisions and ticket IDs but no secrets or full account identifiers.
