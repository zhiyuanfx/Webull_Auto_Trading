import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BadgeDollarSign,
  BarChart3,
  CirclePause,
  Database,
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

type Lang = "en" | "zh";
type RuntimeMode = "test" | "live";
type Tab = "dashboard" | "strategies" | "market" | "orders" | "account" | "storage" | "activity";

type Health = {
  ok: boolean;
  mode: RuntimeMode;
  mode_message: string;
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
  params: Record<string, unknown>;
};

type AccountPayload = {
  mode?: RuntimeMode;
  message?: string;
  configured?: boolean;
  account_id?: string;
  balance?: Record<string, unknown>;
  positions?: Record<string, unknown>[];
  open_orders?: Record<string, unknown>[];
  error?: string;
  paper_account?: Record<string, unknown>;
  history?: Record<string, unknown>[];
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

type StreamMessage = {
  sequence: number;
  timestamp: string;
  strategy_instance_id: string;
  symbol: string;
  type: string;
  raw: Record<string, unknown>;
};

const dictionary = {
  en: {
    account: "Account",
    activeEas: "Active EAs",
    activity: "Activity",
    add: "Add",
    appName: "Webull Auto Trading",
    clear: "Clear",
    configured: "Configured",
    controls: "Controls",
    dashboard: "Dashboard",
    dark: "Dark",
    deposit: "Deposit",
    enabled: "Enabled",
    live: "Live",
    liveSafety: "Live mode: Webull reads enabled, live execution disabled",
    market: "Market Data",
    noActivity: "No activity",
    noMessages: "No stream messages",
    noOrders: "No paper orders",
    noRows: "No rows",
    orders: "Orders",
    pause: "Pause",
    paused: "Paused",
    paperAccount: "Paper account",
    recentIssues: "Recent Issues",
    refresh: "Refresh",
    refreshWebull: "Refresh Webull Reads",
    reset: "Reset",
    resume: "Resume",
    runningStrategy: "Running strategy",
    service: "Service",
    status: "Status",
    storage: "Storage",
    strategies: "Strategies",
    strategy: "Strategy",
    test: "Test",
    testSafety: "Test mode: paper trading with real InsightSentry market data",
    themeLight: "Light",
    unknown: "Unknown",
    webull: "Webull"
  },
  zh: {
    account: "账户",
    activeEas: "运行策略",
    activity: "活动",
    add: "添加",
    appName: "Webull 自动交易",
    clear: "清除",
    configured: "已配置",
    controls: "控制",
    dashboard: "仪表盘",
    dark: "深色",
    deposit: "入金",
    enabled: "启用",
    live: "实盘",
    liveSafety: "实盘模式：允许 Webull 读取，禁止实盘执行",
    market: "市场数据",
    noActivity: "暂无活动",
    noMessages: "暂无流消息",
    noOrders: "暂无纸面订单",
    noRows: "暂无数据",
    orders: "订单",
    pause: "暂停",
    paused: "暂停",
    paperAccount: "纸面账户",
    recentIssues: "近期问题",
    refresh: "刷新",
    refreshWebull: "刷新 Webull 读取",
    reset: "重置",
    resume: "恢复",
    runningStrategy: "运行策略",
    service: "服务",
    status: "状态",
    storage: "存储",
    strategies: "策略",
    strategy: "策略",
    test: "测试",
    testSafety: "测试模式：使用真实 InsightSentry 行情进行纸面交易",
    themeLight: "浅色",
    unknown: "未知",
    webull: "Webull"
  }
};

function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [dark, setDark] = useState(() => localStorage.getItem("theme") === "dark");
  const [lang, setLang] = useState<Lang>(() => (localStorage.getItem("lang") as Lang) || "en");
  const [health, setHealth] = useState<Health | null>(null);
  const [strategies, setStrategies] = useState<StrategyInstance[]>([]);
  const [orders, setOrders] = useState<Record<string, unknown>[]>([]);
  const [cycles, setCycles] = useState<Record<string, unknown>[]>([]);
  const [account, setAccount] = useState<AccountPayload | null>(null);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [storage, setStorage] = useState<Record<string, unknown> | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const t = dictionary[lang];

  const tabs = useMemo(
    () => [
      { id: "dashboard" as const, label: t.dashboard, icon: LayoutDashboard },
      { id: "strategies" as const, label: t.strategies, icon: ListPlus },
      { id: "market" as const, label: t.market, icon: BarChart3 },
      { id: "orders" as const, label: t.orders, icon: TerminalSquare },
      { id: "account" as const, label: t.account, icon: BadgeDollarSign },
      { id: "storage" as const, label: t.storage, icon: Database },
      { id: "activity" as const, label: t.activity, icon: Activity }
    ],
    [t]
  );

  async function refresh() {
    setRefreshing(true);
    try {
      const [healthRes, strategiesRes, ordersRes, cyclesRes, accountRes, activityRes, storageRes] =
        await Promise.all([
          fetch("/api/health"),
          fetch("/api/strategies"),
          fetch("/api/orders"),
          fetch("/api/cycles"),
          fetch("/api/account"),
          fetch("/api/activity"),
          fetch("/api/storage/stats")
        ]);
      setHealth(await healthRes.json());
      setStrategies(await strategiesRes.json());
      setOrders(await ordersRes.json());
      setCycles(await cyclesRes.json());
      setAccount(await accountRes.json());
      setActivity(await activityRes.json());
      setStorage(await storageRes.json());
      setLastUpdatedAt(new Date());
    } finally {
      setRefreshing(false);
    }
  }

  async function switchMode(mode: RuntimeMode) {
    await fetch("/api/settings/runtime-mode", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode })
    });
    await refresh();
  }

  useEffect(() => {
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    localStorage.setItem("lang", lang);
  }, [lang]);

  useEffect(() => {
    void refresh();
    const handle = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(handle);
  }, []);

  const activeContent = useMemo(() => {
    switch (tab) {
      case "strategies":
        return <Strategies strategies={strategies} onChanged={refresh} t={t} />;
      case "market":
        return <Market t={t} />;
      case "orders":
        return <Orders orders={orders} cycles={cycles} t={t} />;
      case "account":
        return <Account account={account} onAccount={setAccount} refresh={refresh} t={t} />;
      case "storage":
        return <StoragePanel storage={storage} onChanged={refresh} t={t} />;
      case "activity":
        return <ActivityLog rows={activity} t={t} />;
      default:
        return (
          <Dashboard
            health={health}
            strategies={strategies}
            activity={activity}
            onChanged={refresh}
            t={t}
          />
        );
    }
  }, [tab, health, strategies, orders, cycles, account, activity, storage, t]);

  const refreshTitle = lastUpdatedAt
    ? `${t.refresh}: ${lastUpdatedAt.toLocaleTimeString()}`
    : t.refresh;

  return (
    <main className={dark ? "app dark" : "app"}>
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">WA</span>
          <span>{t.appName}</span>
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
        <div className="sidebarControls">
          <Segmented
            value={health?.mode ?? "test"}
            options={[
              { value: "test", label: t.test },
              { value: "live", label: t.live }
            ]}
            onChange={(value) => void switchMode(value as RuntimeMode)}
          />
          <Segmented
            value={lang}
            options={[
              { value: "en", label: "EN" },
              { value: "zh", label: "简体中文" }
            ]}
            onChange={(value) => setLang(value as Lang)}
          />
          <button className="iconText" onClick={() => setDark(!dark)} title="Toggle theme">
            {dark ? <Sun size={18} /> : <Moon size={18} />}
            <span>{dark ? t.themeLight : t.dark}</span>
          </button>
        </div>
      </aside>
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>{tabs.find((item) => item.id === tab)?.label}</h1>
            <p>{health?.mode_message ?? t.testSafety}</p>
          </div>
          <button className="iconButton" onClick={() => void refresh()} title={refreshTitle}>
            <RefreshCw size={18} className={refreshing ? "spin" : ""} />
          </button>
        </header>
        {activeContent}
      </section>
    </main>
  );
}

function Segmented({
  value,
  options,
  onChange
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="segmented">
      {options.map((option) => (
        <button
          key={option.value}
          className={value === option.value ? "selected" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function Dashboard({
  health,
  strategies,
  activity,
  onChanged,
  t
}: {
  health: Health | null;
  strategies: StrategyInstance[];
  activity: ActivityRow[];
  onChanged: () => Promise<void>;
  t: typeof dictionary.en;
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
      <Metric label={t.service} value={health?.ok ? "Online" : t.unknown} tone="green" />
      <Metric label={t.activeEas} value={health?.active_strategy_count ?? 0} tone="blue" />
      <Metric label={t.market} value={health?.quote_count ?? 0} tone="gold" />
      <Metric label={t.webull} value={health?.webull_configured ? t.configured : "Off"} tone="red" />
      <section className="panel wide">
        <div className="panelHeader">
          <h2>{t.controls}</h2>
          <button
            className={health?.global_pause ? "dangerButton" : "primaryButton"}
            onClick={() => void setPause(!health?.global_pause)}
          >
            {health?.global_pause ? <Play size={16} /> : <CirclePause size={16} />}
            <span>{health?.global_pause ? t.resume : t.pause}</span>
          </button>
        </div>
        <div className="statusStrip">
          <span>{strategies.length} {t.strategies}</span>
          <span>{activity.length} {t.activity}</span>
          <span>{health?.database ?? ""}</span>
        </div>
      </section>
      <section className="panel wide">
        <h2>{t.recentIssues}</h2>
        <Table
          rows={activity.filter((row) => row.level !== "info").slice(0, 6)}
          columns={["ts", "level", "symbol", "message"]}
          empty={t.noRows}
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
  onChanged,
  t
}: {
  strategies: StrategyInstance[];
  onChanged: () => Promise<void>;
  t: typeof dictionary.en;
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
        params: {}
      })
    });
    await onChanged();
  }
  async function toggle(strategy: StrategyInstance) {
    await fetch(`/api/strategies/${strategy.id}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...strategy, enabled: !strategy.enabled, mode: "paper" })
    });
    await onChanged();
  }
  return (
    <div className="stack">
      <section className="toolbar">
        <input value={symbol} onChange={(event) => setSymbol(event.target.value)} />
        <button className="primaryButton" onClick={() => void add()}>
          <ListPlus size={16} />
          <span>{t.add}</span>
        </button>
      </section>
      <Table
        rows={strategies.map((strategy) => ({
          ...strategy,
          status: strategy.enabled ? t.enabled : t.paused,
          action: (
            <button className="smallButton" onClick={() => void toggle(strategy)}>
              {strategy.enabled ? t.pause : t.resume}
            </button>
          )
        }))}
        columns={["symbol", "strategy_name", "status", "action"]}
        empty={t.noRows}
      />
    </div>
  );
}

function Market({ t }: { t: typeof dictionary.en }) {
  const [strategies, setStrategies] = useState<StrategyInstance[]>([]);
  const [selected, setSelected] = useState("");
  const [cursor, setCursor] = useState(0);
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  async function loadStrategies() {
    const response = await fetch("/api/market/streams/strategies");
    const rows = await response.json();
    setStrategies(rows);
    if (!selected && rows[0]) {
      setSelected(rows[0].id);
    }
  }

  async function poll(strategyId = selected, since = cursor) {
    if (!strategyId) return;
    const response = await fetch(`/api/market/streams/${strategyId}?since=${since}`);
    const rows: StreamMessage[] = await response.json();
    if (rows.length) {
      setMessages((current) => [...current, ...rows].slice(-20));
      setCursor(rows[rows.length - 1].sequence);
    }
  }

  async function switchStrategy(strategyId: string) {
    setSelected(strategyId);
    setMessages([]);
    const response = await fetch(`/api/market/streams/${strategyId}`);
    const existing: StreamMessage[] = await response.json();
    setCursor(existing.length ? existing[existing.length - 1].sequence : 0);
  }

  async function clearVisible() {
    setMessages([]);
    const response = await fetch(`/api/market/streams/${selected}`);
    const existing: StreamMessage[] = await response.json();
    setCursor(existing.length ? existing[existing.length - 1].sequence : cursor);
  }

  useEffect(() => {
    void loadStrategies();
  }, []);

  useEffect(() => {
    void poll();
    const handle = window.setInterval(() => void poll(), 1500);
    return () => window.clearInterval(handle);
  }, [selected, cursor]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  return (
    <div className="stack">
      <section className="toolbar">
        <select value={selected} onChange={(event) => void switchStrategy(event.target.value)}>
          {strategies.map((strategy) => (
            <option key={strategy.id} value={strategy.id}>
              {strategy.symbol} / {strategy.id}
            </option>
          ))}
        </select>
        <button className="smallButton" onClick={() => void clearVisible()}>
          {t.clear}
        </button>
      </section>
      <section className="panel streamPanel" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="empty">{t.noMessages}</div>
        ) : (
          messages.map((message) => (
            <pre key={message.sequence}>
              {message.sequence} {message.timestamp} {message.symbol} {message.type}
              {"\n"}
              {JSON.stringify(message.raw, null, 2)}
            </pre>
          ))
        )}
      </section>
    </div>
  );
}

function Orders({
  orders,
  cycles,
  t
}: {
  orders: Record<string, unknown>[];
  cycles: Record<string, unknown>[];
  t: typeof dictionary.en;
}) {
  return (
    <div className="stack">
      <Table
        rows={orders}
        columns={["symbol", "side", "role", "status", "stop_price", "fill_price", "stop_loss"]}
        empty={t.noOrders}
      />
      <Table
        rows={cycles}
        columns={["symbol", "status", "opened_at", "closed_at", "realized_pnl"]}
        empty={t.noRows}
      />
    </div>
  );
}

function Account({
  account,
  onAccount,
  refresh,
  t
}: {
  account: AccountPayload | null;
  onAccount: (account: AccountPayload) => void;
  refresh: () => Promise<void>;
  t: typeof dictionary.en;
}) {
  const [deposit, setDeposit] = useState("1000");
  async function refreshAccount() {
    const response = await fetch("/api/account?refresh=true");
    onAccount(await response.json());
  }
  async function depositPaper() {
    await fetch("/api/paper-account/deposit", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ amount: Number(deposit) })
    });
    await refresh();
  }
  async function resetPaper() {
    await fetch("/api/paper-account/reset", { method: "POST" });
    await refresh();
  }
  if (account?.mode === "test") {
    return (
      <div className="stack">
        <section className="toolbar">
          <input value={deposit} onChange={(event) => setDeposit(event.target.value)} />
          <button className="primaryButton" onClick={() => void depositPaper()}>{t.deposit}</button>
          <button className="dangerButton" onClick={() => void resetPaper()}>{t.reset}</button>
          <span className="muted">{account.message ?? t.testSafety}</span>
        </section>
        <Table rows={account.paper_account ? [account.paper_account] : []} columns={["starting_balance", "cash_balance", "realized_pnl", "unrealized_pnl", "current_equity", "peak_equity", "max_drawdown"]} empty={t.noRows} />
        <Table rows={account.positions ?? []} columns={["symbol", "side", "quantity", "average_price", "market_price", "unrealized_pnl"]} empty={t.noRows} />
        <Table rows={account.history ?? []} columns={["created_at", "event_type", "amount", "message"]} empty={t.noRows} />
      </div>
    );
  }
  return (
    <div className="stack">
      <section className="toolbar">
        <button className="primaryButton" onClick={() => void refreshAccount()}>
          <RefreshCw size={16} />
          <span>{t.refreshWebull}</span>
        </button>
        <span className="muted">{account?.message ?? t.liveSafety}</span>
      </section>
      {account?.error && (
        <section className="alert">
          <ShieldAlert size={18} />
          <span>{account.error}</span>
        </section>
      )}
      <Table rows={account?.balance ? [account.balance] : []} columns={["total_asset_currency", "total_cash_balance", "total_net_liquidation_value", "total_day_profit_loss"]} empty={t.noRows} />
      <Table rows={account?.positions ?? []} columns={["symbol", "quantity", "last_price", "unrealized_profit_loss"]} empty={t.noRows} />
      <Table rows={account?.open_orders ?? []} columns={["client_order_id", "combo_type", "orders"]} empty={t.noRows} />
    </div>
  );
}

function StoragePanel({
  storage,
  onChanged,
  t
}: {
  storage: Record<string, unknown> | null;
  onChanged: () => Promise<void>;
  t: typeof dictionary.en;
}) {
  async function cleanup(dryRun: boolean) {
    await fetch("/api/storage/cleanup", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ dry_run: dryRun, activity_days: 30, paper_history_days: 365, closed_cycle_days: 365, vacuum: !dryRun })
    });
    await onChanged();
  }
  const rows = storage?.tables ? [storage.tables as Record<string, unknown>] : [];
  return (
    <div className="stack">
      <section className="toolbar">
        <button className="smallButton" onClick={() => void cleanup(true)}>Dry run</button>
        <button className="primaryButton" onClick={() => void cleanup(false)}>Cleanup</button>
        <span className="muted">{String(storage?.database_bytes ?? 0)} bytes</span>
      </section>
      <Table rows={rows} columns={["activity", "orders", "fills", "cycles", "paper_account_events", "quote_snapshots", "bars"]} empty={t.noRows} />
    </div>
  );
}

function ActivityLog({ rows, t }: { rows: ActivityRow[]; t: typeof dictionary.en }) {
  return <Table rows={rows} columns={["ts", "level", "symbol", "event_type", "message"]} empty={t.noActivity} />;
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
