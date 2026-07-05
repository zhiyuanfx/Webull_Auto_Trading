import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BadgeDollarSign,
  BarChart3,
  CirclePause,
  LayoutDashboard,
  ListPlus,
  Moon,
  Play,
  RefreshCw,
  ShieldAlert,
  Sun,
  TerminalSquare
} from "lucide-react";
import "./styles.css";

type Health = {
  ok: boolean;
  mode: string;
  global_pause: boolean;
  strategy_count: number;
  active_strategy_count: number;
  quote_count: number;
  webull_configured: boolean;
  database: string;
};

type StrategyInstance = {
  id: string;
  strategy_name: string;
  symbol: string;
  account_id: string;
  enabled: boolean;
  mode: string;
  params: Record<string, unknown>;
};

type QuoteSnapshot = {
  symbol: string;
  fields: Record<string, unknown>;
  received_at: string;
};

type AccountSnapshot = {
  configured: boolean;
  account_id: string;
  balance?: Record<string, unknown>;
  positions?: Record<string, unknown>[];
  open_orders?: Record<string, unknown>[];
  error?: string;
};

type ActivityRow = {
  id: number;
  ts: string;
  level: string;
  strategy_instance_id?: string;
  symbol?: string;
  event_type: string;
  message: string;
};

type Tab = "dashboard" | "strategies" | "market" | "orders" | "account" | "activity";

const tabs: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "strategies", label: "Strategies", icon: ListPlus },
  { id: "market", label: "Market Data", icon: BarChart3 },
  { id: "orders", label: "Orders", icon: TerminalSquare },
  { id: "account", label: "Account", icon: BadgeDollarSign },
  { id: "activity", label: "Activity", icon: Activity }
];

function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [dark, setDark] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [strategies, setStrategies] = useState<StrategyInstance[]>([]);
  const [quotes, setQuotes] = useState<QuoteSnapshot[]>([]);
  const [orders, setOrders] = useState<Record<string, unknown>[]>([]);
  const [cycles, setCycles] = useState<Record<string, unknown>[]>([]);
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
  const [activity, setActivity] = useState<ActivityRow[]>([]);

  async function refresh() {
    const [healthRes, strategiesRes, quotesRes, ordersRes, cyclesRes, accountRes, activityRes] =
      await Promise.all([
        fetch("/api/health"),
        fetch("/api/strategies"),
        fetch("/api/market/quotes"),
        fetch("/api/orders"),
        fetch("/api/cycles"),
        fetch("/api/account"),
        fetch("/api/activity")
      ]);
    setHealth(await healthRes.json());
    setStrategies(await strategiesRes.json());
    setQuotes(await quotesRes.json());
    setOrders(await ordersRes.json());
    setCycles(await cyclesRes.json());
    setAccount(await accountRes.json());
    setActivity(await activityRes.json());
  }

  useEffect(() => {
    void refresh();
    const handle = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(handle);
  }, []);

  const activeContent = useMemo(() => {
    switch (tab) {
      case "strategies":
        return <Strategies strategies={strategies} onChanged={refresh} />;
      case "market":
        return <Market quotes={quotes} strategies={strategies} onChanged={refresh} />;
      case "orders":
        return <Orders orders={orders} cycles={cycles} />;
      case "account":
        return <Account account={account} onAccount={setAccount} />;
      case "activity":
        return <ActivityLog rows={activity} />;
      default:
        return (
          <Dashboard
            health={health}
            quotes={quotes}
            strategies={strategies}
            activity={activity}
            onChanged={refresh}
          />
        );
    }
  }, [tab, health, strategies, quotes, orders, cycles, account, activity]);

  return (
    <main className={dark ? "app dark" : "app"}>
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">WA</span>
          <span>Webull Auto Trading</span>
        </div>
        <nav>
          {tabs.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={tab === item.id ? "nav active" : "nav"}
                key={item.id}
                onClick={() => setTab(item.id)}
                title={item.label}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <button className="iconText" onClick={() => setDark(!dark)} title="Toggle theme">
          {dark ? <Sun size={18} /> : <Moon size={18} />}
          <span>{dark ? "Light" : "Dark"}</span>
        </button>
      </aside>
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>{tabs.find((item) => item.id === tab)?.label}</h1>
            <p>{health?.mode ?? "paper-first"} runtime</p>
          </div>
          <button className="iconButton" onClick={() => void refresh()} title="Refresh">
            <RefreshCw size={18} />
          </button>
        </header>
        {activeContent}
      </section>
    </main>
  );
}

function Dashboard({
  health,
  quotes,
  strategies,
  activity,
  onChanged
}: {
  health: Health | null;
  quotes: QuoteSnapshot[];
  strategies: StrategyInstance[];
  activity: ActivityRow[];
  onChanged: () => Promise<void>;
}) {
  async function setPause(paused: boolean) {
    await fetch("/api/settings/global-pause", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ paused })
    });
    await onChanged();
  }
  return (
    <div className="grid">
      <Metric label="Service" value={health?.ok ? "Online" : "Unknown"} tone="green" />
      <Metric label="Active EAs" value={health?.active_strategy_count ?? 0} tone="blue" />
      <Metric label="Quotes" value={health?.quote_count ?? quotes.length} tone="gold" />
      <Metric label="Webull" value={health?.webull_configured ? "Configured" : "Read only off"} tone="red" />
      <section className="panel wide">
        <div className="panelHeader">
          <h2>Controls</h2>
          <button
            className={health?.global_pause ? "dangerButton" : "primaryButton"}
            onClick={() => void setPause(!health?.global_pause)}
          >
            {health?.global_pause ? <Play size={16} /> : <CirclePause size={16} />}
            <span>{health?.global_pause ? "Resume" : "Pause"}</span>
          </button>
        </div>
        <div className="statusStrip">
          <span>{strategies.length} configured strategy instances</span>
          <span>{activity.length} recent activity rows</span>
          <span>{health?.database ?? "database pending"}</span>
        </div>
      </section>
      <section className="panel wide">
        <h2>Recent Errors</h2>
        <Table
          rows={activity.filter((row) => row.level !== "info").slice(0, 6)}
          columns={["ts", "level", "symbol", "message"]}
          empty="No recent warnings"
        />
      </section>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: React.ReactNode; tone: string }) {
  return (
    <section className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </section>
  );
}

function Strategies({
  strategies,
  onChanged
}: {
  strategies: StrategyInstance[];
  onChanged: () => Promise<void>;
}) {
  const [symbol, setSymbol] = useState("NASDAQ:AAPL");
  async function add() {
    await fetch("/api/strategies", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        strategy_name: "day_many_bian",
        symbol,
        enabled: true,
        mode: "paper",
        params: {}
      })
    });
    await onChanged();
  }
  async function toggle(strategy: StrategyInstance) {
    await fetch(`/api/strategies/${strategy.id}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...strategy, enabled: !strategy.enabled })
    });
    await onChanged();
  }
  return (
    <div className="stack">
      <section className="toolbar">
        <input value={symbol} onChange={(event) => setSymbol(event.target.value)} />
        <button className="primaryButton" onClick={() => void add()}>
          <ListPlus size={16} />
          <span>Add</span>
        </button>
      </section>
      <Table
        rows={strategies.map((strategy) => ({
          ...strategy,
          status: strategy.enabled ? "Enabled" : "Paused",
          action: (
            <button className="smallButton" onClick={() => void toggle(strategy)}>
              {strategy.enabled ? "Pause" : "Resume"}
            </button>
          )
        }))}
        columns={["symbol", "strategy_name", "mode", "status", "action"]}
        empty="No strategy instances"
      />
    </div>
  );
}

function Market({
  quotes,
  strategies,
  onChanged
}: {
  quotes: QuoteSnapshot[];
  strategies: StrategyInstance[];
  onChanged: () => Promise<void>;
}) {
  const [symbol, setSymbol] = useState(strategies[0]?.symbol ?? "NASDAQ:AAPL");
  const [bid, setBid] = useState("100");
  const [ask, setAsk] = useState("100.05");
  async function sendQuote() {
    const now = Math.floor(Date.now() / 1000);
    await fetch("/api/market/quotes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        code: symbol,
        bid: Number(bid),
        ask: Number(ask),
        last_price: (Number(bid) + Number(ask)) / 2,
        lp_time: now,
        delay_seconds: 0,
        status: "REG"
      })
    });
    await onChanged();
  }
  return (
    <div className="stack">
      <section className="toolbar">
        <input value={symbol} onChange={(event) => setSymbol(event.target.value)} />
        <input value={bid} onChange={(event) => setBid(event.target.value)} />
        <input value={ask} onChange={(event) => setAsk(event.target.value)} />
        <button className="primaryButton" onClick={() => void sendQuote()}>
          <BarChart3 size={16} />
          <span>Inject Quote</span>
        </button>
      </section>
      <Table
        rows={quotes.map((quote) => ({
          symbol: quote.symbol,
          bid: String(quote.fields.bid ?? ""),
          ask: String(quote.fields.ask ?? ""),
          last: String(quote.fields.last_price ?? ""),
          delay: String(quote.fields.delay_seconds ?? ""),
          received_at: quote.received_at
        }))}
        columns={["symbol", "bid", "ask", "last", "delay", "received_at"]}
        empty="No quotes"
      />
    </div>
  );
}

function Orders({ orders, cycles }: { orders: Record<string, unknown>[]; cycles: Record<string, unknown>[] }) {
  return (
    <div className="stack">
      <Table rows={orders} columns={["symbol", "side", "role", "status", "stop_price", "fill_price", "stop_loss"]} empty="No paper orders" />
      <Table rows={cycles} columns={["symbol", "status", "opened_at", "closed_at", "realized_pnl"]} empty="No cycles" />
    </div>
  );
}

function Account({
  account,
  onAccount
}: {
  account: AccountSnapshot | null;
  onAccount: (account: AccountSnapshot) => void;
}) {
  const balance = account?.balance ? [account.balance] : [];
  async function refreshAccount() {
    const response = await fetch("/api/account?refresh=true");
    onAccount(await response.json());
  }
  return (
    <div className="stack">
      <section className="toolbar">
        <button className="primaryButton" onClick={() => void refreshAccount()}>
          <RefreshCw size={16} />
          <span>Refresh Webull Reads</span>
        </button>
        <span className="muted">
          {account?.configured ? "Configured" : "Not configured"} {account?.account_id ?? ""}
        </span>
      </section>
      {account?.error && (
        <section className="alert">
          <ShieldAlert size={18} />
          <span>{account.error}</span>
        </section>
      )}
      <Table rows={balance} columns={["total_asset_currency", "total_cash_balance", "total_net_liquidation_value", "total_day_profit_loss"]} empty="No balance snapshot" />
      <Table rows={account?.positions ?? []} columns={["symbol", "quantity", "last_price", "unrealized_profit_loss"]} empty="No positions" />
      <Table rows={account?.open_orders ?? []} columns={["client_order_id", "combo_type", "orders"]} empty="No Webull open orders" />
    </div>
  );
}

function ActivityLog({ rows }: { rows: ActivityRow[] }) {
  return <Table rows={rows} columns={["ts", "level", "symbol", "event_type", "message"]} empty="No activity" />;
}

function Table({
  rows,
  columns,
  empty
}: {
  rows: Record<string, unknown>[];
  columns: string[];
  empty: string;
}) {
  return (
    <section className="panel tablePanel">
      {rows.length === 0 ? (
        <div className="empty">{empty}</div>
      ) : (
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column.replaceAll("_", " ")}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{renderCell(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function renderCell(value: unknown): React.ReactNode {
  if (React.isValidElement(value)) return value;
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

createRoot(document.getElementById("root")!).render(<App />);
