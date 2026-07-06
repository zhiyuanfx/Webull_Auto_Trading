import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BadgeDollarSign,
  BarChart3,
  CirclePause,
  Database,
  LayoutDashboard,
  Moon,
  Play,
  RefreshCw,
  ShieldAlert,
  Settings2,
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

type FlattenSummary = {
  strategy_id?: string | null;
  global_pause: boolean;
  paused_strategy_count: number;
  cancelled_pending: number;
  closed_positions: number;
  warnings: string[];
};

const dictionary = {
  en: {
    account: "Account",
    activeEas: "Active EAs",
    activity: "Activity",
    appName: "Webull Auto Trading",
    columnHeaders: {
      action: "Action",
      activity: "Activity",
      amount: "Amount",
      average_price: "Average price",
      bars: "Bars",
      cash_balance: "Cash balance",
      client_order_id: "Client order ID",
      closed_at: "Closed at",
      combo_type: "Combo type",
      created_at: "Created at",
      current_equity: "Current equity",
      cycles: "Cycles",
      event_type: "Event type",
      fill_price: "Fill price",
      fills: "Fills",
      last_price: "Last price",
      level: "Level",
      market_price: "Market price",
      max_drawdown: "Max drawdown",
      message: "Message",
      opened_at: "Opened at",
      open_orders: "Open orders",
      orders: "Orders",
      paper_account_events: "Paper account events",
      peak_equity: "Peak equity",
      quantity: "Quantity",
      quote_snapshots: "Quote snapshots",
      realized_pnl: "Realized P&L",
      role: "Role",
      side: "Side",
      starting_balance: "Starting balance",
      status: "Status",
      stop_loss: "Stop loss",
      stop_price: "Stop price",
      strategy_name: "Strategy",
      symbol: "Symbol",
      total_asset_currency: "Total asset currency",
      total_cash_balance: "Total cash balance",
      total_day_profit_loss: "Day P&L",
      total_net_liquidation_value: "Net liquidation value",
      ts: "Time",
      unrealized_pnl: "Unrealized P&L",
      unrealized_profit_loss: "Unrealized P&L"
    },
    clear: "Clear",
    configured: "Configured",
    controls: "Controls",
    cancelledPending: "Cancelled pending",
    closedPositions: "Closed positions",
    dashboard: "Dashboard",
    dark: "Dark",
    deposit: "Deposit",
    dryRun: "Dry run",
    enabled: "Enabled",
    flatten: "Flatten",
    flattenFailedNoQuote: "Flatten failed: no quote",
    flattening: "Flattening...",
    globalFlatten: "Global Flatten",
    globalPause: "Global Pause",
    globalPauseStatus: "Global pause",
    globalResume: "Global Resume",
    warnings: "Warnings",
    live: "Live",
    liveSafety: "Live mode: Webull reads enabled, live execution disabled",
    liveFlattenDisabled: "Live execution disabled",
    market: "Market Data",
    lastUpdated: "Last updated",
    noActivity: "No activity",
    noMessages: "No stream messages",
    noOrders: "No paper orders",
    noRows: "No rows",
    orders: "Orders",
    pause: "Pause",
    pausing: "Pausing...",
    paused: "Paused",
    paperAccount: "Paper account",
    recentIssues: "Recent Issues",
    refresh: "Refresh",
    refreshWebull: "Refresh Webull Reads",
    reset: "Reset",
    resume: "Resume",
    resuming: "Resuming...",
    cleanup: "Cleanup",
    requestFailed: "Request failed",
    runningWaitingForMarketData: "Running, waiting for market data",
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
    waitingForMarketData: "Waiting for market data...",
    webull: "Webull"
  },
  zh: {
    account: "账户",
    activeEas: "运行策略",
    activity: "活动",
    appName: "Webull 自动交易",
    columnHeaders: {
      action: "操作",
      activity: "活动",
      amount: "金额",
      average_price: "平均价格",
      bars: "K线",
      cash_balance: "现金余额",
      client_order_id: "客户订单ID",
      closed_at: "关闭时间",
      combo_type: "组合类型",
      created_at: "创建时间",
      current_equity: "当前权益",
      cycles: "周期",
      event_type: "事件类型",
      fill_price: "成交价",
      fills: "成交",
      last_price: "最新价",
      level: "级别",
      market_price: "市场价格",
      max_drawdown: "最大回撤",
      message: "消息",
      opened_at: "打开时间",
      open_orders: "未结订单",
      orders: "订单",
      paper_account_events: "纸面账户事件",
      peak_equity: "权益峰值",
      quantity: "数量",
      quote_snapshots: "报价快照",
      realized_pnl: "已实现盈亏",
      role: "角色",
      side: "方向",
      starting_balance: "初始余额",
      status: "状态",
      stop_loss: "止损",
      stop_price: "止损触发价",
      strategy_name: "策略",
      symbol: "标的",
      total_asset_currency: "资产币种",
      total_cash_balance: "总现金余额",
      total_day_profit_loss: "当日盈亏",
      total_net_liquidation_value: "总净清算价值",
      ts: "时间",
      unrealized_pnl: "未实现盈亏",
      unrealized_profit_loss: "未实现盈亏"
    },
    clear: "清除",
    configured: "已配置",
    controls: "控制",
    cancelledPending: "已取消挂单",
    closedPositions: "已平仓持仓",
    dashboard: "仪表盘",
    dark: "深色",
    deposit: "入金",
    dryRun: "试运行",
    enabled: "启用",
    flatten: "平仓",
    flattenFailedNoQuote: "平仓失败：无报价",
    flattening: "平仓中...",
    globalFlatten: "全局平仓",
    globalPause: "全局暂停",
    globalPauseStatus: "全局暂停",
    globalResume: "全局恢复",
    warnings: "警告",
    live: "实盘",
    liveSafety: "实盘模式：允许 Webull 读取，禁止实盘执行",
    liveFlattenDisabled: "实盘执行已禁用",
    market: "市场数据",
    lastUpdated: "上次刷新",
    noActivity: "暂无活动",
    noMessages: "暂无流消息",
    noOrders: "暂无纸面订单",
    noRows: "暂无数据",
    orders: "订单",
    pause: "暂停",
    pausing: "暂停中...",
    paused: "暂停",
    paperAccount: "纸面账户",
    recentIssues: "近期问题",
    refresh: "刷新",
    refreshWebull: "刷新 Webull 读取",
    reset: "重置",
    resume: "恢复",
    resuming: "恢复中...",
    cleanup: "清理",
    requestFailed: "请求失败",
    runningWaitingForMarketData: "运行中，等待市场数据",
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
    waitingForMarketData: "等待市场数据...",
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
      { id: "strategies" as const, label: t.strategies, icon: Settings2 },
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
        return <Strategies health={health} strategies={strategies} onChanged={refresh} t={t} />;
      case "market":
        return <Market strategies={strategies} t={t} />;
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
            activity={activity}
            t={t}
          />
        );
    }
  }, [tab, health, strategies, orders, cycles, account, activity, storage, t]);

  const refreshTitle = lastUpdatedAt
    ? `${t.lastUpdated} ${lastUpdatedAt.toLocaleTimeString()}`
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
          <h1>{tabs.find((item) => item.id === tab)?.label}</h1>
          <div className="refreshControl">
            <button className="iconButton" onClick={() => void refresh()} aria-label={t.refresh}>
              <RefreshCw size={18} className={refreshing ? "spin" : ""} />
            </button>
            <span className="refreshTooltip" role="status">
              {refreshTitle}
            </span>
          </div>
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
  activity,
  t
}: {
  health: Health | null;
  activity: ActivityRow[];
  t: typeof dictionary.en;
}) {
  return (
    <div className="grid">
      <Metric label={t.service} value={health?.ok ? "Online" : t.unknown} tone="green" />
      <Metric label={t.activeEas} value={health?.active_strategy_count ?? 0} tone="blue" />
      <Metric label={t.market} value={health?.quote_count ?? 0} tone="gold" />
      <Metric label={t.webull} value={health?.webull_configured ? t.configured : "Off"} tone="red" />
      <section className="panel wide">
        <h2>{t.recentIssues}</h2>
        <Table
          rows={activity.filter((row) => row.level !== "info").slice(0, 6)}
          columns={["ts", "level", "symbol", "message"]}
          empty={t.noRows}
          t={t}
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
  health,
  strategies,
  onChanged,
  t
}: {
  health: Health | null;
  strategies: StrategyInstance[];
  onChanged: () => Promise<void>;
  t: typeof dictionary.en;
}) {
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [rowStatus, setRowStatus] = useState<Record<string, string>>({});
  const liveMode = health?.mode === "live";

  async function toggle(strategy: StrategyInstance) {
    const nextEnabled = !strategy.enabled;
    const actionId = `${nextEnabled ? "resume" : "pause"}:${strategy.id}`;
    setPendingAction(actionId);
    setStatusMessage("");
    setRowStatus((current) => ({
      ...current,
      [strategy.id]: nextEnabled ? t.resuming : t.pausing
    }));
    try {
      const response = await fetch(`/api/strategies/${strategy.id}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled: nextEnabled })
      });
      if (!response.ok) {
        throw new Error(await responseError(response, t.requestFailed));
      }
      setRowStatus((current) => {
        const next = { ...current };
        delete next[strategy.id];
        return next;
      });
      await onChanged();
    } catch (error) {
      const message = error instanceof Error ? error.message : t.requestFailed;
      setStatusMessage(message);
      setRowStatus((current) => ({
        ...current,
        [strategy.id]: `${nextEnabled ? t.resume : t.pause} ${t.requestFailed}`
      }));
    } finally {
      setPendingAction(null);
    }
  }

  async function setGlobalPause(paused: boolean) {
    const actionId = paused ? "global-pause" : "global-resume";
    setPendingAction(actionId);
    setStatusMessage("");
    try {
      const response = await fetch("/api/settings/global-pause", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ paused })
      });
      if (!response.ok) {
        throw new Error(await responseError(response, t.requestFailed));
      }
      await onChanged();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : t.requestFailed);
    } finally {
      setPendingAction(null);
    }
  }

  async function flatten(strategy?: StrategyInstance) {
    if (liveMode) {
      setStatusMessage(t.liveFlattenDisabled);
      return;
    }
    const actionId = strategy ? `flatten:${strategy.id}` : "global-flatten";
    setPendingAction(actionId);
    setStatusMessage("");
    if (strategy) {
      setRowStatus((current) => ({ ...current, [strategy.id]: t.flattening }));
    }
    try {
      const response = await fetch(
        strategy ? `/api/strategies/${strategy.id}/flatten` : "/api/strategies/flatten",
        { method: "POST" }
      );
      if (!response.ok) {
        throw new Error(await responseError(response, t.requestFailed));
      }
      const summary = (await response.json()) as FlattenSummary;
      const message = flattenSummaryMessage(summary, t);
      setStatusMessage(message);
      if (strategy) {
        setRowStatus((current) => ({
          ...current,
          [strategy.id]: summary.warnings.length ? t.flattenFailedNoQuote : t.paused
        }));
      }
      await onChanged();
    } catch (error) {
      const message = error instanceof Error ? error.message : t.requestFailed;
      setStatusMessage(message);
      if (strategy) {
        setRowStatus((current) => ({ ...current, [strategy.id]: message }));
      }
    } finally {
      setPendingAction(null);
    }
  }

  function statusFor(strategy: StrategyInstance) {
    if (rowStatus[strategy.id]) return rowStatus[strategy.id];
    if (pendingAction === "global-pause" && strategy.enabled) return t.pausing;
    if (pendingAction === "global-resume" && strategy.enabled) return t.resuming;
    if (!strategy.enabled) return t.paused;
    if (health?.global_pause) return t.globalPauseStatus;
    return t.runningWaitingForMarketData;
  }

  function actionLabel(strategy: StrategyInstance) {
    if (pendingAction === `pause:${strategy.id}`) return t.pausing;
    if (pendingAction === `resume:${strategy.id}`) return t.resuming;
    return strategy.enabled ? t.pause : t.resume;
  }

  return (
    <div className="stack">
      <section className="toolbar strategyToolbar">
        <button
          className={health?.global_pause ? "primaryButton" : "dangerButton"}
          disabled={pendingAction !== null}
          onClick={() => void setGlobalPause(!health?.global_pause)}
        >
          {health?.global_pause ? <Play size={16} /> : <CirclePause size={16} />}
          <span>
            {pendingAction === "global-pause"
              ? t.pausing
              : pendingAction === "global-resume"
                ? t.resuming
                : health?.global_pause
                  ? t.globalResume
                  : t.globalPause}
          </span>
        </button>
        <button
          className="dangerButton"
          disabled={pendingAction !== null || liveMode}
          onClick={() => void flatten()}
          title={liveMode ? t.liveFlattenDisabled : t.globalFlatten}
        >
          {pendingAction === "global-flatten" ? t.flattening : t.globalFlatten}
        </button>
        {liveMode && <span className="muted">{t.liveFlattenDisabled}</span>}
        {statusMessage && <span className="statusMessage">{statusMessage}</span>}
      </section>
      <Table
        rows={strategies.map((strategy) => ({
          ...strategy,
          status: statusFor(strategy),
          action: (
            <div className="rowActions">
              <button
                className="smallButton"
                disabled={pendingAction !== null}
                onClick={() => void toggle(strategy)}
              >
                {actionLabel(strategy)}
              </button>
              <button
                className="dangerButton"
                disabled={pendingAction !== null || liveMode}
                onClick={() => void flatten(strategy)}
                title={liveMode ? t.liveFlattenDisabled : t.flatten}
              >
                {pendingAction === `flatten:${strategy.id}` ? t.flattening : t.flatten}
              </button>
            </div>
          )
        }))}
        columns={["symbol", "strategy_name", "status", "action"]}
        empty={t.noRows}
        t={t}
      />
    </div>
  );
}

function Market({ strategies, t }: { strategies: StrategyInstance[]; t: typeof dictionary.en }) {
  const symbols = useMemo(() => {
    const unique = new Set<string>();
    strategies.forEach((strategy) => {
      if (strategy.enabled) {
        unique.add(strategy.symbol);
      }
    });
    return [...unique].sort();
  }, [strategies]);
  const [selected, setSelected] = useState("");
  const [cursor, setCursor] = useState(0);
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  async function poll(symbol = selected, since = cursor) {
    if (!symbol) return;
    const response = await fetch(
      `/api/market/streams/by-symbol?symbol=${encodeURIComponent(symbol)}&since=${since}`
    );
    const rows: StreamMessage[] = await response.json();
    if (rows.length) {
      setMessages((current) => [...current, ...rows].slice(-20));
      setCursor(rows[rows.length - 1].sequence);
    }
  }

  async function switchSymbol(symbol: string) {
    setSelected(symbol);
    setMessages([]);
    const response = await fetch(
      `/api/market/streams/by-symbol?symbol=${encodeURIComponent(symbol)}`
    );
    const existing: StreamMessage[] = await response.json();
    setCursor(existing.length ? existing[existing.length - 1].sequence : 0);
  }

  async function clearVisible() {
    setMessages([]);
    const response = await fetch(
      `/api/market/streams/by-symbol?symbol=${encodeURIComponent(selected)}`
    );
    const existing: StreamMessage[] = await response.json();
    setCursor(existing.length ? existing[existing.length - 1].sequence : cursor);
  }

  useEffect(() => {
    if (!symbols.length) {
      setSelected("");
      setMessages([]);
      setCursor(0);
      return;
    }
    if (!selected || !symbols.includes(selected)) {
      setSelected(symbols[0]);
      setMessages([]);
      setCursor(0);
    }
  }, [selected, symbols]);

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
        <label className="fieldLabel">
          <span>{t.columnHeaders.symbol}</span>
          <select value={selected} onChange={(event) => void switchSymbol(event.target.value)}>
            {symbols.map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
          </select>
        </label>
        <button className="smallButton" disabled={!selected} onClick={() => void clearVisible()}>
          {t.clear}
        </button>
      </section>
      <section className="panel streamPanel" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="empty">{selected ? t.waitingForMarketData : t.noMessages}</div>
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
        t={t}
      />
      <Table
        rows={cycles}
        columns={["symbol", "status", "opened_at", "closed_at", "realized_pnl"]}
        empty={t.noRows}
        t={t}
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
        <Table rows={account.paper_account ? [account.paper_account] : []} columns={["starting_balance", "cash_balance", "realized_pnl", "unrealized_pnl", "current_equity", "peak_equity", "max_drawdown"]} empty={t.noRows} t={t} />
        <Table rows={account.positions ?? []} columns={["symbol", "side", "quantity", "average_price", "market_price", "unrealized_pnl"]} empty={t.noRows} t={t} />
        <Table rows={account.history ?? []} columns={["created_at", "event_type", "amount", "message"]} empty={t.noRows} t={t} />
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
      <Table rows={account?.balance ? [account.balance] : []} columns={["total_asset_currency", "total_cash_balance", "total_net_liquidation_value", "total_day_profit_loss"]} empty={t.noRows} t={t} />
      <Table rows={account?.positions ?? []} columns={["symbol", "quantity", "last_price", "unrealized_profit_loss"]} empty={t.noRows} t={t} />
      <Table rows={account?.open_orders ?? []} columns={["client_order_id", "combo_type", "orders"]} empty={t.noRows} t={t} />
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
        <button className="smallButton" onClick={() => void cleanup(true)}>{t.dryRun}</button>
        <button className="primaryButton" onClick={() => void cleanup(false)}>{t.cleanup}</button>
        <span className="muted">{String(storage?.database_bytes ?? 0)} bytes</span>
      </section>
      <Table rows={rows} columns={["activity", "orders", "fills", "cycles", "paper_account_events", "quote_snapshots", "bars"]} empty={t.noRows} t={t} />
    </div>
  );
}

function ActivityLog({ rows, t }: { rows: ActivityRow[]; t: typeof dictionary.en }) {
  return <Table rows={rows} columns={["ts", "level", "symbol", "event_type", "message"]} empty={t.noActivity} t={t} />;
}

function Table({
  rows,
  columns,
  empty,
  t
}: {
  rows: Record<string, unknown>[];
  columns: string[];
  empty: string;
  t: typeof dictionary.en;
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
                <th key={column}>{columnHeader(column, t)}</th>
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

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") return payload.detail;
  } catch {
    // Keep the user-facing fallback when the backend returns a non-JSON error.
  }
  return fallback;
}

function flattenSummaryMessage(summary: FlattenSummary, t: typeof dictionary.en): string {
  const parts = [
    `${t.cancelledPending}: ${summary.cancelled_pending}`,
    `${t.closedPositions}: ${summary.closed_positions}`
  ];
  if (summary.warnings.length) {
    parts.push(`${t.warnings}: ${summary.warnings.join("; ")}`);
  }
  return parts.join(" · ");
}

function renderCell(value: unknown): React.ReactNode {
  if (React.isValidElement(value)) return value;
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function columnHeader(column: string, t: typeof dictionary.en): string {
  return t.columnHeaders[column as keyof typeof t.columnHeaders] ?? humanizeColumn(column);
}

function humanizeColumn(column: string): string {
  return column
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

createRoot(document.getElementById("root")!).render(<App />);
