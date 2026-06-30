import { Page } from "./types";

export type Language = "en" | "zh";

const en = {
  language: {
    ariaLabel: "Language",
    english: "EN",
    chinese: "简中"
  },
  app: {
    brandSubtitle: "TradingView intake",
    subtitle: "Local live bridge for TradingView webhook instructions",
    apiStatus: (status: string) => `API ${status}`,
    loading: "...",
    executionOn: "EXECUTION ON",
    paused: "PAUSED",
    themeLabel: "Theme"
  },
  pages: {
    dashboard: "Dashboard",
    route: "Webhook Route",
    orders: "Orders",
    positions: "Positions",
    activity: "Activity",
    settings: "Settings"
  } satisfies Record<Page, string>,
  common: {
    actions: "Actions",
    action: "Action",
    close: "Close",
    cost: "Cost",
    details: "Details",
    event: "Event",
    last: "Last",
    refresh: "Refresh",
    retry: "Retry",
    save: "Save route",
    side: "Side",
    status: "Status",
    symbol: "Symbol",
    updated: "Updated",
    quantity: "Quantity",
    profitLoss: "P/L"
  },
  dashboard: {
    metrics: {
      execution: "Execution",
      executionOn: "On",
      executionPaused: "Paused",
      globalSafetySwitch: "Global safety switch",
      routes: "Routes",
      routesEnabled: (count: number) => `${count} enabled`,
      queued: "Queued",
      waitingForExecution: "Waiting for execution",
      failures: "Failures",
      needsReview: "Needs review"
    },
    bridgeStatus: "Bridge Status",
    bridgeStatusSubtitle: "Fast intake, durable local history, live Webull execution",
    pauseExecution: "Pause execution",
    enableExecution: "Enable execution",
    emergencyPause: "Emergency pause",
    promptPauseAll: "Type PAUSE ALL",
    status: {
      webullCredentials: "Webull credentials",
      configured: "Configured",
      missing: "Missing",
      database: "Database",
      latestSubmittedOrders: "Latest submitted orders",
      tokenCache: "Token cache"
    }
  },
  route: {
    empty: "No route has been initialized.",
    settingsTitle: "Route Settings",
    routeId: "Route ID",
    name: "Name",
    accountId: "Webull account ID",
    rotateSecret: "Rotate shared secret",
    secretKeep: "Leave blank to keep current secret",
    secretRequired: "Required before use",
    allowedSymbols: "Allowed symbols",
    allowedSymbolsPlaceholder: "1OZ, AAPL",
    maxQuantity: "Max quantity",
    maxNotional: "Max notional",
    acceptedOrderTypes: "Accepted order types",
    enabled: "Route enabled",
    webhook: "Webhook"
  },
  orders: {
    events: "Webhook Events",
    recentEvents: (count: number) => `${count} recent events`,
    orders: "Orders",
    recentOrders: (count: number) => `${count} recent order records`,
    clientId: "Client ID",
    webullId: "Webull ID"
  },
  positions: {
    title: "Webull Positions",
    noAccount: "No account selected",
    staleSnapshot: "stale snapshot",
    flatten: "Flatten",
    promptFlatten: (confirmation: string) => `Type ${confirmation}`,
    promptQuantity: "Quantity to flatten"
  },
  activity: {
    title: "Activity",
    subtitle: "Sanitized local audit trail"
  },
  settings: {
    webull: "Webull",
    ready: "Ready",
    productionSdk: "Production SDK",
    globalSwitch: "Global switch",
    webhookConfigs: "Webhook configs",
    runtime: "Runtime",
    database: "Database",
    tokenCache: "Token cache",
    httpsTunnel: "HTTPS tunnel",
    userManaged: "User managed",
    emergency: "Emergency",
    cancelKnownHelp: "Cancel known open orders uses locally recorded client_order_id values. Review Webull directly if network or token errors occur.",
    cancelKnown: "Cancel known open orders",
    promptCancelKnown: (confirmation: string) => `Type ${confirmation}`
  }
};

const zh: typeof en = {
  language: {
    ariaLabel: "语言",
    english: "EN",
    chinese: "简中"
  },
  app: {
    brandSubtitle: "TradingView 接收",
    subtitle: "本地实时桥接 TradingView Webhook 指令",
    apiStatus: (status: string) => `API ${status}`,
    loading: "...",
    executionOn: "执行开启",
    paused: "已暂停",
    themeLabel: "主题"
  },
  pages: {
    dashboard: "仪表盘",
    route: "Webhook 路由",
    orders: "订单",
    positions: "持仓",
    activity: "活动",
    settings: "设置"
  },
  common: {
    actions: "操作",
    action: "动作",
    close: "关闭",
    cost: "成本",
    details: "详情",
    event: "事件",
    last: "最新价",
    refresh: "刷新",
    retry: "重试",
    save: "保存路由",
    side: "方向",
    status: "状态",
    symbol: "标的",
    updated: "更新时间",
    quantity: "数量",
    profitLoss: "盈亏"
  },
  dashboard: {
    metrics: {
      execution: "执行",
      executionOn: "开启",
      executionPaused: "暂停",
      globalSafetySwitch: "全局安全开关",
      routes: "路由",
      routesEnabled: (count: number) => `${count} 个已启用`,
      queued: "队列",
      waitingForExecution: "等待执行",
      failures: "失败",
      needsReview: "需要检查"
    },
    bridgeStatus: "桥接状态",
    bridgeStatusSubtitle: "快速接收、本地持久历史、实时 Webull 执行",
    pauseExecution: "暂停执行",
    enableExecution: "启用执行",
    emergencyPause: "紧急暂停",
    promptPauseAll: "请输入 PAUSE ALL",
    status: {
      webullCredentials: "Webull 凭证",
      configured: "已配置",
      missing: "缺失",
      database: "数据库",
      latestSubmittedOrders: "最新已提交订单",
      tokenCache: "Token 缓存"
    }
  },
  route: {
    empty: "尚未初始化路由。",
    settingsTitle: "路由设置",
    routeId: "路由 ID",
    name: "名称",
    accountId: "Webull 账户 ID",
    rotateSecret: "轮换共享密钥",
    secretKeep: "留空以保留当前密钥",
    secretRequired: "使用前必须填写",
    allowedSymbols: "允许的标的",
    allowedSymbolsPlaceholder: "1OZ, AAPL",
    maxQuantity: "最大数量",
    maxNotional: "最大名义金额",
    acceptedOrderTypes: "接受的订单类型",
    enabled: "启用路由",
    webhook: "Webhook"
  },
  orders: {
    events: "Webhook 事件",
    recentEvents: (count: number) => `最近 ${count} 个事件`,
    orders: "订单",
    recentOrders: (count: number) => `最近 ${count} 条订单记录`,
    clientId: "Client ID",
    webullId: "Webull ID"
  },
  positions: {
    title: "Webull 持仓",
    noAccount: "未选择账户",
    staleSnapshot: "快照已过期",
    flatten: "平仓",
    promptFlatten: (confirmation: string) => `请输入 ${confirmation}`,
    promptQuantity: "平仓数量"
  },
  activity: {
    title: "活动",
    subtitle: "已脱敏的本地审计记录"
  },
  settings: {
    webull: "Webull",
    ready: "就绪",
    productionSdk: "Production SDK",
    globalSwitch: "全局开关",
    webhookConfigs: "Webhook 配置",
    runtime: "运行环境",
    database: "数据库",
    tokenCache: "Token 缓存",
    httpsTunnel: "HTTPS 隧道",
    userManaged: "用户管理",
    emergency: "紧急操作",
    cancelKnownHelp: "取消已知未完成订单会使用本地记录的 client_order_id 值。如果发生网络或 token 错误，请直接在 Webull 中核对。",
    cancelKnown: "取消已知未完成订单",
    promptCancelKnown: (confirmation: string) => `请输入 ${confirmation}`
  }
};

export type Translation = typeof en;

export const translations: Record<Language, Translation> = { en, zh };

export function isLanguage(value: string | null): value is Language {
  return value === "en" || value === "zh";
}
