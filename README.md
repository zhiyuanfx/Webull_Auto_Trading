# Webull Auto Trading

A local trading console with Python strategies, InsightSentry streaming market data,
SQLite persistence, a FastAPI backend, and a React/Vite UI. Global **Test** mode runs
paper trades; **Live** mode uses the official Webull SDK for account reads and
safety-gated market orders.

## Quick Start

From the repository root, set up the Conda environment and local configuration:

```bash
conda env create -f environment.yml
conda activate webull-strategy-desk
python -m pip install -e ".[dev,webull]"
cp .env.example .env
cp config/strategies.test.example.yml config/strategies.test.yml
cp config/strategies.live.example.yml config/strategies.live.yml
```

For an existing installation, reuse the environment and local config files. Edit
`.env` and the strategy YAML before starting; replace example symbols with current
instruments and start in Test mode. Examples are disabled by default.

Start the backend:

```bash
webull-auto-trading diagnose
webull-auto-trading init-db
webull-auto-trading serve --reload
```

In a second terminal, start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite (usually [http://127.0.0.1:5173](http://127.0.0.1:5173)).
The backend runs at [http://127.0.0.1:8765](http://127.0.0.1:8765) and owns the market
stream and live safety/reconciliation worker.

## Configuration

- **Environment:** [`.env.example`](.env.example) lists InsightSentry credentials,
  Webull credentials and account aliases, database/config paths, and quote settings.
  Copy it to the ignored `.env` file and fill in the values you need.
- **Strategies:** edit `config/strategies.test.yml` or `config/strategies.live.yml`
  for instance IDs, strategy names, symbols, parameters, and enable flags. See the
  [Test example](config/strategies.test.example.yml) and
  [Live skeleton](config/strategies.live.example.yml).
- **Live execution:** keep `LIVE_EXECUTION_MASTER_ENABLE=false` during setup.
  Transmission requires global Live mode, the master switch, the instance's
  `live_execution_enabled` flag, and all account, quote, preview, and reconciliation
  gates to pass.
- **Local data:** `.env`, real strategy configs/modules, `.runtime/` (SQLite and SDK
  tokens), and `data/` are ignored. Keep credentials and private trading settings out
  of git.

## Main Commands

Run Python commands in the activated Conda environment.

| Command | Purpose |
| --- | --- |
| `webull-auto-trading diagnose` | Offline configuration checks |
| `webull-auto-trading diagnose-live` | Offline live execution checks |
| `webull-auto-trading accounts` | Fetch Webull account IDs |
| `webull-auto-trading init-db` | Initialize the local database |
| `webull-auto-trading serve --reload` | Start the backend and workers |
| `webull-auto-trading run` | Initialize the runtime and print health |
| `webull-auto-trading cleanup --dry-run` | Preview storage cleanup |
| `python -m pytest` | Run offline tests |
| `ruff check .` | Lint Python |
| `cd frontend && npm run build` | Build the frontend |

## Adding Strategies

Create `src/webull_auto_trading/strategy/custom.py` with a `CustomStrategy`
class extending the [base interface](src/webull_auto_trading/strategy/base.py), then
add an instance with `strategy_name: custom` to the appropriate local YAML.
Restart the backend to load changes. Keep private modules and tuned configs local;
stateful strategies must implement `reset_state(instance)`.

Use [Recycle Buy](src/webull_auto_trading/strategy/recycle_buy.py) as a small Test/Live
example: it buys, attaches fixed stop/target distances, and waits through a cooldown
before re-entering. It has no daily cycle limit, session window, or contract cutoff.
Keep new Live instances paused until their operating limits are ready.

See [architecture decisions](docs/decisions.md) for runtime and safety details and
the [official Webull documentation](https://developer.webull.com/apis/) for API usage.
