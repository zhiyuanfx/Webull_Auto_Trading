# Webull Strategy Desk

A local, source-first strategy/EA manager for Webull stocks and futures. Strategies
own their logical orders through an MT4-like `OrderSession`; a credential-holding
execution gateway handles broker transport, shared validation, and reconciliation.

The current build includes isolated strategy workers, strategy-local order sessions,
SQLite WAL persistence with FIFO virtual lots, a configurable local simulator, Webull
market/trading adapters, HTTP order reconciliation, fixed/auto-roll futures expiry guards,
and the bilingual light/dark operations UI. Production execution is double-gated.

## Quick start

Requirements: Conda. The project environment installs Python 3.12 and a compatible
Node.js release.

```bash
conda env create -f environment.yml
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"  # editable pointer + dependencies
cp .env.example .env
strategy-desk init-db
strategy-desk serve --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Local simulation is the default and production
order transmission is disabled unless both the environment and UI gates are set.

Copy values into the gitignored `.env` using the comments in `.env.example`. Strategy
workers never receive those variables. Webull market data may require a separate OpenAPI
quotes entitlement even when the desktop application already has quote access.
Reusable SDK tokens are cached in the ignored `.runtime/webull_tokens` directory.

Conda owns the interpreter and installed packages. The editable installation does not copy
the repository into Conda; it registers a pointer to this checkout and provides the
`strategy-desk` command. `pyproject.toml` remains the source of dependency declarations and
`uv.lock` remains the tracked resolution record, but normal
development and runtime commands do not use `uv run` or `uv sync`. When dependencies
change, update the lock without modifying the active Conda environment:

```bash
uv lock
python -m pip install -e ".[dev,webull]"
```

The `webull` extra installs the official SDK boundary used by UAT and production adapters.
Run all commands only after activating `webull-strategy-desk`; `which python` on macOS/Linux
or `where python` on Windows should point into that Conda environment.

Run offline verification with `python -m pytest` and `ruff check .`, followed by
`cd frontend && npm run build`. The read-only UAT checks remain explicit opt-in and are
not part of ordinary verification:

```bash
RUN_WEBULL_UAT_TESTS=1 python -m pytest tests/integration/test_webull_uat.py
```

Validate and import a replay before selecting its path in the instance form:

```bash
strategy-desk replay-import /path/to/quotes.jsonl --name morning-mgc.jsonl
strategy-desk diagnose
```

To rebuild rather than update the environment, deactivate it, remove it with
`conda env remove -n webull-strategy-desk`, and repeat the three setup commands above.

See [AGENTS.md](AGENTS.md), [strategies/AGENTS.md](strategies/AGENTS.md), and
[docs/decisions.md](docs/decisions.md) for architecture, plugin contracts, safety rules,
and source/UI ownership decisions.
