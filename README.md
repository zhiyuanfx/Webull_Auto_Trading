# Webull Auto Trading

Python foundation for a local, paper-first InsightSentry plus Webull trading runtime. The
old external-alert bridge has been removed and replaced with Python-native strategy
instances, SQLite persistence, paper virtual orders, a FastAPI backend, and a React/Vite
operator console.

Runtime v1 supports `paper` and `preview` modes only. It can read Webull account, balance,
position, and open-order data through the official SDK, but it does not place live orders.

## Quick Start

Use the existing Conda environment that already works with the Webull SDK:

```bash
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull,sanity]"
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
```

`serve` starts the local backend on <http://127.0.0.1:8765> by default. The frontend lives in
`frontend/` and proxies `/api` to that backend during Vite development.

```bash
cd frontend
npm install
npm run dev
```

The first strategy instance config lives in `config/strategies.yml`. It is disabled by
default and contains no secrets.

## Verification

Normal verification must not call Webull, InsightSentry, or place live orders:

```bash
python -m pytest
ruff check .
```

For Webull behavior, use the current official documentation from
<https://developer.webull.com/apis/>, especially the Trading API account/order, SDK,
authentication, and token pages. For future InsightSentry work, use
<https://insightsentry.com/docs/ws>.
