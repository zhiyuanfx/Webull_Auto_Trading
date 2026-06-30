# Webull Bridge

Local TradingView-to-Webull bridge. TradingView/Pine Script owns market data, strategy
logic, and alert generation. This app receives TradingView JSON webhooks, validates a
shared secret, stores the event locally, and executes matching live Webull orders.

There is no local simulator, no UAT mode, no strategy worker runtime, and no Webull market
data stream in this rebuild. All normal tests are offline and mocked; live order submission
only happens through the running bridge when the global and route safety switches allow it.

## Quick Start

Use the existing Conda environment that already works with the Webull SDK:

```bash
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
cp .env.example .env
webull-bridge init-db
webull-bridge serve --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. TradingView must send webhooks to a public HTTPS URL that
forwards to the local FastAPI server. The app does not manage Cloudflare, AWS, or tunnels.

## Webhook Payload

Send JSON to:

```text
POST /webhook/tradingview/{route_id}
```

Example:

```json
{
  "secret": "route-shared-secret",
  "event_id": "my-strategy-2026-06-30T14:30:00Z-1",
  "action": "BUY",
  "symbol": "1OZ",
  "quantity": "1",
  "order_type": "MARKET",
  "strategy": "Example Pine Strategy",
  "alert": "long-entry",
  "timeframe": "1"
}
```

Defaults are equity, US market, quantity order, regular session, market order, and DAY
time-in-force. Supported actions are `BUY`, `SELL`, `CANCEL`, `REPLACE`, and `FLATTEN`.
`LIMIT` requires `limit_price`; `STOP_LOSS` requires `stop_price`; `STOP_LOSS_LIMIT`
requires both.

Secrets are checked at intake and never stored in the local history. Duplicate
`event_id` values per route are accepted as duplicates and do not create a second order.

## Operations

```bash
webull-bridge diagnose
webull-bridge db-stats
webull-bridge db-vacuum
webull-bridge ensure-route tv --account-id YOUR_ACCOUNT --secret YOUR_SECRET
```

The UI provides:

- Dashboard: health, route state, global execution switch, and emergency pause.
- Webhook Route: route settings, secret rotation, allowed symbols, and sample payload.
- Orders: webhook/order history with sanitized payloads and Webull responses.
- Positions: Webull positions refreshed on demand.
- Activity: local audit trail.
- Settings: database, token cache, and local tunnel guidance.

## Verification

Normal verification must not call Webull:

```bash
python -m pytest
ruff check .
cd frontend && npm run build
```

For Webull behavior, use the current official documentation from
<https://developer.webull.com/apis/>, especially the Trading API order preview, place,
replace, cancel, open-order, position, SDK, authentication, and token pages.
