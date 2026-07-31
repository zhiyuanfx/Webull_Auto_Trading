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
  market_data_symbol: string;
  webull_symbol: string;
  account_alias: string;
  asset_class: string;
  live_execution_enabled: boolean;
  params: Record<string, unknown>;
};

type StrategyUpdateResult = StrategyInstance & {
  cancelled_pending: number;
};

type AccountPayload = {
  mode?: RuntimeMode;
  message?: string;
  configured?: boolean;
  account_alias?: string;
  account_id_configured?: boolean;
  balance?: Record<string, unknown>;
  positions?: Record<string, unknown>[];
  open_orders?: Record<string, unknown>[];
  error?: string;
  paper_account?: Record<string, unknown>;
  history?: Record<string, unknown>[];
};

type AccountAlias = {
  alias: string;
  configured: boolean;
  account_type?: string;
  accountType?: string;
  account_label?: string;
  account_class?: string;
};

type AccountAliasesPayload = {
  default_alias: string;
  aliases: AccountAlias[];
  legacy_account_id_configured: boolean;
};

type OrdersViewPayload =
  | {
      mode: "test";
      paper_orders: Record<string, unknown>[];
      paper_fills: Record<string, unknown>[];
      paper_cycles: Record<string, unknown>[];
    }
  | {
      mode: "live";
      account_alias: string;
      configured?: boolean;
      account_id_configured?: boolean;
      broker_open_orders: Record<string, unknown>[];
      broker_order_history: Record<string, unknown>[];
      live_intents: Record<string, unknown>[];
      live_virtual_orders: Record<string, unknown>[];
      live_cycles: Record<string, unknown>[];
      reconciliation_events: Record<string, unknown>[];
      error?: string | null;
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

type StreamState =
  | "disabled_missing_credentials"
  | "idle_no_symbols"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "error";

type StreamStatus = {
  state: StreamState;
  connected: boolean;
  desired_symbols: string[];
  subscribed_symbols: string[];
  last_message_at?: string | null;
  last_error?: string | null;
  reconnect_attempt: number;
};

type FlattenSummary = {
  strategy_id?: string | null;
  global_pause: boolean;
  paused_strategy_count: number;
  cancelled_pending: number;
  closed_positions: number;
  warnings: string[];
};

type StrategyResetSummary = {
  strategy_id: string;
  mode: RuntimeMode;
  enabled: boolean;
  completed: boolean;
  cancelled_pending: number;
  reconciled_allocations: number;
  completed_cycles: number;
  strategy_state_cleared: boolean;
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
      error_message: "Error",
      fill_price: "Entry fill",
      fills: "Fills",
      last_price: "Last price",
      level: "Level",
      live_execution_enabled: "Live execution",
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
      stop_price: "Entry trigger",
      take_profit: "Take profit",
      strategy_name: "Strategy",
      symbol: "Symbol",
      account_alias: "Account alias",
      asset_class: "Asset class",
      webull_symbol: "Webull symbol",
      total_asset_currency: "Total asset currency",
      total_cash_balance: "Total cash balance",
      total_day_profit_loss: "Day P&L",
      total_net_liquidation_value: "Net liquidation value",
      ts: "Time",
      unrealized_pnl: "Unrealized P&L",
      unrealized_profit_loss: "Unrealized P&L"
    },
    brokerOrderHistory: "Broker order history",
    brokerOpenOrders: "Broker open orders",
    clear: "Clear",
    configured: "Configured",
    controls: "Controls",
    cancelledPending: "Cancelled pending",
    cancelledVirtualEntries: "Pending virtual entries cancelled",
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
    liveCycles: "Live cycles",
    liveFlattenDisabled: "Live flatten is not available",
    liveIntents: "Live intents",
    liveVirtualOrders: "Live virtual orders",
    market: "Market Data",
    lastUpdated: "Last updated",
    noActivity: "No activity",
    noMessages: "No stream messages",
    noLiveOrders: "No live orders",
    noOrders: "No paper orders",
    noRows: "No rows",
    off: "Off",
    orders: "Orders",
    pause: "Pause",
    pauseSafety:
      "Pausing cancels only this strategy's pending virtual entries. It does not close broker exposure, and strategy risk management stops while paused.",
    pausing: "Pausing...",
    paused: "Paused",
    brokerExposureNotClosed: "Broker exposure was not closed.",
    paperAccount: "Paper account",
    recentIssues: "Recent Issues",
    reconcileAndReset: "Reconcile & Reset",
    refresh: "Refresh",
    refreshWebull: "Refresh Webull Reads",
    selectAccount: "Account",
    reset: "Reset",
    resetChecking: "Checking broker and reconciling...",
    resetConfirm:
      "This clears local EA state only. It does not close or cancel anything at Webull. In Live mode, manually close the position and wait for it to fill first.",
    resetComplete: "Reset complete",
    resetReconciledAllocation: "reconciled allocation",
    resetReconciledAllocations: "reconciled allocations",
    resetReadyToResume: "EA remains paused and is ready to resume",
    resetting: "Resetting...",
    resume: "Resume",
    resuming: "Resuming...",
    cleanup: "Cleanup",
    requestFailed: "Request failed",
    runningWaitingForMarketData: "Running, waiting for market data",
    runningStrategy: "Running strategy",
    service: "Service",
    status: "Status",
    storage: "Storage",
    streamMessages: {
      connectedNoMessages: "Waiting for market data...",
      connecting: "Stream is connecting...",
      error: "Stream error",
      idleNoSymbols: "No enabled strategy symbols are subscribed.",
      missingCredentials: "InsightSentry stream credentials are missing.",
      reconnecting: "Stream is reconnecting..."
    },
    streamState: "Stream",
    streamStates: {
      connected: "Connected",
      connecting: "Connecting",
      disabled_missing_credentials: "Missing credentials",
      error: "Error",
      idle_no_symbols: "No symbols",
      reconnecting: "Reconnecting"
    },
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
      error_message: "错误",
      fill_price: "入场成交价",
      fills: "成交",
      last_price: "最新价",
      level: "级别",
      live_execution_enabled: "实盘执行",
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
      stop_price: "入场触发价",
      take_profit: "止盈",
      strategy_name: "策略",
      symbol: "标的",
      account_alias: "账户别名",
      asset_class: "资产类别",
      webull_symbol: "Webull 标的",
      total_asset_currency: "资产币种",
      total_cash_balance: "总现金余额",
      total_day_profit_loss: "当日盈亏",
      total_net_liquidation_value: "总净清算价值",
      ts: "时间",
      unrealized_pnl: "未实现盈亏",
      unrealized_profit_loss: "未实现盈亏"
    },
    brokerOrderHistory: "券商历史订单",
    brokerOpenOrders: "券商未结订单",
    clear: "清除",
    configured: "已配置",
    controls: "控制",
    cancelledPending: "已取消挂单",
    cancelledVirtualEntries: "已取消待触发虚拟入场单",
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
    liveCycles: "实盘周期",
    liveFlattenDisabled: "实盘平仓暂不可用",
    liveIntents: "实盘意图",
    liveVirtualOrders: "实盘虚拟订单",
    market: "市场数据",
    lastUpdated: "上次刷新",
    noActivity: "暂无活动",
    noMessages: "暂无流消息",
    noLiveOrders: "暂无实盘订单",
    noOrders: "暂无纸面订单",
    noRows: "暂无数据",
    off: "关闭",
    orders: "订单",
    pause: "暂停",
    pauseSafety:
      "暂停只会取消此策略的待触发虚拟入场单，不会关闭券商持仓；暂停期间策略风险管理也会停止。",
    pausing: "暂停中...",
    paused: "暂停",
    brokerExposureNotClosed: "券商持仓未被关闭。",
    paperAccount: "纸面账户",
    recentIssues: "近期问题",
    reconcileAndReset: "核对并重置",
    refresh: "刷新",
    refreshWebull: "刷新 Webull 读取",
    selectAccount: "账户",
    reset: "重置",
    resetChecking: "正在检查券商状态并核对...",
    resetConfirm:
      "此操作只会清除本地 EA 状态，不会在 Webull 平仓或取消任何订单。实盘模式下，请先手动平仓并等待成交。",
    resetComplete: "重置完成",
    resetReconciledAllocation: "笔持仓已核对",
    resetReconciledAllocations: "笔持仓已核对",
    resetReadyToResume: "EA 保持暂停，可随时恢复",
    resetting: "重置中...",
    resume: "恢复",
    resuming: "恢复中...",
    cleanup: "清理",
    requestFailed: "请求失败",
    runningWaitingForMarketData: "运行中，等待市场数据",
    runningStrategy: "运行策略",
    service: "服务",
    status: "状态",
    storage: "存储",
    streamMessages: {
      connectedNoMessages: "等待市场数据...",
      connecting: "行情流正在连接...",
      error: "行情流错误",
      idleNoSymbols: "没有已启用的策略标的可订阅。",
      missingCredentials: "缺少 InsightSentry 行情流凭证。",
      reconnecting: "行情流正在重连..."
    },
    streamState: "行情流",
    streamStates: {
      connected: "已连接",
      connecting: "连接中",
      disabled_missing_credentials: "缺少凭证",
      error: "错误",
      idle_no_symbols: "无标的",
      reconnecting: "重连中"
    },
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
  const [ordersView, setOrdersView] = useState<OrdersViewPayload | null>(null);
  const [account, setAccount] = useState<AccountPayload | null>(null);
  const [accountAliases, setAccountAliases] = useState<AccountAliasesPayload | null>(null);
  const [selectedAccountAlias, setSelectedAccountAlias] = useState(
    () => localStorage.getItem("liveAccountAlias") || ""
  );
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
      const accountQuery = selectedAccountAlias
        ? `?account_alias=${encodeURIComponent(selectedAccountAlias)}`
        : "";
      const [
        healthRes,
        strategiesRes,
        ordersViewRes,
        accountRes,
        aliasesRes,
        activityRes,
        storageRes
      ] =
        await Promise.all([
          fetch("/api/health"),
          fetch("/api/strategies"),
          fetch(`/api/orders-view${accountQuery}`),
          fetch(`/api/account${accountQuery}`),
          fetch("/api/webull/account-aliases"),
          fetch("/api/activity"),
          fetch("/api/storage/stats")
      ]);
      setHealth(await healthRes.json());
      setStrategies(await strategiesRes.json());
      setOrdersView(await ordersViewRes.json());
      setAccount(await accountRes.json());
      const aliasesPayload = (await aliasesRes.json()) as AccountAliasesPayload;
      setAccountAliases(aliasesPayload);
      if (!selectedAccountAlias && aliasesPayload.default_alias) {
        setSelectedAccountAlias(aliasesPayload.default_alias);
        localStorage.setItem("liveAccountAlias", aliasesPayload.default_alias);
      }
      setActivity(await activityRes.json());
      setStorage(await storageRes.json());
      setLastUpdatedAt(new Date());
    } finally {
      setRefreshing(false);
    }
  }

  async function switchMode(mode: RuntimeMode) {
    setOrdersView(null);
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
    if (selectedAccountAlias) {
      localStorage.setItem("liveAccountAlias", selectedAccountAlias);
    }
  }, [selectedAccountAlias]);

  useEffect(() => {
    setOrdersView(null);
    void refresh();
    const handle = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(handle);
  }, [selectedAccountAlias]);

  const activeContent = useMemo(() => {
    switch (tab) {
      case "strategies":
        return <Strategies health={health} strategies={strategies} onChanged={refresh} t={t} />;
      case "market":
        return <Market strategies={strategies} t={t} />;
      case "orders":
        return (
          <Orders
            ordersView={ordersView}
            mode={health?.mode ?? "test"}
            aliases={accountAliases}
            selectedAlias={selectedAccountAlias}
            onSelectedAlias={setSelectedAccountAlias}
            t={t}
          />
        );
      case "account":
        return (
          <Account
            account={account}
            aliases={accountAliases}
            selectedAlias={selectedAccountAlias}
            onSelectedAlias={setSelectedAccountAlias}
            onAccount={setAccount}
            refresh={refresh}
            t={t}
          />
        );
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
  }, [tab, health, strategies, ordersView, account, accountAliases, selectedAccountAlias, activity, storage, t]);

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
      const result = (await response.json()) as StrategyUpdateResult;
      if (!nextEnabled) {
        setStatusMessage(pauseSummaryMessage(result.cancelled_pending, t));
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

  async function resetStrategy(strategy: StrategyInstance) {
    if (!window.confirm(t.resetConfirm)) return;
    const actionId = `reset:${strategy.id}`;
    setPendingAction(actionId);
    setStatusMessage("");
    setRowStatus((current) => ({
      ...current,
      [strategy.id]: liveMode ? t.resetChecking : t.resetting
    }));
    try {
      const response = await fetch(`/api/strategies/${strategy.id}/reset`, {
        method: "POST"
      });
      if (!response.ok) {
        throw new Error(await responseError(response, t.requestFailed));
      }
      const summary = (await response.json()) as StrategyResetSummary;
      setStatusMessage(resetSummaryMessage(summary, t));
      setRowStatus((current) => ({ ...current, [strategy.id]: t.paused }));
      await onChanged();
    } catch (error) {
      const message = error instanceof Error ? error.message : t.requestFailed;
      setStatusMessage(message);
      setRowStatus((current) => ({ ...current, [strategy.id]: message }));
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
      <p className="muted">{t.pauseSafety}</p>
      <Table
        rows={strategies.map((strategy) => ({
          ...strategy,
          live_execution_enabled: strategy.live_execution_enabled ? t.enabled : t.off,
          status: statusFor(strategy),
          action: (
            <div className="rowActions">
              <button
                className="smallButton"
                disabled={pendingAction !== null}
                onClick={() => void toggle(strategy)}
                title={strategy.enabled ? t.pauseSafety : t.resume}
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
              <button
                className="smallButton"
                disabled={pendingAction !== null || strategy.enabled}
                onClick={() => void resetStrategy(strategy)}
                title={t.resetConfirm}
              >
                {pendingAction === `reset:${strategy.id}`
                  ? liveMode
                    ? t.resetChecking
                    : t.resetting
                  : liveMode
                    ? t.reconcileAndReset
                    : t.reset}
              </button>
            </div>
          )
        }))}
        columns={
          liveMode
            ? [
                "symbol",
                "strategy_name",
                "status",
                "account_alias",
                "asset_class",
                "webull_symbol",
                "live_execution_enabled",
                "action"
              ]
            : ["symbol", "strategy_name", "status", "action"]
        }
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
  const [streamStatus, setStreamStatus] = useState<StreamStatus | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  async function pollStatus() {
    const response = await fetch("/api/market/stream-status");
    setStreamStatus(await response.json());
  }

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
    void pollStatus();
    const handle = window.setInterval(() => void pollStatus(), 1500);
    return () => window.clearInterval(handle);
  }, []);

  useEffect(() => {
    void poll();
    const handle = window.setInterval(() => void poll(), 1500);
    return () => window.clearInterval(handle);
  }, [selected, cursor]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const streamState = streamStatus?.state ?? "connecting";
  const emptyMessage =
    streamState === "disabled_missing_credentials"
      ? t.streamMessages.missingCredentials
      : streamState === "idle_no_symbols"
        ? t.streamMessages.idleNoSymbols
        : streamState === "connecting"
          ? t.streamMessages.connecting
          : streamState === "reconnecting"
            ? streamStatus?.last_error || t.streamMessages.reconnecting
            : streamState === "error"
              ? streamStatus?.last_error || t.streamMessages.error
              : selected
                ? t.streamMessages.connectedNoMessages
                : t.noMessages;
  const subscribedSymbols = streamStatus?.subscribed_symbols ?? [];

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
      <section className="statusStrip">
        <span>
          {t.streamState}: {t.streamStates[streamState]}
        </span>
        <span>
          {t.columnHeaders.symbol}:{" "}
          {subscribedSymbols.length ? subscribedSymbols.join(", ") : t.noRows}
        </span>
        {streamStatus?.last_error ? <span>{streamStatus.last_error}</span> : null}
      </section>
      <section className="panel streamPanel" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="empty">{emptyMessage}</div>
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
  ordersView,
  mode,
  aliases,
  selectedAlias,
  onSelectedAlias,
  t
}: {
  ordersView: OrdersViewPayload | null;
  mode: RuntimeMode;
  aliases: AccountAliasesPayload | null;
  selectedAlias: string;
  onSelectedAlias: (alias: string) => void;
  t: typeof dictionary.en;
}) {
  if (!ordersView || ordersView.mode !== mode) {
    return (
      <div className="stack">
        <Table rows={[]} columns={["symbol", "side", "status"]} empty={t.noRows} t={t} />
      </div>
    );
  }
  if (ordersView.mode === "live") {
    const liveAliases = aliases?.aliases ?? [];
    const activeAlias = selectedAlias || aliases?.default_alias || ordersView.account_alias || "";
    return (
      <div className="stack">
        <section className="toolbar">
          <label className="fieldLabel">
            <span>{t.selectAccount}</span>
            <select value={activeAlias} onChange={(event) => onSelectedAlias(event.target.value)}>
              {!liveAliases.length && <option value="">{t.noRows}</option>}
              {liveAliases.map((alias) => (
                <option key={alias.alias} value={alias.alias}>
                  {accountAliasLabel(alias)}
                </option>
              ))}
            </select>
          </label>
        </section>
        {ordersView.error ? (
          <section className="alert">
            <ShieldAlert size={18} />
            <span>{ordersView.error}</span>
          </section>
        ) : null}
        <section className="panel tablePanel">
          <h2>{t.brokerOpenOrders}</h2>
          <InnerTable
            rows={ordersView.broker_open_orders}
            columns={["client_order_id", "combo_type", "orders"]}
            empty={t.noLiveOrders}
            t={t}
          />
        </section>
        <section className="panel tablePanel">
          <h2>{t.brokerOrderHistory}</h2>
          <InnerTable
            rows={ordersView.broker_order_history}
            columns={["client_order_id", "combo_type", "orders"]}
            empty={t.noLiveOrders}
            t={t}
          />
        </section>
        <section className="panel tablePanel">
          <h2>{t.liveIntents}</h2>
          <InnerTable
            rows={ordersView.live_intents}
            columns={[
              "client_order_id",
              "action",
              "side",
              "quantity",
              "webull_symbol",
              "status",
              "created_at",
              "error_message"
            ]}
            empty={t.noLiveOrders}
            t={t}
          />
        </section>
        <section className="panel tablePanel">
          <h2>{t.liveVirtualOrders}</h2>
          <InnerTable
            rows={ordersView.live_virtual_orders}
            columns={[
              "symbol",
              "side",
              "role",
              "status",
              "quantity",
              "stop_price",
              "fill_price",
              "stop_loss",
              "take_profit"
            ]}
            empty={t.noLiveOrders}
            t={t}
          />
        </section>
        <section className="panel tablePanel">
          <h2>{t.liveCycles}</h2>
          <InnerTable
            rows={ordersView.live_cycles}
            columns={["symbol", "status", "opened_at", "closed_at", "realized_pnl"]}
            empty={t.noRows}
            t={t}
          />
        </section>
      </div>
    );
  }
  return (
    <div className="stack">
      <Table
        rows={ordersView.paper_orders}
        columns={[
          "symbol",
          "side",
          "role",
          "status",
          "quantity",
          "stop_price",
          "fill_price",
          "stop_loss",
          "take_profit"
        ]}
        empty={t.noOrders}
        t={t}
      />
      <Table
        rows={ordersView.paper_cycles}
        columns={["symbol", "status", "opened_at", "closed_at", "realized_pnl"]}
        empty={t.noRows}
        t={t}
      />
    </div>
  );
}

function Account({
  account,
  aliases,
  selectedAlias,
  onSelectedAlias,
  onAccount,
  refresh,
  t
}: {
  account: AccountPayload | null;
  aliases: AccountAliasesPayload | null;
  selectedAlias: string;
  onSelectedAlias: (alias: string) => void;
  onAccount: (account: AccountPayload) => void;
  refresh: () => Promise<void>;
  t: typeof dictionary.en;
}) {
  const [deposit, setDeposit] = useState("1000");
  const liveAliases = aliases?.aliases ?? [];
  const activeAlias = selectedAlias || aliases?.default_alias || account?.account_alias || "";
  async function refreshAccount(alias = activeAlias) {
    const query = new URLSearchParams({ refresh: "true" });
    if (alias) query.set("account_alias", alias);
    const response = await fetch(`/api/account?${query.toString()}`);
    onAccount(await response.json());
  }
  async function changeLiveAccount(alias: string) {
    onSelectedAlias(alias);
    await refreshAccount(alias);
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
        <label className="fieldLabel">
          <span>{t.selectAccount}</span>
          <select
            value={activeAlias}
            onChange={(event) => void changeLiveAccount(event.target.value)}
          >
            {!liveAliases.length && <option value="">{t.noRows}</option>}
            {liveAliases.map((item) => (
              <option key={item.alias} value={item.alias}>
                {accountAliasLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <button className="primaryButton" onClick={() => void refreshAccount()}>
          <RefreshCw size={16} />
          <span>{t.refreshWebull}</span>
        </button>
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
      <InnerTable rows={rows} columns={columns} empty={empty} t={t} />
    </section>
  );
}

function InnerTable({
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
  return rows.length === 0 ? (
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

function pauseSummaryMessage(cancelledPending: number, t: typeof dictionary.en): string {
  return [
    t.paused,
    `${t.cancelledVirtualEntries}: ${cancelledPending}`,
    t.brokerExposureNotClosed
  ].join(" · ");
}

function resetSummaryMessage(
  summary: StrategyResetSummary,
  t: typeof dictionary.en
): string {
  const allocationLabel =
    summary.reconciled_allocations === 1
      ? t.resetReconciledAllocation
      : t.resetReconciledAllocations;
  return `${t.resetComplete}; ${summary.reconciled_allocations} ${allocationLabel}. ${t.resetReadyToResume}.`;
}

function accountAliasLabel(account: AccountAlias): string {
  const details = account.account_label || account.account_class || account.account_type || account.accountType;
  return details ? `${account.alias} (${details})` : account.alias;
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
