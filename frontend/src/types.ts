export type Theme = "light" | "dark";
export type Page = "dashboard" | "route" | "orders" | "positions" | "activity" | "settings";

export interface Route {
  route_id: string;
  name: string;
  account_id: string;
  enabled: boolean;
  allowed_symbols: string[];
  max_quantity: string;
  max_notional: string;
  accepted_order_types: string[];
  created_at?: string;
  updated_at?: string;
  secret_configured: boolean;
}

export interface Health {
  status: string;
  database: string;
  webull_configured: boolean;
  execution_enabled: boolean;
  token_dir: string;
  routes: Route[];
}

export interface EventRow {
  id: number;
  route_id: string;
  event_id: string;
  action: string;
  symbol?: string;
  status: string;
  payload: Record<string, unknown>;
  normalized: Record<string, unknown>;
  error?: string;
  created_at: string;
  updated_at: string;
}

export interface OrderRow {
  id: number;
  event_pk: number;
  route_id: string;
  account_id: string;
  client_order_id?: string;
  target_client_order_id?: string;
  webull_order_id?: string;
  action: string;
  symbol?: string;
  side?: string;
  status: string;
  order: Record<string, unknown>;
  preview: Record<string, unknown>;
  response: Record<string, unknown>;
  error?: string;
  created_at: string;
  updated_at: string;
}

export interface ActivityRow {
  id: number;
  kind: string;
  level: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface PositionsResponse {
  account_id: string;
  positions: Array<Record<string, unknown>>;
  stale?: boolean;
  error?: string;
}
