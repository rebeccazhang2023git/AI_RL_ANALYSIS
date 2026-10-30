/* Debt Restructuring & Liquidity Analysis: front end */
"use strict";

const state = { news: null, prices: null, symbol: "", overlay: false, q: null, overview: null, snapshot: null, period: null, start: null, eventFilter: "all" };
const $ = (sel) => document.querySelector(sel);

// ---------------------------------------------------------------- formatting
const M = 1e6;
const fmt = {
  usd: (v) => v == null ? "–" : `${v < 0 ? "−" : ""}$${Math.abs(v / M).toLocaleString(undefined, { maximumFractionDigits: 0 })}M`,
  usdM: (v) => `${v < 0 ? "−" : ""}$${Math.abs(Math.round(v)).toLocaleString()}M`, // v already in $M
  x: (v) => v == null ? "–" : `${v.toFixed(2)}×`,
  pct: (v) => v == null ? "–" : `${(v * 100).toFixed(1)}%`,
  pct100: (v) => v == null ? "–" : `${v.toFixed(1)}%`,
  num: (v, d = 2) => v == null ? "–" : v.toFixed(d),
  months: (v) => v == null ? "n/a" : `${v.toFixed(1)} mo`,
  px: (v) => v == null ? "–" : `$${v.toFixed(2)}`,
  ret: (v) => v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`,
  cents: (v) => v == null ? "–" : `${v.toFixed(0)}¢`,
};

const METRICS = {
  liquid_assets: ["Liquid assets (cash + ST inv.)", "usd"], working_capital: ["Working capital", "usd"],
  current_ratio: ["Current ratio", "x"], quick_ratio: ["Quick ratio", "x"], cash_ratio: ["Cash ratio", "x"],
  fcf: ["Free cash flow (qtr)", "usd"], fcf_ttm: ["Free cash flow (TTM)", "usd"], runway_months: ["Runway (months)", "num"],
  funded_debt: ["Funded debt", "usd"], finance_leases: ["Finance leases", "usd"], total_debt: ["Total debt", "usd"],
  operating_leases: ["Operating lease liabilities", "usd"], net_debt: ["Net debt", "usd"],
  debt_price_pct: ["Notes price (¢ per $)", "num"], revenue_ttm: ["Revenue (TTM)", "usd"],
  ebitda_ttm: ["EBITDA (TTM)", "usd"], adj_ebitda_ttm: ["Adj. EBITDA ex-impairments (TTM)", "usd"],
  interest_expense_ttm: ["Interest expense (TTM)", "usd"], ebitda_margin_ttm: ["EBITDA margin (TTM)", "pct"],
  debt_to_ebitda: ["Debt / EBITDA", "x"], net_debt_to_ebitda: ["Net debt / EBITDA", "x"],
  lease_adj_leverage: ["Lease-adj. debt / EBITDAR", "x"], ebitda_interest_coverage: ["EBITDA / interest", "x"],
  price_close: ["Stock price (quarter-end)", "px"], price_qtr_return: ["Stock return (quarter)", "ret"],
  price_drawdown: ["Drawdown from peak", "ret"], distress_flag_count: ["Distress flags (count)", "num"],
  z_double_prime: ["Altman Z''", "num"], shareholder_returns_ttm: ["Buybacks + dividends (TTM)", "usd"],
};

const TABLE_SECTIONS = [
  ["Liquidity", ["liquid_assets", "working_capital", "current_ratio", "quick_ratio", "cash_ratio", "fcf", "fcf_ttm", "runway_months"]],
  ["Capital structure", ["debt_current", "debt_noncurrent", "finance_leases", "total_debt", "operating_leases", "net_debt", "notes_principal", "debt_fair_value", "debt_price_pct"]],
  ["Earnings (TTM)", ["revenue_ttm", "ebitda_ttm", "adj_ebitda_ttm", "interest_expense_ttm", "ebitda_margin_ttm"]],
  ["Leverage & solvency", ["debt_to_ebitda", "net_debt_to_ebitda", "lease_adj_leverage", "ebitda_interest_coverage", "z_double_prime"]],
  ["Balance sheet", ["cash", "receivables", "inventory", "current_assets", "total_assets", "accounts_payable", "current_liabilities", "total_liabilities", "retained_earnings", "equity"]],
  ["Stock price (Yahoo Finance)", ["price_close", "price_qtr_return", "price_drawdown"]],
  ["Cash flow (quarter)", ["ocf", "capex", "buybacks", "dividends", "debt_issued", "debt_repaid", "equity_issued"]],
];

function label(col) { return (METRICS[col] && METRICS[col][0]) || state.overview.labels[col] || col; }
function format(col, v) { const f = (METRICS[col] && METRICS[col][1]) || "usd"; return fmt[f](v); }

// ---------------------------------------------------------------- api
async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function setStatus(msg, isError = false) {
  const el = $("#status");
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

// ---------------------------------------------------------------- loading
async function loadCompany(q) {
  $("main").classList.add("loading");
  setStatus(`Fetching ${q} from SEC EDGAR…`);
  try {
    state.overview = await api(`/api/overview?q=${encodeURIComponent(q)}`);
    state.q = q;
    state.period = state.overview.latest;
    const years = [...new Set(state.overview.periods.map((p) => p.end.slice(0, 4)))];
    state.start = years.includes("2016") ? "2016" : years[0];
    renderHeader();
    renderFilters(years);
    await loadSnapshot();
    renderEvents();
    setStatus(`Loaded ${state.overview.periods.length} quarterly periods.`);
    loadPrices();
    loadNews();
  } catch (e) {
    setStatus(e.message, true);
  } finally {
    $("main").classList.remove("loading");
  }
}

// Prices load after the EDGAR data so a slow/failed Yahoo call never blocks the analysis.
async function loadPrices() {
  const q = state.q;
  state.prices = null;
  const ov = state.overview;
  ["price_close", "price_qtr_return", "price_drawdown"].forEach((c) => { ov.series[c] = ov.periods.map(() => null); });
  renderPrices();
  try {
    const sym = state.symbol ? `&symbol=${encodeURIComponent(state.symbol)}` : "";
    const res = await api(`/api/prices?q=${encodeURIComponent(q)}${sym}`);
    if (q !== state.q) return; // company changed while loading
    state.prices = res;
    if (res.available) Object.assign(ov.series, res.quarterly);
    $("#csv-link").href = `/api/export.csv?q=${encodeURIComponent(q)}${sym}`;
  } catch (e) {
    if (q !== state.q) return;
    state.prices = { available: false, reason: e.message };
  }
  renderPrices();
  renderTable();
  if (state.snapshot) renderCharts();
}

const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function loadNews() {
  const q = state.q;
  state.news = null;
  renderNews();
  try {
    const sym = state.symbol ? `&symbol=${encodeURIComponent(state.symbol)}` : "";
    const res = await api(`/api/news?q=${encodeURIComponent(q)}${sym}`);
    if (q !== state.q) return;
    state.news = res;
  } catch (e) {
    if (q !== state.q) return;
    state.news = { available: false, items: [], reason: e.message };
  }
  renderNews();
}

function renderNews() {
  const n = state.news;
  $("#news-meta").textContent = n && n.available ? `${n.symbol} · ${n.items.length} stories` : "";
  $("#news-msg").textContent = n == null ? "Loading news from Yahoo Finance…" : !n.available ? n.reason : n.items.length ? "" : "No recent stories found.";
  $("#news-msg").classList.toggle("error", n != null && !n.available);
  const fmtTime = (iso) => new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  $("#news-list").innerHTML = n && n.available ? n.items.map((i) => `
    <li><span class="date">${escapeHtml(fmtTime(i.published))}</span>
      <div><a href="${escapeHtml(i.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(i.title)}</a>
        <div class="muted">${escapeHtml(i.publisher || "")}${i.tickers.length > 1 ? " · " + escapeHtml(i.tickers.filter((t) => t !== n.symbol).slice(0, 4).join(", ")) : ""}</div></div></li>`).join("") : "";
}

async function loadSnapshot() {
  state.snapshot = await api(`/api/snapshot?q=${encodeURIComponent(state.q)}&period=${state.period}`);
  renderKpis();
  renderFlags();
  renderCharts();
  renderTable();
  buildLabForms();
  runLab();
}

function renderHeader() {
  const c = state.overview.company;
  const former = c.former_names.length ? ` (formerly <strong>${c.former_names.join(", ")}</strong>)` : "";
  $("#company-line").innerHTML =
    `${c.name}${former} · CIK <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${c.cik}" target="_blank" rel="noopener">${c.cik}</a>` +
    (c.sic ? ` · ${c.sic}` : "");
  $("#csv-link").href = `/api/export.csv?q=${encodeURIComponent(state.q)}`;
  state.symbol = ""; $("#symbol-q").value = "";
}

function renderFilters(years) {
  const ps = $("#period-select");
  ps.innerHTML = [...state.overview.periods].reverse()
    .map((p) => `<option value="${p.end}">${p.label === p.end ? p.end : `${p.label} · ${p.end}`}</option>`).join("");
  ps.value = state.period;
  const rs = $("#range-select");
  rs.innerHTML = years.map((y) => `<option value="${y}">${y}</option>`).join("");
  rs.value = state.start;
}

// ---------------------------------------------------------------- series helpers
function inRange() {
  return state.overview.periods.map((p, i) => [p, i]).filter(([p]) => p.end.slice(0, 4) >= state.start);
}
function xs() { return inRange().map(([p]) => p.end); }
function ys(col, scale = 1) {
  const s = state.overview.series[col] || [];
  return inRange().map(([, i]) => (s[i] == null ? null : s[i] / scale));
}
function seriesAt(col, end) {
  const i = state.overview.periods.findIndex((p) => p.end === end);
  return i < 0 ? null : state.overview.series[col][i];
}

// ---------------------------------------------------------------- KPIs & flags
function renderKpis() {
  const v = state.snapshot.values;
  const yearAgo = (() => {
    const i = state.overview.periods.findIndex((p) => p.end === state.period);
    return i >= 4 ? state.overview.periods[i - 4].end : null;
  })();
  const delta = (col) => {
    if (!yearAgo) return "";
    const prev = seriesAt(col, yearAgo), cur = v[col];
    if (prev == null || cur == null) return "";
    return `${format(col, prev)} a year earlier`;
  };
  const tiles = [
    ["Liquid assets", fmt.usd(v.liquid_assets), delta("liquid_assets")],
    ["Total debt", fmt.usd(v.total_debt), delta("total_debt")],
    ["EBITDA (TTM)", fmt.usd(v.ebitda_ttm), delta("ebitda_ttm")],
    ["Free cash flow (TTM)", fmt.usd(v.fcf_ttm), delta("fcf_ttm")],
    ["Cash runway", fmt.months(v.runway_months), v.runway_months == null ? "FCF is positive" : "at TTM burn, ex-revolver"],
    ["Notes price", fmt.cents(v.debt_price_pct), "fair value ÷ principal"],
    ["Current ratio", fmt.x(v.current_ratio), delta("current_ratio")],
    ["Altman Z''", fmt.num(v.z_double_prime), state.snapshot.z_zone ? `${state.snapshot.z_zone} zone` : ""],
  ];
  $("#kpis").innerHTML = tiles.map(([l, val, d]) =>
    `<div class="kpi"><div class="label">${l}</div><div class="value">${val}</div><div class="delta">${d}</div></div>`).join("");
}

const SEV_ICON = { critical: "!", serious: "!", warning: "?", good: "✓" };
function renderFlags() {
  const s = state.snapshot;
  $("#flags-period").textContent = `as of ${s.fiscal_label || s.period_end}`;
  const flags = s.flags.length ? s.flags : [{ severity: "good", title: "No distress signals triggered", detail: "All rule checks passed for this period." }];
  $("#flags").innerHTML = flags.map((f) => `
    <li class="flag sev-${f.severity}">
      <span class="icon" aria-hidden="true">${SEV_ICON[f.severity]}</span>
      <div><span class="title">${f.title}</span><span class="sev">${f.severity}</span>
      <div class="detail">${f.detail}</div></div>
    </li>`).join("");
}

// ---------------------------------------------------------------- charts
const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
const SERIES = () => [css("--series-1"), css("--series-2"), css("--series-3")];

function merge(a, b) {
  const out = { ...a };
  for (const [k, v] of Object.entries(b || {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) && a[k] ? merge(a[k], v) : v;
  }
  return out;
}

function baseLayout(extra) {
  return merge({
    paper_bgcolor: css("--surface-1"), plot_bgcolor: css("--surface-1"),
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: css("--text-secondary"), size: 12 },
    margin: { l: 60, r: 90, t: 30, b: 40 },
    xaxis: { showgrid: false, linecolor: css("--axis"), tickfont: { color: css("--text-muted") }, type: "date" },
    yaxis: { gridcolor: css("--grid"), zerolinecolor: css("--axis"), zerolinewidth: 1, tickfont: { color: css("--text-muted") } },
    legend: { orientation: "h", x: 0, y: 1.14, font: { color: css("--text-secondary") } },
    hovermode: "x unified",
    hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--axis"), font: { color: css("--text-primary") } },
    shapes: asOfMarker(),
  }, extra);
}

function asOfMarker() {
  return [{ type: "line", xref: "x", yref: "paper", x0: state.period, x1: state.period, y0: 0, y1: 1,
    line: { color: css("--axis"), width: 1 } }];
}

const CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

/** Line chart; values in $M if unit === "usd". Endpoint direct labels in text ink. */
function lineChart(id, cols, { unit = "usd", names, extra = {}, extraShapes = [] } = {}) {
  const scale = unit === "usd" ? M : 1;
  const hover = unit === "usd" ? "$%{y:,.0f}M" : unit === "x" ? "%{y:.2f}×" : "%{y:.2f}";
  const colors = SERIES();
  const x = xs();
  const traces = cols.map((c, k) => ({
    x, y: ys(c, scale), name: names ? names[k] : label(c), type: "scatter", mode: "lines",
    line: { color: colors[k], width: 2 }, connectgaps: false, hovertemplate: `${hover}<extra>${names ? names[k] : label(c)}</extra>`,
  }));
  const annotations = traces.map((t) => {
    let j = t.y.length - 1;
    while (j >= 0 && t.y[j] == null) j--;
    if (j < 0) return null;
    const txt = unit === "usd" ? fmt.usdM(t.y[j]) : unit === "x" ? `${t.y[j].toFixed(2)}×` : t.y[j].toFixed(2);
    return { x: t.x[j], y: t.y[j], text: txt, xanchor: "left", xshift: 6, showarrow: false, font: { color: css("--text-secondary"), size: 11 } };
  }).filter(Boolean);
  const layout = baseLayout(merge({ annotations, showlegend: cols.length > 1, yaxis: { tickformat: unit === "usd" ? "$,.0f" : "", ticksuffix: unit === "usd" ? "M" : unit === "x" ? "×" : "" } }, extra));
  layout.shapes = [...asOfMarker(), ...extraShapes];
  Plotly.react(id, traces, layout, CONFIG);
}

function barChart(id, groups, { names, unit = "usd" } = {}) {
  const colors = SERIES();
  const x = xs();
  const traces = groups.map((c, k) => ({
    x, y: ys(c, M), name: names ? names[k] : label(c), type: "bar",
    marker: { color: colors[k], cornerradius: 4 },
    hovertemplate: `$%{y:,.0f}M<extra>${names ? names[k] : label(c)}</extra>`,
  }));
  Plotly.react(id, traces, baseLayout({
    barmode: "group", bargap: 0.25, bargroupgap: 0.08, showlegend: groups.length > 1,
    yaxis: { tickformat: "$,.0f", ticksuffix: "M" }, margin: { r: 20 },
  }), CONFIG);
}

const hasPrices = () => state.prices && state.prices.available;

/** Dashed stock-price line on a secondary axis for an already-drawn time-series chart. */
function addPriceOverlay(id) {
  if (!state.overlay || !hasPrices()) return;
  const { dates, close } = state.prices.daily;
  const x = [], y = [];
  dates.forEach((d, i) => { if (d.slice(0, 4) >= state.start) { x.push(d); y.push(close[i]); } });
  Plotly.addTraces(id, [{
    x, y, yaxis: "y2", name: `${state.prices.symbol} price`, type: "scatter", mode: "lines",
    line: { color: css("--text-secondary"), width: 1.25, dash: "dot" }, hovertemplate: "$%{y:,.2f}<extra>Stock price</extra>",
  }]);
  Plotly.relayout(id, {
    yaxis2: { overlaying: "y", side: "right", showgrid: false, tickprefix: "$", tickfont: { color: css("--text-muted") }, automargin: true },
    "margin.r": 70, showlegend: true,
  });
}

function renderCharts() {
  const ov = state.overview;
  // derived series for the capital-structure stack
  const add = (...cols) => ov.series[cols[0]].map((_, i) => cols.reduce((s, c) => s + (ov.series[c][i] || 0), 0));
  ov.series._debt_due = add("debt_current", "short_term_borrowings");
  ov.series._debt_lt = add("debt_noncurrent", "finance_leases");
  ov.series._op_leases = ov.series.operating_leases;

  lineChart("ch-cash-debt", ["liquid_assets", "total_debt"]);

  const colors = SERIES();
  const stackCols = [["_debt_due", "Debt due ≤ 12 months"], ["_debt_lt", "Long-term debt + finance leases"], ["_op_leases", "Operating lease liabilities"]];
  Plotly.react("ch-cap-structure", stackCols.map(([c, n], k) => ({
    x: xs(), y: ys(c, M), name: n, type: "bar", marker: { color: colors[k], line: { color: css("--surface-1"), width: 2 } },
    hovertemplate: `$%{y:,.0f}M<extra>${n}</extra>`,
  })), baseLayout({ barmode: "stack", bargap: 0.2, yaxis: { tickformat: "$,.0f", ticksuffix: "M" }, margin: { r: 20 } }), CONFIG);

  lineChart("ch-ratios", ["current_ratio", "quick_ratio"], {
    unit: "x", extraShapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: { color: css("--text-muted"), width: 1 } }],
  });
  barChart("ch-fcf", ["fcf"]);
  barChart("ch-returns", ["shareholder_returns_ttm", "fcf_ttm"], { names: ["Buybacks + dividends (TTM)", "Free cash flow (TTM)"] });

  lineChart("ch-ebitda", ["ebitda_ttm", "adj_ebitda_ttm", "interest_expense_ttm"],
    { names: ["EBITDA", "Adj. EBITDA (ex-impairments)", "Interest expense"] });
  // Multiples explode as EBITDA approaches zero; cap the axis so the normal range stays readable.
  const lev = [...ys("debt_to_ebitda"), ...ys("lease_adj_leverage")].filter((v) => v != null);
  const levMax = Math.min(Math.max(6, ...lev) * 1.1, 20);
  lineChart("ch-leverage", ["debt_to_ebitda", "lease_adj_leverage"], {
    unit: "x", names: ["Debt / EBITDA", "Lease-adj. debt / EBITDAR"], extra: { yaxis: { range: [0, levMax] } },
  });
  lineChart("ch-debt-fv", ["notes_principal_est", "debt_fair_value"], { names: ["Principal", "Fair value"] });

  const band = (y0, y1, color, text) => ({ type: "rect", xref: "paper", x0: 0, x1: 1, y0, y1, fillcolor: color, opacity: 0.10, line: { width: 0 }, layer: "below", label: { text, textposition: "bottom left", font: { size: 11, color: css("--text-secondary") } } });
  const z = ys("z_double_prime").filter((v) => v != null);
  const zmin = Math.min(-1, ...z), zmax = Math.max(4, ...z);
  lineChart("ch-z", ["z_double_prime"], {
    unit: "num", names: ["Z''"],
    extraShapes: [band(zmin - 1, 1.1, css("--critical"), "Distress"), band(1.1, 2.6, css("--warning"), "Grey"), band(2.6, zmax + 1, css("--good"), "Safe")],
    extra: { yaxis: { range: [zmin - 0.5, zmax + 0.5] } },
  });
  ["ch-cash-debt", "ch-cap-structure", "ch-ratios", "ch-fcf", "ch-returns", "ch-ebitda", "ch-leverage", "ch-debt-fv", "ch-z"]
    .forEach(addPriceOverlay);
}

// ---------------------------------------------------------------- stock price tab
const REL_COLOR = () => ({ critical: css("--critical"), restructuring: css("--warning"), financing: css("--series-2") });

function renderPrices() {
  const p = state.prices;
  const toggle = $("#overlay-toggle");
  toggle.disabled = !hasPrices();
  if (!hasPrices()) { state.overlay = false; toggle.checked = false; }
  $("#price-meta").textContent = hasPrices() ? `${p.symbol} · ${p.first_date} to ${p.last_date}` : "";
  $("#price-msg").textContent = p == null ? "Loading prices from Yahoo Finance…" : hasPrices() ? "" : p.reason;
  $("#price-msg").classList.toggle("error", p != null && !hasPrices());
  if (!hasPrices()) {
    ["ch-price", "reaction-table", "corr-table"].forEach((id) => { $("#" + id).innerHTML = ""; });
    if ($("#ch-price").data) Plotly.purge("ch-price");
    return;
  }

  const { dates, close } = p.daily;
  const keyEvents = p.events.filter((e) => REL_COLOR()[e.relevance] && e.price != null);
  const traces = [{ x: dates, y: close, name: `${p.symbol} close`, type: "scatter", mode: "lines",
    line: { color: css("--series-1"), width: 1.5 }, hovertemplate: "$%{y:,.2f}<extra></extra>" }];
  for (const [rel, color] of Object.entries(REL_COLOR())) {
    const ev = keyEvents.filter((e) => e.relevance === rel);
    if (!ev.length) continue;
    traces.push({
      x: ev.map((e) => e.filed), y: ev.map((e) => e.price), name: rel, type: "scatter", mode: "markers",
      marker: { color, size: 9, line: { color: css("--surface-1"), width: 1.5 } },
      text: ev.map((e) => `${e.form}: ${e.items.map((t) => t.label).join("; ")}<br>Move: ${fmt.ret(e.move_1d)}`),
      hovertemplate: "%{x}<br>%{text}<extra></extra>",
    });
  }
  // drawdown panel
  let peak = -Infinity;
  const dd = close.map((v) => { peak = Math.max(peak, v); return (v / peak - 1) * 100; });
  traces.push({ x: dates, y: dd, yaxis: "y2", name: "Drawdown", type: "scatter", mode: "lines", fill: "tozeroy",
    line: { color: css("--critical"), width: 1 }, fillcolor: "rgba(200,60,60,0.15)", hovertemplate: "%{y:.1f}%<extra>Drawdown</extra>" });
  Plotly.react("ch-price", traces, baseLayout({
    hovermode: "closest", shapes: [], margin: { r: 30 },
    xaxis: { anchor: "y2" },
    yaxis: { domain: [0.34, 1], tickprefix: "$" },
    yaxis2: { domain: [0, 0.26], ticksuffix: "%", gridcolor: css("--grid"), tickfont: { color: css("--text-muted") } },
  }), CONFIG);

  const REL_NAMES = { critical: "Critical (default, delisting, bankruptcy…)", restructuring: "Restructuring", financing: "Financing",
    governance: "Governance", report: "10-K / 10-Q", info: "Other" };
  $("#reaction-table").innerHTML = `<thead><tr><th style="text-align:left">Filing type</th><th>N</th><th>Avg move</th><th>Median</th><th>% negative</th></tr></thead><tbody>` +
    p.event_summary.map((r) => `<tr><td style="text-align:left">${REL_NAMES[r.relevance] || r.relevance}</td><td>${r.n}</td>
      <td class="${r.avg_move < 0 ? "neg" : ""}">${fmt.ret(r.avg_move)}</td><td class="${r.median_move < 0 ? "neg" : ""}">${fmt.ret(r.median_move)}</td>
      <td>${(r.pct_negative * 100).toFixed(0)}%</td></tr>`).join("") + "</tbody>";

  const c2 = (v) => v == null ? "–" : `<span class="${v < 0 ? "neg" : ""}">${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}</span>`;
  $("#corr-table").innerHTML = `<thead><tr><th style="text-align:left">Signal</th><th>Level → next qtr</th><th>Change → same qtr</th><th>N</th></tr></thead><tbody>` +
    p.correlations.map((r) => `<tr><td style="text-align:left">${label(r.metric)}</td><td>${c2(r.level_vs_next)}</td><td>${c2(r.change_vs_same)}</td>
      <td>${Math.max(r.n_level, r.n_change) || "–"}</td></tr>`).join("") + "</tbody>";
}

// ---------------------------------------------------------------- data table
function renderTable() {
  const periods = inRange().reverse();
  const head = `<thead><tr><th>Line item</th>${periods.map(([p]) => `<th>${p.label === p.end ? p.end : p.label}<br><span class="muted">${p.end}</span></th>`).join("")}</tr></thead>`;
  const body = TABLE_SECTIONS.map(([title, cols]) =>
    `<tr class="section"><td colspan="${periods.length + 1}">${title}</td></tr>` +
    cols.map((c) => `<tr><td>${label(c)}</td>${periods.map(([, i]) => {
      const v = state.overview.series[c] ? state.overview.series[c][i] : null;
      return `<td class="${v != null && v < 0 ? "neg" : ""}">${format(c, v)}</td>`;
    }).join("")}</tr>`).join("")).join("");
  $("#data-table").innerHTML = head + `<tbody>${body}</tbody>`;

  const src = state.overview.sources;
  $("#sources-table").innerHTML = `<thead><tr><th>Line item</th><th style="text-align:left">us-gaap tag(s)</th></tr></thead><tbody>` +
    Object.entries(src).map(([k, tags]) => `<tr><td>${label(k)}</td><td style="text-align:left">${tags.join(", ")}</td></tr>`).join("") + "</tbody>";
}

// ---------------------------------------------------------------- events
const EVENT_FILTERS = [["key", "Key events"], ["critical", "Critical"], ["restructuring", "Restructuring"], ["financing", "Financing"], ["governance", "Governance"], ["report", "10-K / 10-Q"], ["all", "All"]];
function renderEvents() {
  $("#event-filters").innerHTML = EVENT_FILTERS.map(([k, n]) =>
    `<button class="chip" data-filter="${k}" aria-pressed="${state.eventFilter === k}">${n}</button>`).join("");
  const keep = (e) => state.eventFilter === "all" ? true
    : state.eventFilter === "key" ? e.items.some((t) => ["critical", "restructuring", "financing"].includes(t.relevance))
      : e.items.some((t) => t.relevance === state.eventFilter);
  const rows = state.overview.events.filter(keep);
  $("#events").innerHTML = rows.length ? rows.map((e) => `
    <li><span class="date">${e.filed}</span><a href="${e.url}" target="_blank" rel="noopener">${e.form}</a>
      <div>${e.items.map((t) => `<span class="tag rel-${t.relevance}">${t.code === e.form ? "" : t.code + " "}${t.label}</span>`).join("")}</div></li>`).join("")
    : `<li><span></span><span></span><div class="muted">No filings in this category.</div></li>`;
}

// ---------------------------------------------------------------- restructuring lab
// field spec: [key, label, kind]  kind: usd | pct | cents | x | int | select:<a,b>
const LAB = {
  waterfall: {
    form: "#wf-form", endpoint: "/api/waterfall",
    groups: [
      ["Valuation", [["mode", "Basis", "select:liquidation,going_concern"], ["ebitda", "EBITDA ($M)", "usd"], ["ev_multiple", "EV / EBITDA", "x"], ["admin_cost_pct", "Admin costs %", "pct"]]],
      ["Asset book values ($M)", [["cash", "Cash & inv.", "usd"], ["receivables", "Receivables", "usd"], ["inventory", "Inventory", "usd"], ["ppe", "PP&E", "usd"], ["other_assets", "Other", "usd"]]],
      ["Recovery rates (%)", [["recovery_rates.cash", "Cash", "pct"], ["recovery_rates.receivables", "Receivables", "pct"], ["recovery_rates.inventory", "Inventory (NOLV)", "pct"], ["recovery_rates.ppe", "PP&E", "pct"], ["recovery_rates.other_assets", "Other", "pct"]]],
      ["Claims ($M)", [["priority_claims", "Priority", "usd"], ["secured_debt", "Secured (ABL/FILO)", "usd"], ["unsecured_notes", "Unsecured notes", "usd"], ["trade_payables", "Trade payables", "usd"], ["lease_rejection_claims", "Lease rejection", "usd"], ["other_unsecured", "Other unsecured", "usd"]]],
    ],
    render: renderWaterfall,
  },
  exchange: {
    form: "#ex-form", endpoint: "/api/exchange",
    groups: [
      ["Offer", [["notes_face", "Notes face ($M)", "usd"], ["participation", "Participation %", "pct"], ["price", "Price (¢ per $1)", "cents"], ["consideration", "Paid in", "select:new_secured_debt,cash,equity"]]],
      ["Rates", [["old_coupon", "Old coupon %", "pct"], ["new_coupon", "New debt coupon %", "pct"], ["other_debt", "Other debt ($M)", "usd"], ["other_debt_rate", "Other debt rate %", "pct"]]],
      ["Company", [["liquid_assets", "Liquid assets ($M)", "usd"], ["ebitda", "EBITDA ($M)", "usd"], ["new_equity_raise", "New equity ($M)", "usd"], ["tax_rate", "Tax rate %", "pct"]]],
    ],
    render: renderExchange,
  },
  runway: {
    form: "#rw-form", endpoint: "/api/runway",
    groups: [
      ["Liquidity", [["starting_liquidity", "Cash ($M)", "usd"], ["revolver_availability", "Undrawn revolver ($M)", "usd"], ["new_capital", "New capital, month 1 ($M)", "usd"]]],
      ["Burn", [["monthly_burn", "Monthly burn ($M)", "usd"], ["burn_reduction_pct", "Burn reduction %", "pct"], ["ramp_months", "Ramp (months)", "int"]]],
      ["Maturities", [["debt_maturities", "Maturity ($M)", "usd"], ["maturity_month", "Due in month", "int"], ["horizon_months", "Horizon (months)", "int"]]],
    ],
    render: renderRunway,
  },
};

const getPath = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, path, v) {
  const ks = path.split(".");
  let cur = o;
  ks.slice(0, -1).forEach((k) => { cur[k] = cur[k] || {}; cur = cur[k]; });
  cur[ks.at(-1)] = v;
}
const toDisplay = (kind, v) => kind === "usd" ? +(v / M).toFixed(1) : kind === "pct" ? +(v * 100).toFixed(2) : kind === "cents" ? +(v * 100).toFixed(1) : v;
const fromDisplay = (kind, v) => kind === "usd" ? v * M : (kind === "pct" || kind === "cents") ? v / 100 : kind === "int" ? Math.round(v) : v;

function buildLabForms() {
  $("#lab-period").textContent = state.snapshot.fiscal_label || state.snapshot.period_end;
  for (const [name, spec] of Object.entries(LAB)) {
    const defaults = state.snapshot.defaults[name];
    const form = $(spec.form);
    form.innerHTML = spec.groups.map(([title, fields]) => `<fieldset><legend>${title}</legend>${fields.map(([key, lab, kind]) => {
      const v = getPath(defaults, key);
      if (kind.startsWith("select:")) {
        const opts = kind.slice(7).split(",");
        return `<label>${lab}<select name="${key}" data-kind="select">${opts.map((o) => `<option value="${o}" ${o === v ? "selected" : ""}>${o.replaceAll("_", " ")}</option>`).join("")}</select></label>`;
      }
      return `<label>${lab}<input type="number" step="any" name="${key}" data-kind="${kind}" value="${toDisplay(kind, v)}"></label>`;
    }).join("")}</fieldset>`).join("") +
      `<div class="actions"><button type="button" data-reset="${name}">Reset to filing</button></div>`;
    let timer;
    form.oninput = () => { clearTimeout(timer); timer = setTimeout(() => runModel(name), 250); };
    form.onsubmit = (e) => { e.preventDefault(); runModel(name); };
    form.querySelector("[data-reset]").onclick = () => { buildLabForms(); runLab(); };
  }
}

function readForm(name) {
  const out = {};
  $(LAB[name].form).querySelectorAll("[name]").forEach((el) => {
    const kind = el.dataset.kind;
    if (kind === "select") setPath(out, el.name, el.value);
    else if (el.value !== "") setPath(out, el.name, fromDisplay(kind, parseFloat(el.value)));
  });
  return out;
}

async function runModel(name) {
  try {
    const res = await api(LAB[name].endpoint, { q: state.q, period: state.period, inputs: readForm(name) });
    LAB[name].render(res);
  } catch (e) { setStatus(e.message, true); }
}
function runLab() { Object.keys(LAB).forEach(runModel); }

function renderWaterfall(r) {
  const cls = r.classes.filter((c) => c.claim);
  const names = cls.map((c) => c.class);
  Plotly.react("ch-waterfall", [{
    type: "bar", orientation: "h", y: names, x: cls.map((c) => c.recovery_pct ?? 0),
    marker: { color: css("--series-1"), cornerradius: 4 },
    text: cls.map((c) => `${(c.recovery_pct ?? 0).toFixed(0)}%`), textposition: "outside", cliponaxis: false,
    textfont: { color: css("--text-secondary") },
    customdata: cls.map((c) => [c.claim / M, c.recovery / M]),
    hovertemplate: "%{y}<br>Recovery %{x:.1f}%<br>$%{customdata[1]:,.0f}M of $%{customdata[0]:,.0f}M<extra></extra>",
  }], baseLayout({
    xaxis: { type: "linear", range: [0, 115], ticksuffix: "%", showgrid: true, gridcolor: css("--grid") },
    yaxis: { autorange: "reversed", gridcolor: "rgba(0,0,0,0)", automargin: true },
    margin: { l: 10, r: 20, t: 10 }, hovermode: "closest", shapes: [], showlegend: false,
  }), CONFIG);

  const equity = r.classes.find((c) => c.class === "Equity");
  $("#wf-result").innerHTML = `
    <div class="callout">Distributable value <strong>${fmt.usd(r.distributable_value)}</strong><br>
      <span class="muted">${r.basis}. Liquidation value ${fmt.usd(r.liquidation_value)} · going-concern value ${fmt.usd(r.going_concern_value)} · admin costs ${fmt.usd(r.admin_costs)}</span></div>
    <p>Fulcrum security: <strong>${r.fulcrum || "none, all claims paid in full"}</strong>.
      ${r.fulcrum ? "This is the class most likely to own the reorganized equity." : ""}
      Equity receives <strong>${fmt.usd(equity.recovery)}</strong>.</p>
    <table class="data-table"><thead><tr><th>Class</th><th>Claim</th><th>Recovery</th><th>%</th></tr></thead><tbody>
      ${r.classes.map((c) => `<tr><td>${c.class}</td><td>${c.claim == null ? "–" : fmt.usd(c.claim)}</td><td>${fmt.usd(c.recovery)}</td><td>${c.recovery_pct == null ? "–" : fmt.pct100(c.recovery_pct)}</td></tr>`).join("")}
    </tbody></table>
    <p class="muted">Compare: the notes' fair value implied a market recovery of about <strong>${fmt.cents(state.snapshot.values.debt_price_pct)}</strong> on the dollar.</p>`;
}

function renderExchange(r) {
  const f = (row, v) => row.fmt === "usd" ? fmt.usd(v) : fmt.x(v);
  $("#ex-result").innerHTML = `
    <div class="grid-2">
      <div>
        <table class="data-table"><thead><tr><th>Metric</th><th>Pre</th><th>Pro forma</th><th>Change</th></tr></thead><tbody>
          ${r.table.map((row) => `<tr><td>${row.metric}</td><td>${f(row, row.pre)}</td><td>${f(row, row.post)}</td>
            <td>${row.pre == null || row.post == null ? "–" : row.fmt === "usd" ? fmt.usd(row.post - row.pre) : fmt.x(row.post - row.pre)}</td></tr>`).join("")}
        </tbody></table>
      </div>
      <div>
        <div class="callout">Discount captured <strong>${fmt.usd(r.discount_captured)}</strong><br>
          <span class="muted">${fmt.usd(r.tendered_face)} face tendered for ${fmt.usd(r.consideration_value)} of consideration.
          COD income ${fmt.usd(r.cod_income)} (potential tax ${fmt.usd(r.cod_tax_if_not_excluded)} unless the §108 insolvency exclusion applies).</span></div>
        <ul class="notes">${r.notes.map((n) => `<li>${n}</li>`).join("")}</ul>
      </div>
    </div>`;
}

function renderRunway(r) {
  const y = r.liquidity.map((v) => v / M);
  Plotly.react("ch-runway", [{
    x: r.months, y, type: "scatter", mode: "lines", name: "Liquidity", line: { color: css("--series-1"), width: 2 },
    hovertemplate: "Month %{x}: $%{y:,.0f}M<extra></extra>",
  }], baseLayout({
    xaxis: { type: "linear", title: { text: "Months from period end", font: { size: 11 } } },
    yaxis: { tickformat: "$,.0f", ticksuffix: "M", zerolinecolor: css("--critical"), zerolinewidth: 1 },
    shapes: [], showlegend: false, margin: { r: 20 }, hovermode: "x",
    annotations: r.zero_month ? [{ x: r.zero_month, y: 0, text: `Cash out in month ${r.zero_month}`, showarrow: true, arrowcolor: css("--text-muted"), ax: 40, ay: -40, font: { color: css("--text-primary") } }] : [],
  }), CONFIG);
  const end = r.liquidity.at(-1);
  $("#rw-result").innerHTML = r.zero_month
    ? `<div class="callout"><span class="flag sev-critical" style="display:inline-grid"><span class="icon">!</span></span>
         Liquidity runs out in <strong>month ${r.zero_month}</strong>.</div>
       <p class="muted">To bridge the full horizon you'd need about <strong>${fmt.usd(-Math.min(...r.liquidity))}</strong> of additional capital, burn cuts or DIP financing.</p>`
    : `<div class="callout">Liquidity stays positive through month ${r.months.at(-1)}. Ending balance <strong>${fmt.usd(end)}</strong>.</div>`;
}

// ---------------------------------------------------------------- company search
let searchTimer = null, searchSeq = 0;
function onCompanyInput(e) {
  const q = e.target.value.trim();
  clearTimeout(searchTimer);
  if (q.length < 2) return;
  searchTimer = setTimeout(async () => {
    const seq = ++searchSeq;
    try {
      const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq) return; // stale response
      const list = $("#company-suggestions");
      list.replaceChildren(...results.map((r) => {
        const o = document.createElement("option");
        o.value = r.ticker;
        o.label = `${r.name} (CIK ${r.cik})`;
        o.textContent = o.label;
        return o;
      }));
    } catch (_) { /* suggestions are best-effort */ }
  }, 200);
}

// ---------------------------------------------------------------- wiring
document.addEventListener("DOMContentLoaded", () => {
  $("#company-q").addEventListener("input", onCompanyInput);
  $("#overlay-toggle").onchange = (e) => { state.overlay = e.target.checked; renderCharts(); };
  $("#symbol-form").onsubmit = (e) => { e.preventDefault(); state.symbol = $("#symbol-q").value.trim(); if (state.overview) { loadPrices(); loadNews(); } };
  $("#company-form").onsubmit = (e) => { e.preventDefault(); loadCompany($("#company-q").value.trim()); };
  $("#period-select").onchange = async (e) => {
    state.period = e.target.value;
    $("main").classList.add("loading");
    try { await loadSnapshot(); } catch (err) { setStatus(err.message, true); }
    $("main").classList.remove("loading");
  };
  $("#range-select").onchange = (e) => { state.start = e.target.value; renderCharts(); renderTable(); };
  $("#event-filters").onclick = (e) => {
    const f = e.target.dataset.filter;
    if (f) { state.eventFilter = f; renderEvents(); }
  };
  const showTab = (name) => {
    const b = document.querySelector(`.tabs button[data-tab="${name}"]`);
    if (!b) return;
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll(".tab-panel").forEach((p) => p.hidden = p.id !== `tab-${name}`);
    // Plotly can't size charts inside hidden panels; resize once visible.
    document.querySelectorAll(`#tab-${name} .js-plotly-plot`).forEach((el) => Plotly.Plots.resize(el));
  };
  document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
    history.replaceState(null, "", `#${b.dataset.tab}`);
    showTab(b.dataset.tab);
  });
  showTab(location.hash.slice(1) || "overview");
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.snapshot) { renderCharts(); runLab(); } });
  loadCompany($("#company-q").value.trim());
});
