import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { isLanguage, Language, Translation, translations } from "./i18n";
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

function readTheme(): Theme {
  return localStorage.getItem("theme") === "dark" ? "dark" : "light";
}

function readLanguage(): Language {
  const stored = localStorage.getItem("language");
  return isLanguage(stored) ? stored : "en";
}

function App() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [language, setLanguage] = useState<Language>(readLanguage);
  const [page, setPage] = useState<Page>("dashboard");
  const [health, setHealth] = useState<Health | null>(null);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [positions, setPositions] = useState<PositionsResponse>({ account_id: "", positions: [] });
  const [error, setError] = useState("");
  const t = translations[language];
  const pageLabels = t.pages;

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

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    localStorage.setItem("language", language);
  }, [language]);

  const activeRoute = routes[0];
  const failures = events.filter(item => ["failed", "rejected", "validation_failed", "unknown"].includes(item.status));
  const queued = events.filter(item => item.status === "queued").length;
  const submitted = orders.filter(item => item.status === "submitted").length;

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">WB</div><div><strong>Webull Bridge</strong><span>{t.app.brandSubtitle}</span></div></div>
      <nav>{(Object.keys(pageLabels) as Page[]).map(item =>
        <button className={page === item ? "active" : ""} onClick={() => setPage(item)} key={item}>
          <span className="nav-dot" />{pageLabels[item]}
        </button>)}</nav>
      <div className="sidebar-foot"><span className="status-dot" /> {t.app.apiStatus(health?.status || t.app.loading)}</div>
    </aside>
    <main>
      <header>
        <div><h1>{pageLabels[page]}</h1><p>{t.app.subtitle}</p></div>
        <div className="header-actions">
          <span className={`mode-badge ${health?.execution_enabled ? "live" : ""}`}>{health?.execution_enabled ? t.app.executionOn : t.app.paused}</span>
          <LanguageSwitch language={language} setLanguage={setLanguage} t={t} />
          <button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={t.app.themeLabel}>{theme === "light" ? "◐" : "○"}</button>
        </div>
      </header>
      {error && <div className="error-banner">{error}</div>}
      {page === "dashboard" && <Dashboard health={health} routes={routes} queued={queued} submitted={submitted} failures={failures.length} onReload={reload} t={t} />}
      {page === "route" && <RoutePage route={activeRoute} onReload={reload} t={t} />}
      {page === "orders" && <OrdersPage events={events} orders={orders} onReload={reload} t={t} />}
      {page === "positions" && <PositionsPage routes={routes} positions={positions} refresh={refreshPositions} t={t} />}
      {page === "activity" && <ActivityPage rows={activity} t={t} />}
      {page === "settings" && <SettingsPage health={health} onReload={reload} t={t} />}
    </main>
  </div>;
}

function LanguageSwitch({ language, setLanguage, t }: { language: Language; setLanguage: (language: Language) => void; t: Translation }) {
  return <div className="segmented" role="group" aria-label={t.language.ariaLabel}>
    <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} type="button">{t.language.english}</button>
    <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")} type="button">{t.language.chinese}</button>
  </div>;
}

function Dashboard({ health, routes, queued, submitted, failures, onReload, t }: { health: Health | null; routes: Route[]; queued: number; submitted: number; failures: number; onReload: () => void; t: Translation }) {
  async function toggleExecution() {
    await request("/settings/execution", { method: "PUT", body: JSON.stringify({ enabled: !health?.execution_enabled }) });
    onReload();
  }
  async function emergencyPause() {
    if (window.prompt(t.dashboard.promptPauseAll) !== "PAUSE ALL") return;
    await request("/emergency/pause", { method: "POST", body: JSON.stringify({ confirmation: "PAUSE ALL" }) });
    onReload();
  }
  return <>
    <section className="metrics">
      <Metric label={t.dashboard.metrics.execution} value={health?.execution_enabled ? t.dashboard.metrics.executionOn : t.dashboard.metrics.executionPaused} note={t.dashboard.metrics.globalSafetySwitch} positive={health?.execution_enabled} />
      <Metric label={t.dashboard.metrics.routes} value={String(routes.length)} note={t.dashboard.metrics.routesEnabled(routes.filter(item => item.enabled).length)} />
      <Metric label={t.dashboard.metrics.queued} value={String(queued)} note={t.dashboard.metrics.waitingForExecution} />
      <Metric label={t.dashboard.metrics.failures} value={String(failures)} note={t.dashboard.metrics.needsReview} positive={failures === 0} />
    </section>
    <section className="panel"><div className="panel-title"><div><h2>{t.dashboard.bridgeStatus}</h2><p>{t.dashboard.bridgeStatusSubtitle}</p></div><div className="button-row"><button className={health?.execution_enabled ? "secondary" : "primary"} onClick={toggleExecution}>{health?.execution_enabled ? t.dashboard.pauseExecution : t.dashboard.enableExecution}</button><button className="danger" onClick={emergencyPause}>{t.dashboard.emergencyPause}</button></div></div>
      <div className="status-grid">
        <Status label={t.dashboard.status.webullCredentials} value={health?.webull_configured ? t.dashboard.status.configured : t.dashboard.status.missing} ok={!!health?.webull_configured} />
        <Status label={t.dashboard.status.database} value={health?.database || t.app.loading} ok />
        <Status label={t.dashboard.status.latestSubmittedOrders} value={String(submitted)} ok />
        <Status label={t.dashboard.status.tokenCache} value={health?.token_dir || t.app.loading} ok />
      </div>
    </section>
  </>;
}

function RoutePage({ route, onReload, t }: { route?: Route; onReload: () => void; t: Translation }) {
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
  if (!route) return <section className="panel"><div className="empty">{t.route.empty}</div></section>;
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
  return <div className="two-column"><section className="panel form-panel"><h2>{t.route.settingsTitle}</h2><form onSubmit={submit}>
    <label>{t.route.routeId}<input value={route.route_id} disabled /></label>
    <label>{t.route.name}<input name="name" defaultValue={route.name} required /></label>
    <label>{t.route.accountId}<input name="account_id" defaultValue={route.account_id} required /></label>
    <label>{t.route.rotateSecret}<input name="secret" placeholder={route.secret_configured ? t.route.secretKeep : t.route.secretRequired} /></label>
    <label>{t.route.allowedSymbols}<input name="allowed_symbols" defaultValue={route.allowed_symbols.join(", ")} placeholder={t.route.allowedSymbolsPlaceholder} /></label>
    <div className="form-row"><label>{t.route.maxQuantity}<input name="max_quantity" defaultValue={route.max_quantity} /></label><label>{t.route.maxNotional}<input name="max_notional" defaultValue={route.max_notional} /></label></div>
    <label>{t.route.acceptedOrderTypes}<input name="accepted_order_types" defaultValue={route.accepted_order_types.join(", ")} /></label>
    <label className="check"><input name="enabled" type="checkbox" defaultChecked={route.enabled} />{t.route.enabled}</label>
    <button className="primary">{t.common.save}</button>
  </form></section>
  <section className="panel"><div className="panel-title"><div><h2>{t.route.webhook}</h2><p>POST /webhook/tradingview/{route.route_id}</p></div></div><pre>{sample}</pre></section></div>;
}

function OrdersPage({ events, orders, onReload, t }: { events: EventRow[]; orders: OrderRow[]; onReload: () => void; t: Translation }) {
  const [selected, setSelected] = useState<EventRow | null>(null);
  async function retry(item: EventRow) {
    await request(`/events/${item.id}/retry`, { method: "POST" });
    onReload();
  }
  return <><section className="panel"><div className="panel-title"><div><h2>{t.orders.events}</h2><p>{t.orders.recentEvents(events.length)}</p></div><button className="secondary" onClick={onReload}>{t.common.refresh}</button></div>
    <div className="table-wrap"><table><thead><tr><th>{t.common.event}</th><th>{t.common.action}</th><th>{t.common.symbol}</th><th>{t.common.status}</th><th>{t.common.updated}</th><th>{t.common.actions}</th></tr></thead><tbody>
      {events.map(item => <tr key={item.id}><td><strong>{item.event_id}</strong><small>{item.route_id}</small></td><td>{item.action}</td><td>{item.symbol || "-"}</td><td><span className={`tag ${item.status}`}>{item.status}</span></td><td>{new Date(item.updated_at).toLocaleString()}</td><td><div className="button-row"><button className="secondary" onClick={() => setSelected(item)}>{t.common.details}</button>{["failed", "unknown"].includes(item.status) && <button className="secondary" onClick={() => retry(item)}>{t.common.retry}</button>}</div></td></tr>)}
    </tbody></table></div></section>
    <section className="panel"><div className="panel-title"><div><h2>{t.orders.orders}</h2><p>{t.orders.recentOrders(orders.length)}</p></div></div><div className="table-wrap"><table><thead><tr><th>{t.orders.clientId}</th><th>{t.common.side}</th><th>{t.common.symbol}</th><th>{t.common.status}</th><th>{t.orders.webullId}</th></tr></thead><tbody>{orders.map(item => <tr key={item.id}><td><strong>{item.client_order_id || item.target_client_order_id || "-"}</strong><small>{item.account_id}</small></td><td>{item.side || item.action}</td><td>{item.symbol || "-"}</td><td><span className={`tag ${item.status}`}>{item.status}</span></td><td>{item.webull_order_id || "-"}</td></tr>)}</tbody></table></div></section>
    {selected && <section className="panel detail-panel"><div className="panel-title"><div><h2>{selected.event_id}</h2><p>{selected.status}</p></div><button className="secondary" onClick={() => setSelected(null)}>{t.common.close}</button></div><pre>{JSON.stringify(selected, null, 2)}</pre></section>}</>;
}

function PositionsPage({ routes, positions, refresh, t }: { routes: Route[]; positions: PositionsResponse; refresh: () => void; t: Translation }) {
  const route = routes[0];
  async function flatten(symbol: string) {
    const confirmation = `FLATTEN ${symbol}`;
    if (window.prompt(t.positions.promptFlatten(confirmation)) !== confirmation) return;
    const quantity = window.prompt(t.positions.promptQuantity, "1") || "1";
    await request(`/routes/${route.route_id}/flatten/${symbol}?quantity=${encodeURIComponent(quantity)}`, { method: "POST", body: JSON.stringify({ confirmation }) });
    refresh();
  }
  return <section className="panel"><div className="panel-title"><div><h2>{t.positions.title}</h2><p>{positions.account_id || t.positions.noAccount}{positions.stale ? ` - ${t.positions.staleSnapshot}` : ""}</p></div><button className="secondary" onClick={refresh}>{t.common.refresh}</button></div>
    {positions.error && <div className="error-banner">{positions.error}</div>}
    <div className="table-wrap"><table><thead><tr><th>{t.common.symbol}</th><th>{t.common.quantity}</th><th>{t.common.cost}</th><th>{t.common.last}</th><th>{t.common.profitLoss}</th><th>{t.common.actions}</th></tr></thead><tbody>{positions.positions.map((item, index) => { const symbol = String(item.symbol || ""); return <tr key={index}><td><strong>{symbol || "-"}</strong><small>{String(item.instrument_type || "")}</small></td><td>{String(item.quantity || "-")}</td><td>{String(item.cost_price || "-")}</td><td>{String(item.last_price || "-")}</td><td>{String(item.unrealized_profit_loss || "-")}</td><td>{route && symbol && <button className="danger" onClick={() => flatten(symbol)}>{t.positions.flatten}</button>}</td></tr>; })}</tbody></table></div>
  </section>;
}

function ActivityPage({ rows, t }: { rows: ActivityRow[]; t: Translation }) {
  return <section className="panel"><div className="panel-title"><div><h2>{t.activity.title}</h2><p>{t.activity.subtitle}</p></div></div><div className="activity-list">{rows.map(item => <article key={item.id}><span className={`tag ${item.level}`}>{item.kind}</span><div><strong>{item.message}</strong><small>{new Date(item.created_at).toLocaleString()}</small></div></article>)}</div></section>;
}

function SettingsPage({ health, onReload, t }: { health: Health | null; onReload: () => void; t: Translation }) {
  async function cancelKnown() {
    const confirmation = "CANCEL KNOWN OPEN ORDERS";
    if (window.prompt(t.settings.promptCancelKnown(confirmation)) !== confirmation) return;
    await request("/emergency/cancel-known", { method: "POST", body: JSON.stringify({ confirmation }) });
    onReload();
  }
  return <><section className="metrics"><Metric label="API" value={health?.status || t.app.loading} note="127.0.0.1" /><Metric label={t.settings.webull} value={health?.webull_configured ? t.settings.ready : t.dashboard.status.missing} note={t.settings.productionSdk} /><Metric label={t.dashboard.metrics.execution} value={health?.execution_enabled ? t.dashboard.metrics.executionOn : t.dashboard.metrics.executionPaused} note={t.settings.globalSwitch} /><Metric label={t.dashboard.metrics.routes} value={String(health?.routes.length || 0)} note={t.settings.webhookConfigs} /></section>
  <section className="panel system-grid"><div><h2>{t.settings.runtime}</h2><Status label={t.settings.database} value={health?.database || t.app.loading} ok /><Status label={t.settings.tokenCache} value={health?.token_dir || t.app.loading} ok /><Status label={t.settings.httpsTunnel} value={t.settings.userManaged} ok /></div><div><h2>{t.settings.emergency}</h2><p className="muted">{t.settings.cancelKnownHelp}</p><button className="danger" onClick={cancelKnown}>{t.settings.cancelKnown}</button></div></section></>;
}

function Metric({ label, value, note, positive }: { label: string; value: string; note: string; positive?: boolean }) {
  return <article className="metric"><span>{label}</span><strong className={positive ? "gain" : ""}>{value}</strong><small>{note}</small><div className="spark"><i /><i /><i /><i /><i /><i /></div></article>;
}

function Status({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return <div className="status-row"><span><i className={ok ? "ok" : "idle"} />{label}</span><strong>{value}</strong></div>;
}

export default App;
