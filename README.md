# Webull Auto Trading

Python foundation for the next Webull auto-trading project. The old external-alert bridge
has been removed so the repo can be rebuilt around InsightSentry streaming data and
Python-native strategy logic.

The current baseline keeps only the reusable Webull pieces: environment-backed credential
settings, SDK token cache configuration, account id discovery, safe diagnostics, and a small
official-SDK wrapper for account, position, open-order, preview, place, replace, and cancel
operations. It does not include an InsightSentry client, strategy runtime, executor, API
server, database, or UI yet.

## Quick Start

Use the existing Conda environment that already works with the Webull SDK:

```bash
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
cp .env.example .env
webull-auto-trading diagnose
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
```

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
