import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { translator } from "./i18n";
import { Health, Language, Page, Plugin, Position, SimAccount, StrategyDetail, StrategyInstance, Theme } from "./types";

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

function App() {
  const [language, setLanguage] = useState<Language>(() => (localStorage.getItem("language") as Language) || "en");
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("theme") as Theme) || "light");
  const [page, setPage] = useState<Page>("dashboard");
  const [instances, setInstances] = useState<StrategyInstance[]>([]);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [accounts, setAccounts] = useState<SimAccount[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const t = useMemo(() => translator(language), [language]);

  const reload = useCallback(async () => {
    try {
      const [nextInstances, nextPlugins, nextAccounts, nextPositions, nextHealth] = await Promise.all([
        request<StrategyInstance[]>("/instances"), request<Plugin[]>("/plugins"),
        request<SimAccount[]>("/simulator/accounts"), request<Position[]>("/positions"), request<Health>("/health")
      ]);
      setInstances(nextInstances); setPlugins(nextPlugins); setAccounts(nextAccounts);
      setPositions(nextPositions); setHealth(nextHealth); setError("");
    } catch (reason) { setError(String(reason)); }
  }, []);

  useEffect(() => { reload(); const id = window.setInterval(reload, 3000); return () => clearInterval(id); }, [reload]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("theme", theme); }, [theme]);
  useEffect(() => { localStorage.setItem("language", language); document.documentElement.lang = language === "zh" ? "zh-CN" : "en"; }, [language]);

  const running = instances.filter(item => item.worker?.alive).length;
  const pnl = positions.reduce((sum, item) => sum + Number(item.realized_pnl), 0);
  const activeModes = [...new Set(instances.filter(item => item.worker?.alive).map(item => item.mode))];

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">SD</div><div><strong>Strategy Desk</strong><span>{t("automation")}</span></div></div>
      <nav>{(["dashboard", "strategies", "accounts", "system"] as Page[]).map(item =>
        <button className={page === item ? "active" : ""} onClick={() => setPage(item)} key={item}>
          <span className="nav-dot" />{t(item)}
        </button>)}</nav>
      <div className="sidebar-foot"><span className="status-dot" /> API {health?.status || "—"}</div>
    </aside>
    <main>
      <header>
        <div><h1>{t(page)}</h1><p>{t("subtitle")}</p></div>
        <div className="header-actions">
          <span className="mode-badge">{activeModes.length ? activeModes.join(" + ") : t("idle")}</span>
          <div className="segmented"><button className={language === "en" ? "selected" : ""} onClick={() => setLanguage("en")}>EN</button><button className={language === "zh" ? "selected" : ""} onClick={() => setLanguage("zh")}>简中</button></div>
          <button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label="theme">{theme === "light" ? "◐" : "○"}</button>
        </div>
      </header>
      {error && <div className="error-banner">{error}</div>}
      {page === "dashboard" && <Dashboard t={t} running={running} pnl={pnl} positions={positions} instances={instances} health={health} onAction={reload} />}
      {page === "strategies" && <Strategies t={t} plugins={plugins} accounts={accounts} positions={positions} instances={instances} reload={reload} />}
      {page === "accounts" && <Accounts t={t} accounts={accounts} reload={reload} />}
      {page === "system" && <SystemPage t={t} health={health} plugins={plugins} />}
    </main>
  </div>;
}

type T = ReturnType<typeof translator>;

function Dashboard({ t, running, pnl, positions, instances, health, onAction }: { t: T; running: number; pnl: number; positions: Position[]; instances: StrategyInstance[]; health: Health | null; onAction: () => void }) {
  async function emergency() { if (window.prompt("Type EMERGENCY STOP") !== "EMERGENCY STOP") return; await request("/emergency-stop?confirmation=EMERGENCY%20STOP", { method: "POST" }); onAction(); }
  return <>
    <section className="metrics">
      <Metric label={t("running")} value={String(running)} note={`${instances.length} ${t("total")}`} />
      <Metric label={t("positions")} value={String(positions.length)} note={t("virtualLedgers")} />
      <Metric label={t("dayPnl")} value={`${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`} positive={pnl >= 0} note={t("allStrategies")} />
      <Metric label={t("feed")} value={health?.feed_age_ms == null ? "— ms" : `${health.feed_age_ms} ms`} note={health?.market_connections.length ? `${health.market_connections.length} ${t("connections").toLowerCase()}` : t("awaitingMarket")} />
    </section>
    <section className="panel"><div className="panel-title"><div><h2>{t("strategies")}</h2><p>{t("overview")}</p></div><div className="button-row"><button className="danger" onClick={emergency}>{t("emergency")}</button><button className="secondary" onClick={onAction}>{t("refresh")}</button></div></div>
      <StrategyTable t={t} instances={instances} positions={positions} reload={onAction} />
    </section>
  </>;
}

function Metric({ label, value, note, positive }: { label: string; value: string; note: string; positive?: boolean }) {
  return <article className="metric"><span>{label}</span><strong className={positive ? "gain" : ""}>{value}</strong><small>{note}</small><div className="spark"><i /><i /><i /><i /><i /><i /></div></article>;
}

function StrategyTable({ t, instances, positions, reload, onInspect }: { t: T; instances: StrategyInstance[]; positions: Position[]; reload: () => void; onInspect?: (item: StrategyInstance) => void }) {
  async function toggle(item: StrategyInstance) {
    const action = item.worker?.alive ? "stop" : "start";
    let live_confirmation: string | undefined;
    if (action === "start" && item.mode === "WEBULL_LIVE") live_confirmation = window.prompt("Type ENABLE LIVE TRADING") || undefined;
    await request(`/instances/${item.id}/${action}`, { method: "POST", body: JSON.stringify({ live_confirmation }) });
    reload();
  }
  async function action(item: StrategyInstance, name: "pause" | "resume" | "flatten") { if (name === "flatten" && !window.confirm(`Flatten ${item.config.name}?`)) return; await request(`/instances/${item.id}/${name}`, { method: "POST" }); reload(); }
  if (!instances.length) return <div className="empty">{t("noStrategies")}</div>;
  return <div className="table-wrap"><table><thead><tr><th>{t("strategy")}</th><th>{t("mode")}</th><th>{t("symbols")}</th><th>{t("position")}</th><th>{t("pnl")}</th><th>{t("health")}</th><th>{t("actions")}</th></tr></thead><tbody>
    {instances.map(item => { const own = positions.filter(position => position.strategy_instance_id === item.id); const qty = own.map(p => `${p.symbol} ${p.quantity}`).join(", ") || "Flat"; const ownPnl = own.reduce((sum, p) => sum + Number(p.realized_pnl), 0); return <tr key={item.id}>
      <td><strong>{item.config.name}</strong><small>{item.plugin_id} · {item.plugin_version}</small></td><td><span className={`tag ${item.mode.toLowerCase()}`}>{item.mode}</span></td><td>{item.config.symbols.join(", ")}</td><td>{qty}</td><td className={ownPnl >= 0 ? "gain" : "loss"}>{ownPnl >= 0 ? "+" : ""}${ownPnl.toFixed(2)}</td><td><span className={item.worker?.alive ? "health-ok" : "health-idle"}><i />{item.worker?.alive ? item.state : "STOPPED"}</span></td><td><div className="button-row">{onInspect && <button className="secondary" onClick={() => onInspect(item)}>{t("details")}</button>}<button className={item.worker?.alive ? "secondary" : "primary"} onClick={() => toggle(item)}>{item.worker?.alive ? t("stop") : t("start")}</button>{item.worker?.alive && <button className="secondary" onClick={() => action(item, item.state === "PAUSED" ? "resume" : "pause")}>{item.state === "PAUSED" ? t("resume") : t("pause")}</button>}{own.length > 0 && <button className="danger" onClick={() => action(item, "flatten")}>{t("flatten")}</button>}</div></td>
    </tr>; })}
  </tbody></table></div>;
}

function Strategies({ t, plugins, accounts, positions, instances, reload }: { t: T; plugins: Plugin[]; accounts: SimAccount[]; positions: Position[]; instances: StrategyInstance[]; reload: () => void }) {
  const [plugin, setPlugin] = useState(plugins[0]?.id || "");
  const [mode, setMode] = useState("LOCAL_SIM");
  const [feed, setFeed] = useState("REPLAY");
  const [productCode, setProductCode] = useState("");
  const [contracts, setContracts] = useState<Array<{ symbol: string; name?: string; contract_month: string }>>([]);
  const [contractError, setContractError] = useState("");
  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  useEffect(() => { if (!plugin && plugins[0]) setPlugin(plugins[0].id); }, [plugins, plugin]);
  async function loadContracts() { try { const environment = mode === "WEBULL_UAT" || feed === "UAT" ? "WEBULL_UAT" : "WEBULL_LIVE"; const values = await request<Array<{ symbol: string; name?: string; contract_month: string }>>(`/futures/contracts/${environment}?code=${encodeURIComponent(productCode)}`); setContracts(values.filter(item => item.symbol && item.contract_month)); setContractError(""); } catch (reason) { setContractError(String(reason)); } }
  async function inspect(item: StrategyInstance) { setDetail(await request<StrategyDetail>(`/instances/${item.id}/detail`)); }
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const selected = plugins.find(item => item.id === data.get("plugin")); const parameters = JSON.parse(String(data.get("parameters") || "{}")); const contractMode = String(data.get("contract_mode") || ""); await request("/instances", { method: "POST", body: JSON.stringify({ name: data.get("name"), plugin_id: selected?.id, plugin_version: selected?.version, mode: data.get("mode"), feed_source: data.get("feed"), account_id: data.get("account"), symbols: String(data.get("symbols")).split(",").map(s => s.trim().toUpperCase()), parameters, replay_path: data.get("replay_path") || null, replay_speed: Number(data.get("replay_speed") || 1), risk: { max_position: String(data.get("max_position")), max_notional: String(data.get("max_notional")), max_daily_realized_loss: String(data.get("max_loss")), max_open_orders: 50, max_orders_per_minute: 60, allowed_symbols: [] }, contract: contractMode ? { mode: contractMode, product_code: data.get("product_code"), current_symbol: String(data.get("symbols")).split(",")[0].trim().toUpperCase(), contracts } : null }) }); form.reset(); reload(); }
  return <><div className="two-column"><section className="panel"><div className="panel-title"><div><h2>{t("strategies")}</h2><p>{instances.length} configured</p></div></div><StrategyTable t={t} instances={instances} positions={positions} reload={reload} onInspect={inspect} /></section>
    <section className="panel form-panel"><h2>{t("createStrategy")}</h2><form onSubmit={submit}><label>{t("name")}<input name="name" required placeholder="Morning MGC" /></label><label>{t("plugin")}<select name="plugin" required value={plugin} onChange={e => setPlugin(e.target.value)}>{plugins.map(item => <option value={item.id} key={item.id}>{item.name.en} · {item.version}</option>)}</select></label><div className="form-row"><label>{t("mode")}<select name="mode" value={mode} onChange={e => setMode(e.target.value)}><option value="LOCAL_SIM">LOCAL_SIM</option><option value="WEBULL_UAT">WEBULL_UAT</option><option value="WEBULL_LIVE">WEBULL_LIVE</option></select></label><label>{t("dataSource")}<select name="feed" value={feed} onChange={e => setFeed(e.target.value)}><option value="REPLAY">Replay</option><option value="PRODUCTION">Webull production</option><option value="UAT">Webull UAT</option></select></label></div>{feed === "REPLAY" && <div className="form-row"><label>{t("replayPath")}<input name="replay_path" placeholder="recordings/session.jsonl" /></label><label>{t("replaySpeed")}<input name="replay_speed" type="number" min="0.01" max="1000" step="0.01" defaultValue="1" /></label></div>}<label>{t("account")}<input name="account" list="sim-account-options" required placeholder={mode === "LOCAL_SIM" ? "SIM account" : "Webull account ID"} /><datalist id="sim-account-options">{accounts.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</datalist></label><label>{t("symbols")}<input name="symbols" list="futures-contract-options" required placeholder="MGCQ6" /><datalist id="futures-contract-options">{contracts.map(item => <option value={item.symbol} key={item.symbol}>{item.name || item.contract_month}</option>)}</datalist></label><div className="form-row"><label>{t("futuresBinding")}<select name="contract_mode"><option value="">{t("notFutures")}</option><option value="FIXED">{t("fixedContract")}</option><option value="AUTO_ROLL">{t("autoRoll")}</option></select></label><label>{t("productCode")}<input name="product_code" placeholder="MGC" value={productCode} onChange={event => setProductCode(event.target.value.toUpperCase())} /></label></div><button type="button" className="secondary" disabled={!productCode} onClick={loadContracts}>{t("loadContracts")}</button>{contractError && <p className="field-error">{contractError}</p>}<label>{t("parametersJson")}<textarea name="parameters" defaultValue="{}" rows={3} /></label><div className="form-row"><label>{t("maxPosition")}<input name="max_position" defaultValue="100" /></label><label>{t("maxNotional")}<input name="max_notional" defaultValue="1000000" /></label></div><label>{t("maxDailyLoss")}<input name="max_loss" defaultValue="5000" /></label><button className="primary" disabled={!plugins.length || (mode === "LOCAL_SIM" && !accounts.length)}>{t("create")}</button></form></section>
  </div>{detail && <StrategyDetailPanel t={t} detail={detail} close={() => setDetail(null)} />}</>;
}

function StrategyDetailPanel({ t, detail, close }: { t: T; detail: StrategyDetail; close: () => void }) {
  return <section className="panel detail-panel"><div className="panel-title"><div><h2>{detail.instance.config.name}</h2><p>{detail.instance.id} · {detail.instance.mode}{detail.expiry_state ? ` · ${detail.expiry_state}` : ""}</p></div><button className="secondary" onClick={close}>{t("close")}</button></div><div className="detail-grid"><div><h3>{t("orders")}</h3>{detail.orders.length ? detail.orders.slice(-20).reverse().map(order => <div className="detail-row" key={order.id}><span>{order.command.symbol} · {order.command.side} {order.command.quantity}</span><strong>{order.status}</strong><small>{order.command.origin} · {order.command.order_type}</small></div>) : <p className="muted">{t("none")}</p>}</div><div><h3>{t("fills")}</h3>{detail.fills.length ? detail.fills.slice(-20).reverse().map(fill => <div className="detail-row" key={fill.id}><span>{fill.symbol} · {fill.side} {fill.quantity}</span><strong>@ {fill.price}</strong><small>{new Date(fill.filled_at).toLocaleString()}</small></div>) : <p className="muted">{t("none")}</p>}</div><div><h3>{t("runHistory")}</h3>{detail.runs.length ? detail.runs.map(run => <div className="detail-row" key={run.id}><span>{run.status}</span><small>{new Date(run.started_at).toLocaleString()}</small></div>) : <p className="muted">{t("none")}</p>}</div><div><h3>{t("logs")}</h3>{detail.events.length ? detail.events.slice(0, 20).map(event => <div className="detail-row" key={event.id}><span>{event.kind}</span><small>{new Date(event.created_at).toLocaleString()}</small></div>) : <p className="muted">{t("none")}</p>}</div></div></section>;
}

function Accounts({ t, accounts, reload }: { t: T; accounts: SimAccount[]; reload: () => void }) {
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form)); await request("/simulator/accounts", { method: "POST", body: JSON.stringify({ name: data.name, initial_cash: data.initial_cash, commission_per_unit: data.commission, slippage_bps: data.slippage, latency_ms: Number(data.latency), partial_fills: data.partial === "on", leverage: data.leverage, futures_margin_per_contract: data.futures_margin }) }); form.reset(); reload(); }
  async function reset(item: SimAccount) { if (!window.confirm(`${t("resetSimulator")} ${item.name}?`)) return; await request(`/simulator/accounts/${item.id}/reset`, { method: "POST", body: "{}" }); reload(); }
  return <div className="two-column"><section className="panel"><div className="panel-title"><div><h2>{t("simulatorAccounts")}</h2><p>{t("simulatorDesc")}</p></div></div><div className="account-list">{accounts.map(item => <article key={item.id}><div className="account-icon">SIM</div><div><strong>{item.name}</strong><span>{item.id}</span></div><div className="account-balance"><strong>${Number(item.cash).toLocaleString()}</strong><span>{item.slippage_bps} bps · {item.latency_ms} ms</span></div><button className="secondary" onClick={() => reset(item)}>{t("reset")}</button></article>)}</div></section>
    <section className="panel form-panel"><h2>{t("newSimulator")}</h2><form onSubmit={submit}><label>{t("name")}<input name="name" required placeholder="Primary simulator" /></label><div className="form-row"><label>{t("initialFunds")}<input name="initial_cash" type="number" defaultValue="100000" min="1" /></label><label>{t("commission")}<input name="commission" type="number" defaultValue="0" min="0" step="0.01" /></label></div><div className="form-row"><label>{t("slippage")}<input name="slippage" type="number" defaultValue="0" min="0" step="0.1" /></label><label>{t("latency")}<input name="latency" type="number" defaultValue="0" min="0" /></label></div><div className="form-row"><label>{t("leverage")}<input name="leverage" type="number" defaultValue="1" min="1" step="0.1" /></label><label>{t("futuresMargin")}<input name="futures_margin" type="number" defaultValue="0" min="0" step="1" /></label></div><label className="check"><input name="partial" type="checkbox" defaultChecked />{t("partialFills")}</label><button className="primary">{t("create")}</button></form></section>
  </div>;
}

function SystemPage({ t, health, plugins }: { t: T; health: Health | null; plugins: Plugin[] }) {
  const [result, setResult] = useState("");
  async function validate() { const rows = await request<Array<{ status: string }>>("/plugins/validate", { method: "POST" }); setResult(rows.every(item => item.status === "ok") ? t("allGood") : JSON.stringify(rows)); }
  return <><section className="metrics"><Metric label="API" value={health?.status || "—"} note="127.0.0.1" /><Metric label="UAT" value={health?.uat_configured ? t("configured") : t("missing")} note="Webull SDK" /><Metric label={t("production")} value={health?.production_configured ? t("configured") : t("missing")} note={`${t("liveGate")}: ${health?.live_enabled ? t("enabled") : t("disabled")}`} /><Metric label={t("plugins")} value={String(plugins.length)} note={t("immutableVersions")} /></section><section className="panel system-grid"><div><h2>{t("connections")}</h2><Status label="SQLite" value={health?.database || "—"} ok /><Status label="Webull UAT" value={health?.uat_configured ? t("configured") : t("missing")} ok={!!health?.uat_configured} /><Status label="Webull production" value={health?.production_configured ? t("configured") : t("missing")} ok={!!health?.production_configured} /></div><div><h2>{t("validation")}</h2><p className="muted">{t("validation")}</p><button className="secondary" onClick={validate}>{t("validate")}</button>{result && <p className="validation-result">{result}</p>}</div></section></>;
}

function Status({ label, value, ok }: { label: string; value: string; ok: boolean }) { return <div className="status-row"><span><i className={ok ? "ok" : "idle"} />{label}</span><strong>{value}</strong></div>; }

export default App;
