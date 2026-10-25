"use strict";

const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
// Page in the HTML to the last part of the address, per language. Both spellings always work.
const PATHS = {
  overview: { en: "", de: "" },
  traffic: { en: "traffic", de: "verteilung" },
  geo: { en: "countries", de: "laender" },
  visitors: { en: "visitors", de: "besucher" },
  paths: { en: "files", de: "dateien" },
};
const ROUTES = Object.fromEntries(Object.entries(PATHS).flatMap(([page, p]) => [[p.en, page], [p.de, page]]));
const $ = (id) => document.getElementById(id);

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* not important */ } },
};
const browserLang = () => ((navigator.languages || [navigator.language]).find((l) => /^(en|de)/i.test(l)) || "en").slice(0, 2).toLowerCase();
const state = {
  days: Number(store.get("days", "7")) || 7, host: store.get("host", ""),
  lang: I18N[store.get("lang", "")] ? store.get("lang", "") : browserLang(),
  page: "overview", country: "", data: null, meta: null, world: null,
};
let nf, regionNames;

function t(key, vars = {}) {
  const s = I18N[state.lang][key] ?? I18N.en[key] ?? key;
  return typeof s === "string" ? s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "") : s;
}

function color(i) {
  return getComputedStyle(document.documentElement).getPropertyValue(COLORS[Math.min(i, COLORS.length - 1)]).trim();
}

function fmtBytes(b) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  const digits = b >= 100 || i === 0 ? 0 : b >= 10 ? 1 : 2;
  return b.toLocaleString(t("locale"), { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " " + units[i];
}
// Flag from regional indicator symbols, Windows only shows the two letters
const flag = (cc) => (/^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map((c) => 0x1f1a5 + c.charCodeAt(0))) : "");
function countryName(cc) {
  if (t("special_cc")[cc]) return t("special_cc")[cc];
  try { return regionNames?.of(cc) || cc; } catch { return cc; }
}
const countryLabel = (cc) => (flag(cc) ? flag(cc) + " " : "") + countryName(cc);
const kindName = (k) => t("kinds")[k] || k;
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0) + (state.lang === "de" ? " %" : "%");
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (day, opts) => new Date(day + "T12:00").toLocaleDateString(t("locale"), opts);

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + ": " + r.status);
  return r.json();
}

function showTip(ev, html) {
  const tip = $("tip");
  tip.innerHTML = html;
  tip.hidden = false;
  const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + "px";
  tip.style.top = Math.max(8, ev.clientY - tip.offsetHeight - 12) + "px";
}
const hideTip = () => ($("tip").hidden = true);

// ---------- Language ----------

// Static texts in the HTML carry data-i18n (text), data-i18n-aria (aria-label) or a page key in data-page
function applyLang() {
  nf = new Intl.NumberFormat(t("locale"));
  try { regionNames = new Intl.DisplayNames([state.lang], { type: "region" }); } catch { regionNames = null; }
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
  document.querySelectorAll("#nav a, a[data-link]").forEach((a) => a.setAttribute("href", PATHS[a.dataset.page][state.lang] || "./"));
  document.querySelectorAll("#lang button").forEach((b) => b.classList.toggle("on", b.dataset.lang === state.lang));
  const opt = $("host").querySelector('option[value=""]');
  if (opt) opt.textContent = t("all_domains");
  if (!$("clear-box").hidden) setClearAsk();
}

function setLang(lang) {
  state.lang = lang;
  store.set("lang", lang);
  applyLang();
  // keep the address in the chosen language
  history.replaceState(null, "", pageUrl(state.page, state.country));
  showPage();
  if (state.meta) renderFoot(state.meta);
  if (state.data) render(state.data);
}

// ---------- Bar chart over time ----------

function niceMax(v) {
  if (v <= 0) return 1;
  const units = [1, 1024, 1024 ** 2, 1024 ** 3, 1024 ** 4];
  let u = units[0];
  for (const x of units) if (v >= x) u = x;
  const n = v / u;
  const step = [1, 2, 2.5, 5, 10].find((s) => n / s <= 5) || 10;
  return Math.ceil(n / step) * step * u;
}

function dayLabel(l, hourly) {
  if (hourly) return l;
  const [, m, d] = l.split("-");
  return state.lang === "de" ? `${Number(d)}.${Number(m)}.` : `${Number(d)}/${Number(m)}`;
}
const longDay = (day) => fmtDate(day, { weekday: "short", day: "numeric", month: "long" });

function renderChart(d) {
  const el = $("chart");
  if (!el.clientWidth) return;  // page not visible right now
  const W = el.clientWidth, H = el.clientHeight || 260;
  const pad = { l: 58, r: 6, t: 8, b: 24 };
  const n = d.labels.length;
  let series = d.series.slice(0, 7);
  if (d.series.length > 7) {
    const rest = new Array(n).fill(0);
    d.series.slice(7).forEach((s) => s.values.forEach((v, i) => (rest[i] += v)));
    series.push({ name: t("others"), values: rest });
  }
  const sums = d.labels.map((_, i) => series.reduce((a, s) => a + s.values[i], 0));
  const max = niceMax(Math.max(0, ...sums));
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const bw = iw / n, gap = Math.min(6, bw * 0.25);
  const y = (v) => pad.t + ih - (v / max) * ih;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t("chart_aria"))}">`;
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = y(v);
    svg += `<line class="gridline" x1="${pad.l}" x2="${W - pad.r}" y1="${yy}" y2="${yy}"/>`;
    svg += `<text class="axis" x="${pad.l - 8}" y="${yy + 4}" text-anchor="end">${fmtBytes(v)}</text>`;
  }
  const every = Math.ceil(n / Math.max(1, Math.floor(iw / 52)));
  for (let i = 0; i < n; i++) {
    const x = pad.l + i * bw;
    svg += `<g class="col" data-i="${i}">`;
    let acc = 0;
    series.forEach((s, si) => {
      const v = s.values[i];
      if (!v) return;
      const y1 = y(acc + v), y0 = y(acc);
      svg += `<rect class="bar" x="${x + gap / 2}" y="${y1}" width="${Math.max(1, bw - gap)}" height="${Math.max(0.5, y0 - y1)}" fill="${color(si)}" rx="1.5"/>`;
      acc += v;
    });
    svg += `<rect class="hit" x="${x}" y="${pad.t}" width="${bw}" height="${ih}"/></g>`;
    if (i % every === 0 || i === n - 1 && n < 10) {
      svg += `<text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${dayLabel(d.labels[i], d.hourly)}</text>`;
    }
  }
  svg += "</svg>";
  el.innerHTML = svg;

  $("legend").innerHTML = series.map((s, i) => `<span><i style="background:${color(i)}"></i>${esc(s.name)}</span>`).join("");

  el.querySelectorAll("g.col").forEach((g) => {
    g.addEventListener("mousemove", (ev) => {
      const i = Number(g.dataset.i);
      const rows = series.map((s, si) => [s.name, s.values[i], si]).filter((r) => r[1] > 0).sort((a, b) => b[1] - a[1]);
      const title = d.hourly ? t("hour_title", { h: d.labels[i] }) : longDay(d.labels[i]);
      showTip(ev, `<b>${title}: ${fmtBytes(sums[i])}</b>` + (rows.length
        ? rows.map((r) => `<div class="row"><span><i style="background:${color(r[2])}"></i>${esc(r[0])}</span><span>${fmtBytes(r[1])}</span></div>`).join("")
        : `<div>${t("no_traffic")}</div>`));
    });
    g.addEventListener("mouseleave", hideTip);
  });
}

// ---------- World map ----------

function renderMap(d) {
  const el = $("map");
  const w = state.world;
  if (!w) { el.innerHTML = `<div class="empty">${t("map_loading")}</div>`; return; }
  const by = Object.fromEntries(d.countries.map((c) => [c.name, c]));
  const vals = d.countries.filter((c) => w.shapes[c.name] && c.bytes > 0).map((c) => c.bytes);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  // logarithmic, otherwise everything next to the largest country is almost white
  const level = (b) => (hi > lo ? 0.18 + 0.82 * Math.log(b / lo) / Math.log(hi / lo) : 1);
  let svg = `<svg viewBox="0 0 ${w.width} ${w.height}" role="img" aria-label="${esc(t("map_aria"))}">`;
  for (const [cc, path] of Object.entries(w.shapes)) {
    const c = by[cc];
    const style = c && c.bytes > 0 ? ` style="fill-opacity:${level(c.bytes).toFixed(2)}"` : "";
    svg += `<path d="${path}" data-cc="${cc}" class="${c && c.bytes > 0 ? "has" : ""}${cc === state.country ? " sel" : ""}"${style}/>`;
  }
  el.innerHTML = svg + "</svg>";
  // selected country on top so its outline is not covered
  el.querySelector("path.sel")?.parentNode.appendChild(el.querySelector("path.sel"));

  const total = d.countries.reduce((a, c) => a + c.bytes, 0);
  el.querySelectorAll("path").forEach((p) => {
    p.addEventListener("mousemove", (ev) => {
      const c = by[p.dataset.cc];
      showTip(ev, `<b>${esc(countryLabel(p.dataset.cc))}</b>` + (c
        ? `<div class="row"><span>${t("data")}</span><span>${fmtBytes(c.bytes)} · ${pct(c.bytes, total)}</span></div>`
          + `<div class="row"><span>${t("requests")}</span><span>${nf.format(c.requests)}</span></div>`
        : `<div>${t("no_traffic")}</div>`));
    });
    p.addEventListener("mouseleave", hideTip);
    p.addEventListener("click", () => { hideTip(); setCountry(p.dataset.cc); });
  });
}

// ---------- Lists and tables ----------

function renderBars(id, rows, opts = {}) {
  const el = $(id);
  if (!rows.length) { el.innerHTML = `<div class="empty">${t("empty")}</div>`; return; }
  const total = opts.total ?? rows.reduce((a, r) => a + r.bytes, 0);
  const top = rows[0].bytes || 1;
  el.innerHTML = rows.slice(0, opts.limit || 8).map((r, i) => {
    const name = opts.label ? opts.label(r.name) : r.name;
    const sel = opts.selected && opts.selected === r.name ? " sel" : "";
    return `<div class="bar-row${opts.click ? " click" : ""}${sel}" data-name="${esc(r.name)}" title="${t("n_requests", { n: nf.format(r.requests) })}">
      <span class="n">${esc(name)}${opts.sub && r.sub ? `<small>${esc(r.sub)}</small>` : ""}</span>
      <span class="v">${r.bytes ? fmtBytes(r.bytes) + " · " + pct(r.bytes, total) : t("n_requests_no_data", { n: nf.format(r.requests) })}</span>
      <div class="track"><div class="fill" style="width:${(r.bytes / top) * 100}%;background:${color(opts.mono ? 0 : i)}"></div></div>
    </div>`;
  }).join("");
  if (opts.click) el.querySelectorAll(".bar-row").forEach((row) => row.addEventListener("click", () => opts.click(row.dataset.name)));
}

function renderHod(hod) {
  const max = Math.max(1, ...hod);
  $("hod").innerHTML = hod.map((v, h) => `<div style="height:${(v / max) * 100}%" title="${t("hod_tip", { h, h2: h + 1, v: fmtBytes(v) })}"></div>`).join("");
  let axis = $("hod").nextElementSibling;
  if (!axis || !axis.classList.contains("hod-axis")) {
    axis = document.createElement("div");
    axis.className = "hod-axis";
    $("hod").after(axis);
  }
  axis.innerHTML = t("hod_axis").map((l) => `<span>${l}</span>`).join("");
}

function renderTable(el, head, rows) {
  if (typeof el === "string") el = $(el);
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">${t("empty")}</td></tr>`; return; }
  el.innerHTML = "<thead><tr>" + head.map((h, i) => `<th${i ? ' class="num"' : ""}>${t(h)}</th>`).join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody>";
}

const clientRow = (c, withCountry = true) =>
  `<tr><td>${esc(c.name)}<span class="badge">${esc(c.net)}</span>${withCountry ? `<span class="host">${esc(countryLabel(c.cc))}</span>` : ""}</td>`
  + `<td class="num">${fmtBytes(c.bytes)}</td><td class="num">${nf.format(c.requests)}</td></tr>`;

function renderGeoNote(d) {
  const m = state.meta || {};
  const total = d.countries.reduce((a, c) => a + c.bytes, 0);
  const cf = d.countries.find((c) => c.name === "CLOUDFLARE");
  let note = "";
  if (m.geo === false) note = t("geo_off");
  else if (!m.geo_ready) note = m.geo_error ? t("geo_error", { error: m.geo_error }) : t("geo_loading");
  else if (cf && total && cf.bytes / total > 0.2) note = t("geo_cloudflare", { p: pct(cf.bytes, total) });
  $("geo-note").textContent = note;
  $("geo-note").hidden = !note;
}

function renderCountry(d) {
  const el = $("country-detail");
  const c = d.country;
  $("country-clear").hidden = !state.country;
  if (!state.country || !c) {
    $("country-title").textContent = t("country");
    el.innerHTML = `<div class="empty">${t("pick_country")}</div>`;
    return;
  }
  $("country-title").textContent = countryLabel(c.code);
  const total = d.countries.reduce((a, x) => a + x.bytes, 0);
  const sum = c.hosts.reduce((a, h) => a + h.bytes, 0);
  const req = c.hosts.reduce((a, h) => a + h.requests, 0);
  const maxDay = Math.max(1, ...c.days.map((x) => x.bytes));
  el.innerHTML = `
    <div class="mini-kpis">
      <div><span class="label">${t("data")}</span><b>${fmtBytes(sum)}</b><span class="hint">${t("of_all_countries", { p: pct(sum, total) })}</span></div>
      <div><span class="label">${t("requests")}</span><b>${nf.format(req)}</b></div>
      <div><span class="label">${t("kpi_visitors")}</span><b>${nf.format(c.visitors)}</b></div>
    </div>
    <p class="sub-head">${t("country_days")}</p>
    <div class="days" style="grid-template-columns:repeat(${c.days.length},1fr)">${c.days.map((x) =>
      `<div style="height:${(x.bytes / maxDay) * 100}%" title="${longDay(x.day)}: ${fmtBytes(x.bytes)}"></div>`).join("")}</div>
    <p class="sub-head">${t("domains")}</p>
    <div class="bars" id="c-hosts"></div>
    <p class="sub-head">${t("country_clients")}</p>
    <div class="table-wrap"><table id="c-clients"></table></div>`;
  renderBars("c-hosts", c.hosts, { mono: true, limit: 10 });
  renderTable("c-clients", ["col_address", "col_data", "col_requests"], c.clients.slice(0, 15).map((x) => clientRow(x, false)));
}

function render(d) {
  const days = d.hourly ? 1 : d.days;
  $("k-bytes").textContent = fmtBytes(d.total.bytes);
  $("k-bytes-day").textContent = d.hourly ? t("last_24h") : t("per_day", { v: fmtBytes(d.total.bytes / days) });
  $("k-req").textContent = nf.format(d.total.requests);
  $("k-req-avg").textContent = d.total.requests ? t("per_request", { v: fmtBytes(d.total.bytes / d.total.requests) }) : "";
  $("k-inet").textContent = fmtBytes(d.total.internet);
  $("k-inet-share").textContent = t("share_of_bytes", { p: pct(d.total.internet, d.total.bytes) });
  $("k-vis").textContent = nf.format(d.total.visitors);

  $("chart-title").textContent = t(d.hourly ? "chart_hour" : "chart_day") + " " + t(d.host ? "by_area" : "by_domain");
  renderChart(d);

  if (d.host) {
    $("t-group").textContent = t("domain");
    renderBars("b-group", d.hosts, { mono: true });
  } else {
    $("t-group").textContent = t("domains");
    renderBars("b-group", d.hosts, { click: (h) => setHost(h) });
  }
  renderBars("b-kinds", d.kinds, { label: kindName });
  renderBars("b-countries-top", d.countries, { limit: 6, mono: true, label: countryLabel, click: (cc) => go("geo", cc) });

  renderBars("b-areas", d.areas, { limit: 15, mono: true, sub: !d.host });
  renderBars("b-status", d.statuses, { label: (s) => t("status")[s] || s });
  renderBars("b-nets", d.nets);
  renderHod(d.hod);

  renderMap(d);
  renderBars("b-countries", d.countries, { limit: 300, mono: true, label: countryLabel, click: setCountry, selected: state.country });
  renderGeoNote(d);
  renderCountry(d);

  renderTable("t-clients", ["col_address", "col_data", "col_requests"], d.clients.map((c) => clientRow(c)));
  renderTable("t-paths", ["col_path", "col_data", "col_requests"], d.paths.map((p) =>
    `<tr><td class="path">${esc(p.name)}${d.host ? "" : `<span class="host">${esc(p.host)}</span>`}</td><td class="num">${fmtBytes(p.bytes)}</td><td class="num">${nf.format(p.requests)}</td></tr>`));
}

// ---------- Pages ----------

function readRoute() {
  const seg = location.pathname.replace(/\/+$/, "").split("/").pop();
  state.page = ROUTES[seg] || "overview";
  const q = new URLSearchParams(location.search);
  state.country = state.page === "geo" ? (q.get("country") || q.get("land") || "").toUpperCase() : "";
}

function pageUrl(page, country = "") {
  const key = state.lang === "de" ? "land" : "country";
  return (PATHS[page][state.lang] || "./") + (country ? `?${key}=` + encodeURIComponent(country) : "");
}

function showPage() {
  document.querySelectorAll(".page").forEach((p) => (p.hidden = p.dataset.page !== state.page));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === state.page));
  document.title = state.page === "overview" ? "NPMcountdy" : `${t("page_" + state.page)} · NPMcountdy`;
  hideTip();
}

// Switch pages without reloading; the country is part of the address (?country=DE)
function go(page, country = "") {
  history.pushState(null, "", pageUrl(page, country));
  const before = state.country;
  readRoute();
  showPage();
  window.scrollTo(0, 0);
  if (state.country !== before) load();
  else if (state.data) render(state.data);
}

function setCountry(cc) {
  go("geo", cc === state.country ? "" : cc);
}

// ---------- Controls ----------

function setHost(h) {
  state.host = h;
  store.set("host", h);
  $("host").value = h;
  $("clear-msg").hidden = true;
  updateClear();
  load();
}

// Deleting is only offered for a selected domain and only if the server allows it (CLEAR)
function updateClear() {
  const on = Boolean(state.meta?.clear && state.host);
  if (!on) $("clear-box").hidden = true;
  $("clear-open").hidden = !on || !$("clear-box").hidden;
}

function setClearAsk() {
  const [before, after = ""] = t("clear_ask", { host: "\u0001" }).split("\u0001");
  $("clear-ask").innerHTML = esc(before) + `<b>${esc(state.host)}</b>` + esc(after);
}

function showClearMsg(text, err = false) {
  $("clear-msg").textContent = text;
  $("clear-msg").classList.toggle("err", err);
  $("clear-msg").hidden = false;
}

async function clearHost() {
  const host = state.host;
  $("clear-yes").disabled = true;
  try {
    const r = await fetch("api/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-NPMcountdy": "clear" },
      body: JSON.stringify({ host }),
    });
    const res = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(res.error || "HTTP " + r.status);
    $("clear-box").hidden = true;
    setHost("");
    showClearMsg(t("clear_done", { host, bytes: fmtBytes(res.bytes), requests: nf.format(res.requests) }));
    loadMeta().catch(() => {});
  } catch (e) {
    showClearMsg(t("clear_failed", { error: e.message }), true);
  } finally {
    $("clear-yes").disabled = false;
  }
}

function renderFoot(m) {
  const parts = [];
  if (m.version) parts.push("NPMcountdy " + m.version);
  if (m.first_day) parts.push(t("foot_since", { d: fmtDate(m.first_day) }));
  if (m.last_ingest) parts.push(t("foot_read", { t: new Date(m.last_ingest * 1000).toLocaleTimeString(t("locale"), { hour: "2-digit", minute: "2-digit" }) }));
  parts.push(t("foot_kept", { n: m.detail_days }));
  parts.push(t("foot_counted"));
  if (m.geo_date) parts.push(t("foot_geo", { d: fmtDate(m.geo_date) }));
  $("foot").innerHTML = esc(parts.join(" · "))
    + (m.geo ? ' · <a href="https://db-ip.com" target="_blank" rel="noopener">IP Geolocation by DB-IP</a>' : "")
    + " · " + esc(t("foot_map"))
    + (m.last_error ? `<br><span class="err">${esc(t("foot_error", { error: m.last_error }))}</span>` : "");
}

async function loadMeta() {
  const m = await getJSON("api/meta");
  state.meta = m;
  const sel = $("host");
  const cur = state.host;
  sel.innerHTML = `<option value="">${esc(t("all_domains"))}</option>` + m.hosts.map((h) => `<option>${esc(h)}</option>`).join("");
  if (cur && !m.hosts.includes(cur)) sel.insertAdjacentHTML("beforeend", `<option>${esc(cur)}</option>`);
  sel.value = cur;
  updateClear();
  renderFoot(m);
}

async function load() {
  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", Number(b.dataset.days) === state.days));
  try {
    const q = `days=${state.days}&host=${encodeURIComponent(state.host)}&country=${encodeURIComponent(state.country)}`;
    const d = await getJSON("api/summary?" + q);
    state.data = d;
    render(d);
  } catch (e) {
    $("foot").innerHTML = `<span class="err">${esc(t("load_failed", { error: e.message }))}</span>`;
  }
}

document.querySelectorAll("#range button").forEach((b) => b.addEventListener("click", () => {
  state.days = Number(b.dataset.days);
  store.set("days", String(state.days));
  load();
}));
document.querySelectorAll("#lang button").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
$("host").addEventListener("change", (e) => setHost(e.target.value));
$("clear-open").addEventListener("click", () => {
  setClearAsk();
  $("clear-box").hidden = false;
  $("clear-msg").hidden = true;
  updateClear();
});
$("clear-no").addEventListener("click", () => { $("clear-box").hidden = true; updateClear(); });
$("clear-yes").addEventListener("click", clearHost);
$("country-clear").addEventListener("click", () => setCountry(state.country));
document.querySelectorAll("#nav a, a[data-link]").forEach((a) => a.addEventListener("click", (e) => {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;  // let new tabs through
  e.preventDefault();
  go(a.dataset.page);
}));
window.addEventListener("popstate", () => {
  const before = state.country;
  readRoute();
  showPage();
  if (state.country !== before) load();
  else if (state.data) render(state.data);
});
let rt;
window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => state.data && renderChart(state.data), 120); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => state.data && render(state.data));

applyLang();
readRoute();
showPage();
getJSON("world.json").then((w) => { state.world = w; if (state.data) renderMap(state.data); }).catch(() => {});
loadMeta().catch(() => {}).finally(load);
setInterval(() => { loadMeta().catch(() => {}); load(); }, 60000);
