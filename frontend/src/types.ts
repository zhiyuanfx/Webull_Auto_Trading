export type Language = "en" | "zh";
export type Theme = "light" | "dark";
export type Page = "dashboard" | "strategies" | "accounts" | "system";

export interface Plugin {
  id: string;
  version: string;
  name: { en: string; zh_cn: string };
  description: { en: string; zh_cn: string };
  assets: string[];
  parameters: Record<string, unknown>;
  source_hash: string;
}

export interface StrategyInstance {
  id: string;
  plugin_id: string;
  plugin_version: string;
  plugin_source_hash: string;
  mode: "LOCAL_SIM" | "WEBULL_UAT" | "WEBULL_LIVE";
  account_id: string;
  feed_source: string;
  state: string;
  config: {
    name: string;
    symbols: string[];
    parameters: Record<string, unknown>;
  };
  worker?: { alive: boolean; pid: number };
}

export interface SimAccount {
  id: string;
  name: string;
  initial_cash: string;
  cash: string;
  commission_per_unit: string;
  slippage_bps: string;
  latency_ms: number;
  partial_fills: number;
  leverage: string;
  futures_margin_per_contract: string;
}

export interface Position {
  strategy_instance_id: string;
  symbol: string;
  quantity: string;
  average_price: string;
  realized_pnl: string;
}

export interface Health {
  status: string;
  database: string;
  uat_configured: boolean;
  production_configured: boolean;
  live_enabled: boolean;
  workers: unknown[];
  feed_age_ms: number | null;
  market_connections: Array<{ feed: string; category: string }>;
}

export interface StrategyDetail {
  instance: StrategyInstance;
  orders: Array<{ id: string; status: string; filled_quantity: string; command: { symbol: string; side: string; quantity: string; order_type: string; origin: string } }>;
  fills: Array<{ id: string; symbol: string; side: string; quantity: string; price: string; filled_at: string }>;
  positions: Position[];
  runs: Array<{ id: string; status: string; started_at: string; ended_at?: string }>;
  events: Array<{ id: number; kind: string; created_at: string; payload: Record<string, unknown> }>;
  expiry_state: string | null;
}
