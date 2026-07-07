# Webull Auto Trading

Python foundation for a local InsightSentry plus Webull trading console. The old
external-alert bridge has been removed and replaced with Python-native strategy instances,
SQLite persistence, paper virtual orders, a FastAPI backend, and a React/Vite operator
console.

Runtime mode is global: `test` mode paper trades against real InsightSentry market data, and
`live` mode allows Webull account/balance/position/open-order reads through the official SDK.
Live order placement, replacement, cancellation, and flattening are not implemented.

## Quick Start

Use the existing Conda environment that already works with the Webull SDK:

```bash
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
cp .env.example .env
webull-auto-trading diagnose
webull-auto-trading init-db
```

Discover Webull account id candidates from the authenticated SDK session:

```bash
webull-auto-trading accounts
```

Use `webull-auto-trading accounts --raw` only when you need the redacted raw account-list
response for troubleshooting. Sensitive keys such as tokens, app keys, app secrets,
passwords, and authorization fields are replaced with `<redacted>`.

## Configuration

`.env` is ignored and should contain only local secrets and machine-specific settings:

```text
WEBULL_REGION=us
WEBULL_PROD_APP_KEY=
WEBULL_PROD_APP_SECRET=
WEBULL_TOKEN_DIR=.runtime/webull_tokens
WEBULL_PROD_TOKEN_WAIT_SECONDS=300
WEBULL_ACCOUNT_ID=
```

SDK tokens are stored under `.runtime/webull_tokens/live`, which is ignored by git. Do not
commit `.env`, `.runtime/`, Webull logs, account secrets, access tokens, or copied raw API
payloads containing secrets.

## Commands

```bash
webull-auto-trading diagnose
webull-auto-trading accounts
webull-auto-trading accounts --raw
webull-auto-trading init-db
webull-auto-trading serve --reload
webull-auto-trading run
webull-auto-trading cleanup --dry-run
```

`serve` starts the local backend and InsightSentry quote stream worker on
<http://127.0.0.1:8765> by default. The API remains usable if stream credentials are missing
or the stream is reconnecting. The frontend lives in `frontend/` and proxies `/api` to that
backend during Vite development.

```bash
cd frontend
npm install
npm run dev
```

Mode-specific strategy configs live in `config/strategies.test.yml` and
`config/strategies.live.yml`. Add strategies there, not through the UI. They are disabled by
default and contain no secrets.

## Verification

Normal verification must not call Webull, InsightSentry, or place live orders:

```bash
python -m pytest
ruff check .
cd frontend && npm run build
```

For Webull behavior, use the current official documentation from
<https://developer.webull.com/apis/>, especially the Trading API account/order, SDK,
authentication, and token pages. For future InsightSentry work, use
<https://insightsentry.com/docs/ws>.
