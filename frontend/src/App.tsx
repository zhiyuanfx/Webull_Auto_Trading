import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ActivityRow, EventRow, Health, OrderRow, Page, PositionsResponse, Route, Theme } from "./types";

const API = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  }
  return response.json();
}

const pageLabels: Record<Page, string> = {
  dashboard: "Dashboard",
  route: "Webhook Route",
  orders: "Orders",
  positions: "Positions",
  activity: "Activity",
  settings: "Settings"
};

function App() {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("theme") as Theme) || "light");
  const [page, setPage] = useState<Page>("dashboard");
  const [health, setHealth] = useState<Health | null>(null);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [positions, setPositions] = useState<PositionsResponse>({ account_id: "", positions: [] });
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      const [nextHealth, nextRoutes, nextEvents, nextOrders, nextActivity] = await Promise.all([
        request<Health>("/health"),
        request<Route[]>("/routes"),
        request<EventRow[]>("/events"),
        request<OrderRow[]>("/orders"),
        request<ActivityRow[]>("/activity")
      ]);
      setHealth(nextHealth);
      setRoutes(nextRoutes);
      setEvents(nextEvents);
      setOrders(nextOrders);
      setActivity(nextActivity);
      setError("");
    } catch (reason) {
      setError(String(reason));
    }
  }, []);

  const refreshPositions = useCallback(async () => {
    try {
      const nextPositions = await request<PositionsResponse>("/positions");
      setPositions(nextPositions);
      setError("");
    } catch (reason) {
      setError(String(reason));
    }
  }, []);

  useEffect(() => {
    reload();
    refreshPositions();
    const id = window.setInterval(reload, 3000);
    return () => window.clearInterval(id);
  }, [reload, refreshPositions]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  const activeRoute = routes[0];
  const failures = events.filter(item => ["failed", "rejected", "validation_failed", "unknown"].includes(item.status));
  const queued = events.filter(item => item.status === "queued").length;
  const submitted = orders.filter(item => item.status === "submitted").length;

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">WB</div><div><strong>Webull Bridge</strong><span>TradingView intake</span></div></div>
      <nav>{(Object.keys(pageLabels) as Page[]).map(item =>
        <button className={page === item ? "active" : ""} onClick={() => setPage(item)} key={item}>
          <span className="nav-dot" />{pageLabels[item]}
        </button>)}</nav>
      <div className="sidebar-foot"><span className="status-dot" /> API {health?.status || "..."}</div>
    </aside>
    <main>
      <header>
        <div><h1>{pageLabels[page]}</h1><p>Local live bridge for TradingView webhook instructions</p></div>
        <div className="header-actions">
          <span className={`mode-badge ${health?.execution_enabled ? "live" : ""}`}>{health?.execution_enabled ? "EXECUTION ON" : "PAUSED"}</span>
          <button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label="theme">{theme === "light" ? "◐" : "○"}</button>
        </div>
      </header>
      {error && <div className="error-banner">{error}</div>}
      {page === "dashboard" && <Dashboard health={health} routes={routes} queued={queued} submitted={submitted} failures={failures.length} onReload={reload} />}
      {page === "route" && <RoutePage route={activeRoute} onReload={reload} />}
      {page === "orders" && <OrdersPage events={events} orders={orders} onReload={reload} />}
      {page === "positions" && <PositionsPage routes={routes} positions={positions} refresh={refreshPositions} />}
      {page === "activity" && <ActivityPage rows={activity} />}
      {page === "settings" && <SettingsPage health={health} onReload={reload} />}
    </main>
  </div>;
}

function Dashboard({ health, routes, queued, submitted, failures, onReload }: { health: Health | null; routes: Route[]; queued: number; submitted: number; failures: number; onReload: () => void }) {
  async function toggleExecution() {
    await request("/settings/execution", { method: "PUT", body: JSON.stringify({ enabled: !health?.execution_enabled }) });
    onReload();
  }
  async function emergencyPause() {
    if (window.prompt("Type PAUSE ALL") !== "PAUSE ALL") return;
    await request("/emergency/pause", { method: "POST", body: JSON.stringify({ confirmation: "PAUSE ALL" }) });
    onReload();
  }
  return <>
    <section className="metrics">
      <Metric label="Execution" value={health?.execution_enabled ? "On" : "Paused"} note="Global safety switch" positive={health?.execution_enabled} />
      <Metric label="Routes" value={String(routes.length)} note={`${routes.filter(item => item.enabled).length} enabled`} />
      <Metric label="Queued" value={String(queued)} note="Waiting for execution" />
      <Metric label="Failures" value={String(failures)} note="Needs review" positive={failures === 0} />
    </section>
    <section className="panel"><div className="panel-title"><div><h2>Bridge Status</h2><p>Fast intake, durable local history, live Webull execution</p></div><div className="button-row"><button className={health?.execution_enabled ? "secondary" : "primary"} onClick={toggleExecution}>{health?.execution_enabled ? "Pause execution" : "Enable execution"}</button><button className="danger" onClick={emergencyPause}>Emergency pause</button></div></div>
      <div className="status-grid">
        <Status label="Webull credentials" value={health?.webull_configured ? "Configured" : "Missing"} ok={!!health?.webull_configured} />
        <Status label="Database" value={health?.database || "..."} ok />
        <Status label="Latest submitted orders" value={String(submitted)} ok />
        <Status label="Token cache" value={health?.token_dir || "..."} ok />
      </div>
    </section>
  </>;
}

function RoutePage({ route, onReload }: { route?: Route; onReload: () => void }) {
  const sample = useMemo(() => JSON.stringify({
    secret: "route-shared-secret",
    event_id: "strategy-{{timenow}}-{{bar_index}}",
    action: "BUY",
    symbol: "1OZ",
    quantity: "1",
    order_type: "MARKET",
    strategy: "Pine strategy name",
    alert: "long-entry",
    timeframe: "{{interval}}"
  }, null, 2), []);
  if (!route) return <section className="panel"><div className="empty">No route has been initialized.</div></section>;
  const activeRoute = route;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const secret = String(data.get("secret") || "");
    await request(`/routes/${activeRoute.route_id}`, {
      method: "PUT",
      body: JSON.stringify({
        name: data.get("name"),
        account_id: data.get("account_id"),
        enabled: data.get("enabled") === "on",
        secret: secret || undefined,
        allowed_symbols: String(data.get("allowed_symbols") || "").split(",").map(item => item.trim()).filter(Boolean),
        max_quantity: data.get("max_quantity"),
        max_notional: data.get("max_notional"),
        accepted_order_types: String(data.get("accepted_order_types") || "").split(",").map(item => item.trim()).filter(Boolean)
      })
    });
    onReload();
  }
  return <div className="two-column"><section className="panel form-panel"><h2>Route Settings</h2><form onSubmit={submit}>
    <label>Route ID<input value={route.route_id} disabled /></label>
    <label>Name<input name="name" defaultValue={route.name} required /></label>
    <label>Webull account ID<input name="account_id" defaultValue={route.account_id} required /></label>
    <label>Rotate shared secret<input name="secret" placeholder={route.secret_configured ? "Leave blank to keep current secret" : "Required before use"} /></label>
    <label>Allowed symbols<input name="allowed_symbols" defaultValue={route.allowed_symbols.join(", ")} placeholder="1OZ, AAPL" /></label>
    <div className="form-row"><label>Max quantity<input name="max_quantity" defaultValue={route.max_quantity} /></label><label>Max notional<input name="max_notional" defaultValue={route.max_notional} /></label></div>
    <label>Accepted order types<input name="accepted_order_types" defaultValue={route.accepted_order_types.join(", ")} /></label>
    <label className="check"><input name="enabled" type="checkbox" defaultChecked={route.enabled} />Route enabled</label>
    <button className="primary">Save route</button>
  </form></section>
  <section className="panel"><div className="panel-title"><div><h2>Webhook</h2><p>POST /webhook/tradingview/{route.route_id}</p></div></div><pre>{sample}</pre></section></div>;
}

function OrdersPage({ events, orders, onReload }: { events: EventRow[]; orders: OrderRow[]; onReload: () => void }) {
  const [selected, setSelected] = useState<EventRow | null>(null);
  async function retry(item: EventRow) {
    await request(`/events/${item.id}/retry`, { method: "POST" });
    onReload();
  }
  return <><section className="panel"><div className="panel-title"><div><h2>Webhook Events</h2><p>{events.length} recent events</p></div><button className="secondary" onClick={onReload}>Refresh</button></div>
    <div className="table-wrap"><table><thead><tr><th>Event</th><th>Action</th><th>Symbol</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead><tbody>
      {events.map(item => <tr key={item.id}><td><strong>{item.event_id}</strong><small>{item.route_id}</small></td><td>{item.action}</td><td>{item.symbol || "-"}</td><td><span className={`tag ${item.status}`}>{item.status}</span></td><td>{new Date(item.updated_at).toLocaleString()}</td><td><div className="button-row"><button className="secondary" onClick={() => setSelected(item)}>Details</button>{["failed", "unknown"].includes(item.status) && <button className="secondary" onClick={() => retry(item)}>Retry</button>}</div></td></tr>)}
    </tbody></table></div></section>
    <section className="panel"><div className="panel-title"><div><h2>Orders</h2><p>{orders.length} recent order records</p></div></div><div className="table-wrap"><table><thead><tr><th>Client ID</th><th>Side</th><th>Symbol</th><th>Status</th><th>Webull ID</th></tr></thead><tbody>{orders.map(item => <tr key={item.id}><td><strong>{item.client_order_id || item.target_client_order_id || "-"}</strong><small>{item.account_id}</small></td><td>{item.side || item.action}</td><td>{item.symbol || "-"}</td><td><span className={`tag ${item.status}`}>{item.status}</span></td><td>{item.webull_order_id || "-"}</td></tr>)}</tbody></table></div></section>
    {selected && <section className="panel detail-panel"><div className="panel-title"><div><h2>{selected.event_id}</h2><p>{selected.status}</p></div><button className="secondary" onClick={() => setSelected(null)}>Close</button></div><pre>{JSON.stringify(selected, null, 2)}</pre></section>}</>;
}

function PositionsPage({ routes, positions, refresh }: { routes: Route[]; positions: PositionsResponse; refresh: () => void }) {
  const route = routes[0];
  async function flatten(symbol: string) {
    const confirmation = `FLATTEN ${symbol}`;
    if (window.prompt(`Type ${confirmation}`) !== confirmation) return;
    const quantity = window.prompt("Quantity to flatten", "1") || "1";
    await request(`/routes/${route.route_id}/flatten/${symbol}?quantity=${encodeURIComponent(quantity)}`, { method: "POST", body: JSON.stringify({ confirmation }) });
    refresh();
  }
  return <section className="panel"><div className="panel-title"><div><h2>Webull Positions</h2><p>{positions.account_id || "No account selected"}{positions.stale ? " - stale snapshot" : ""}</p></div><button className="secondary" onClick={refresh}>Refresh</button></div>
    {positions.error && <div className="error-banner">{positions.error}</div>}
    <div className="table-wrap"><table><thead><tr><th>Symbol</th><th>Quantity</th><th>Cost</th><th>Last</th><th>P/L</th><th>Actions</th></tr></thead><tbody>{positions.positions.map((item, index) => { const symbol = String(item.symbol || ""); return <tr key={index}><td><strong>{symbol || "-"}</strong><small>{String(item.instrument_type || "")}</small></td><td>{String(item.quantity || "-")}</td><td>{String(item.cost_price || "-")}</td><td>{String(item.last_price || "-")}</td><td>{String(item.unrealized_profit_loss || "-")}</td><td>{route && symbol && <button className="danger" onClick={() => flatten(symbol)}>Flatten</button>}</td></tr>; })}</tbody></table></div>
  </section>;
}

function ActivityPage({ rows }: { rows: ActivityRow[] }) {
  return <section className="panel"><div className="panel-title"><div><h2>Activity</h2><p>Sanitized local audit trail</p></div></div><div className="activity-list">{rows.map(item => <article key={item.id}><span className={`tag ${item.level}`}>{item.kind}</span><div><strong>{item.message}</strong><small>{new Date(item.created_at).toLocaleString()}</small></div></article>)}</div></section>;
}

function SettingsPage({ health, onReload }: { health: Health | null; onReload: () => void }) {
  async function cancelKnown() {
    const confirmation = "CANCEL KNOWN OPEN ORDERS";
    if (window.prompt(`Type ${confirmation}`) !== confirmation) return;
    await request("/emergency/cancel-known", { method: "POST", body: JSON.stringify({ confirmation }) });
    onReload();
  }
  return <><section className="metrics"><Metric label="API" value={health?.status || "..."} note="127.0.0.1" /><Metric label="Webull" value={health?.webull_configured ? "Ready" : "Missing"} note="Production SDK" /><Metric label="Execution" value={health?.execution_enabled ? "On" : "Paused"} note="Global switch" /><Metric label="Routes" value={String(health?.routes.length || 0)} note="Webhook configs" /></section>
  <section className="panel system-grid"><div><h2>Runtime</h2><Status label="Database" value={health?.database || "..."} ok /><Status label="Token cache" value={health?.token_dir || "..."} ok /><Status label="HTTPS tunnel" value="User managed" ok /></div><div><h2>Emergency</h2><p className="muted">Cancel known open orders uses locally recorded client order ids. Review Webull directly if network or token errors occur.</p><button className="danger" onClick={cancelKnown}>Cancel known open orders</button></div></section></>;
}

function Metric({ label, value, note, positive }: { label: string; value: string; note: string; positive?: boolean }) {
  return <article className="metric"><span>{label}</span><strong className={positive ? "gain" : ""}>{value}</strong><small>{note}</small><div className="spark"><i /><i /><i /><i /><i /><i /></div></article>;
}

function Status({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return <div className="status-row"><span><i className={ok ? "ok" : "idle"} />{label}</span><strong>{value}</strong></div>;
}

export default App;
