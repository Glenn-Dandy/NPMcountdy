"use strict";

const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const STATUS_NAMES = {
  "2xx": "2xx erfolgreich",
  "3xx": "3xx Weiterleitung oder unverändert",
  "4xx": "4xx abgelehnt oder nicht gefunden",
  "5xx": "5xx Serverfehler",
};
const SPECIAL_CC = { LAN: "Heimnetz (LAN)", CLOUDFLARE: "Cloudflare, Herkunft unbekannt", "??": "Unbekannt" };
// Unterseite (letzter Teil der Adresse) zu Seite im HTML
const ROUTES = { "": "overview", verteilung: "traffic", laender: "geo", besucher: "visitors", dateien: "paths" };
const nf = new Intl.NumberFormat("de-DE");
const regionNames = (() => { try { return new Intl.DisplayNames(["de"], { type: "region" }); } catch { return null; } })();
const $ = (id) => document.getElementById(id);

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* egal */ } },
};
const state = {
  days: Number(store.get("days", "7")) || 7, host: store.get("host", ""),
  page: "overview", country: "", data: null, meta: null, world: null,
};

function color(i) {
  return getComputedStyle(document.documentElement).getPropertyValue(COLORS[Math.min(i, COLORS.length - 1)]).trim();
}

function fmtBytes(b) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  const digits = b >= 100 || i === 0 ? 0 : b >= 10 ? 1 : 2;
  return b.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " " + units[i];
}
// Flagge aus den Regional-Indicator-Zeichen, Windows zeigt dafür nur die zwei Buchstaben
const flag = (cc) => (/^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map((c) => 0x1f1a5 + c.charCodeAt(0))) : "");
function countryName(cc) {
  if (SPECIAL_CC[cc]) return SPECIAL_CC[cc];
  try { return regionNames?.of(cc) || cc; } catch { return cc; }
}
const countryLabel = (cc) => (flag(cc) ? flag(cc) + " " : "") + countryName(cc);
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0) + " %";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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

// ---------- Balkendiagramm über die Zeit ----------

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
  return `${Number(d)}.${Number(m)}.`;
}
const longDay = (day) => new Date(day + "T12:00").toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "long" });

function renderChart(d) {
  const el = $("chart");
  if (!el.clientWidth) return;  // Seite gerade nicht sichtbar
  const W = el.clientWidth, H = el.clientHeight || 260;
  const pad = { l: 58, r: 6, t: 8, b: 24 };
  const n = d.labels.length;
  let series = d.series.slice(0, 7);
  if (d.series.length > 7) {
    const rest = new Array(n).fill(0);
    d.series.slice(7).forEach((s) => s.values.forEach((v, i) => (rest[i] += v)));
    series.push({ name: "Weitere", values: rest });
  }
  const sums = d.labels.map((_, i) => series.reduce((a, s) => a + s.values[i], 0));
  const max = niceMax(Math.max(0, ...sums));
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const bw = iw / n, gap = Math.min(6, bw * 0.25);
  const y = (v) => pad.t + ih - (v / max) * ih;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Datenmenge im Zeitverlauf">`;
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
      const title = d.hourly ? d.labels[i] + " Uhr" : longDay(d.labels[i]);
      showTip(ev, `<b>${title}: ${fmtBytes(sums[i])}</b>` + (rows.length
        ? rows.map((r) => `<div class="row"><span><i style="background:${color(r[2])}"></i>${esc(r[0])}</span><span>${fmtBytes(r[1])}</span></div>`).join("")
        : "<div>kein Verkehr</div>"));
    });
    g.addEventListener("mouseleave", hideTip);
  });
}

// ---------- Weltkarte ----------

function renderMap(d) {
  const el = $("map");
  const w = state.world;
  if (!w) { el.innerHTML = '<div class="empty">Karte wird geladen …</div>'; return; }
  const by = Object.fromEntries(d.countries.map((c) => [c.name, c]));
  const vals = d.countries.filter((c) => w.shapes[c.name] && c.bytes > 0).map((c) => c.bytes);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  // logarithmisch, sonst ist neben dem größten Land alles fast weiß
  const level = (b) => (hi > lo ? 0.18 + 0.82 * Math.log(b / lo) / Math.log(hi / lo) : 1);
  let svg = `<svg viewBox="0 0 ${w.width} ${w.height}" role="img" aria-label="Weltkarte, Datenmenge nach Land">`;
  for (const [cc, path] of Object.entries(w.shapes)) {
    const c = by[cc];
    const style = c && c.bytes > 0 ? ` style="fill-opacity:${level(c.bytes).toFixed(2)}"` : "";
    svg += `<path d="${path}" data-cc="${cc}" class="${c && c.bytes > 0 ? "has" : ""}${cc === state.country ? " sel" : ""}"${style}/>`;
  }
  el.innerHTML = svg + "</svg>";
  // ausgewähltes Land nach oben, damit der Rand nicht verdeckt wird
  el.querySelector("path.sel")?.parentNode.appendChild(el.querySelector("path.sel"));

  const total = d.countries.reduce((a, c) => a + c.bytes, 0);
  el.querySelectorAll("path").forEach((p) => {
    p.addEventListener("mousemove", (ev) => {
      const c = by[p.dataset.cc];
      showTip(ev, `<b>${esc(countryLabel(p.dataset.cc))}</b>` + (c
        ? `<div class="row"><span>Daten</span><span>${fmtBytes(c.bytes)} · ${pct(c.bytes, total)}</span></div>`
          + `<div class="row"><span>Anfragen</span><span>${nf.format(c.requests)}</span></div>`
        : "<div>kein Verkehr</div>"));
    });
    p.addEventListener("mouseleave", hideTip);
    p.addEventListener("click", () => { hideTip(); setCountry(p.dataset.cc); });
  });
}

// ---------- Listen und Tabellen ----------

function renderBars(id, rows, opts = {}) {
  const el = $(id);
  if (!rows.length) { el.innerHTML = '<div class="empty">Keine Daten im Zeitraum</div>'; return; }
  const total = opts.total ?? rows.reduce((a, r) => a + r.bytes, 0);
  const top = rows[0].bytes || 1;
  el.innerHTML = rows.slice(0, opts.limit || 8).map((r, i) => {
    const name = opts.label ? opts.label(r.name) : opts.names?.[r.name] || r.name;
    const sel = opts.selected && opts.selected === r.name ? " sel" : "";
    return `<div class="bar-row${opts.click ? " click" : ""}${sel}" data-name="${esc(r.name)}" title="${nf.format(r.requests)} Anfragen">
      <span class="n">${esc(name)}${opts.sub && r.sub ? `<small>${esc(r.sub)}</small>` : ""}</span>
      <span class="v">${r.bytes ? fmtBytes(r.bytes) + " · " + pct(r.bytes, total) : nf.format(r.requests) + " Anfragen, keine Daten"}</span>
      <div class="track"><div class="fill" style="width:${(r.bytes / top) * 100}%;background:${color(opts.mono ? 0 : i)}"></div></div>
    </div>`;
  }).join("");
  if (opts.click) el.querySelectorAll(".bar-row").forEach((row) => row.addEventListener("click", () => opts.click(row.dataset.name)));
}

function renderHod(hod) {
  const max = Math.max(1, ...hod);
  $("hod").innerHTML = hod.map((v, h) => `<div style="height:${(v / max) * 100}%" title="${h}:00 bis ${h + 1}:00 Uhr: ${fmtBytes(v)}"></div>`).join("");
  let axis = $("hod").nextElementSibling;
  if (!axis || !axis.classList.contains("hod-axis")) {
    axis = document.createElement("div");
    axis.className = "hod-axis";
    $("hod").after(axis);
  }
  axis.innerHTML = "<span>0 Uhr</span><span>6</span><span>12</span><span>18</span><span>23</span>";
}

function renderTable(el, head, rows) {
  if (typeof el === "string") el = $(el);
  if (!rows.length) { el.innerHTML = '<tr><td class="empty">Keine Daten im Zeitraum</td></tr>'; return; }
  el.innerHTML = "<thead><tr>" + head.map((h, i) => `<th${i ? ' class="num"' : ""}>${h}</th>`).join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody>";
}

const clientRow = (c, withCountry = true) =>
  `<tr><td>${esc(c.name)}<span class="badge">${esc(c.net)}</span>${withCountry ? `<span class="host">${esc(countryLabel(c.cc))}</span>` : ""}</td>`
  + `<td class="num">${fmtBytes(c.bytes)}</td><td class="num">${nf.format(c.requests)}</td></tr>`;

function renderGeoNote(d) {
  const m = state.meta || {};
  const total = d.countries.reduce((a, c) => a + c.bytes, 0);
  const cf = d.countries.find((c) => c.name === "CLOUDFLARE");
  let note = "";
  if (m.geo === false) note = "Länderzuordnung ist ausgeschaltet (GEOIP=off).";
  else if (!m.geo_ready) note = m.geo_error ? "Länderdatenbank konnte nicht geladen werden: " + m.geo_error : "Länderdatenbank wird geladen …";
  else if (cf && total && cf.bytes / total > 0.2) note = `${pct(cf.bytes, total)} der Daten gingen an Cloudflare-Adressen. `
    + "Dort steht nicht die echte Besucher-IP im Log des NPM, Abhilfe siehe README.";
  $("geo-note").textContent = note;
  $("geo-note").hidden = !note;
}

function renderCountry(d) {
  const el = $("country-detail");
  const c = d.country;
  $("country-clear").hidden = !state.country;
  if (!state.country || !c) {
    $("country-title").textContent = "Land";
    el.innerHTML = '<div class="empty">Ein Land auf der Karte oder in der Liste anklicken.</div>';
    return;
  }
  $("country-title").textContent = countryLabel(c.code);
  const total = d.countries.reduce((a, x) => a + x.bytes, 0);
  const sum = c.hosts.reduce((a, h) => a + h.bytes, 0);
  const req = c.hosts.reduce((a, h) => a + h.requests, 0);
  const maxDay = Math.max(1, ...c.days.map((x) => x.bytes));
  el.innerHTML = `
    <div class="mini-kpis">
      <div><span class="label">Daten</span><b>${fmtBytes(sum)}</b><span class="hint">${pct(sum, total)} aller Länder</span></div>
      <div><span class="label">Anfragen</span><b>${nf.format(req)}</b></div>
      <div><span class="label">Besucher-IPs</span><b>${nf.format(c.visitors)}</b></div>
    </div>
    <p class="sub-head">Datenmenge pro Tag</p>
    <div class="days" style="grid-template-columns:repeat(${c.days.length},1fr)">${c.days.map((x) =>
      `<div style="height:${(x.bytes / maxDay) * 100}%" title="${longDay(x.day)}: ${fmtBytes(x.bytes)}"></div>`).join("")}</div>
    <p class="sub-head">Domains</p>
    <div class="bars" id="c-hosts"></div>
    <p class="sub-head">Größte Besucher aus diesem Land</p>
    <div class="table-wrap"><table id="c-clients"></table></div>`;
  renderBars("c-hosts", c.hosts, { mono: true, limit: 10 });
  renderTable("c-clients", ["Adresse", "Daten", "Anfragen"], c.clients.slice(0, 15).map((x) => clientRow(x, false)));
}

function render(d) {
  const days = d.hourly ? 1 : d.days;
  $("k-bytes").textContent = fmtBytes(d.total.bytes);
  $("k-bytes-day").textContent = d.hourly ? "in den letzten 24 Stunden" : "Ø " + fmtBytes(d.total.bytes / days) + " pro Tag";
  $("k-req").textContent = nf.format(d.total.requests);
  $("k-req-avg").textContent = d.total.requests ? "Ø " + fmtBytes(d.total.bytes / d.total.requests) + " pro Anfrage" : "";
  $("k-inet").textContent = fmtBytes(d.total.internet);
  $("k-inet-share").textContent = pct(d.total.internet, d.total.bytes) + " der Datenmenge";
  $("k-vis").textContent = nf.format(d.total.visitors);

  $("chart-title").textContent = (d.hourly ? "Datenmenge pro Stunde" : "Datenmenge pro Tag") + (d.host ? " nach Bereich" : " nach Domain");
  renderChart(d);

  if (d.host) {
    $("t-group").textContent = "Domain";
    renderBars("b-group", d.hosts, { mono: true });
  } else {
    $("t-group").textContent = "Domains";
    renderBars("b-group", d.hosts, { click: (h) => setHost(h) });
  }
  renderBars("b-kinds", d.kinds);
  renderBars("b-countries-top", d.countries, { limit: 6, mono: true, label: countryLabel, click: (cc) => go("laender", cc) });

  renderBars("b-areas", d.areas, { limit: 15, mono: true, sub: !d.host });
  renderBars("b-status", d.statuses, { names: STATUS_NAMES });
  renderBars("b-nets", d.nets);
  renderHod(d.hod);

  renderMap(d);
  renderBars("b-countries", d.countries, { limit: 300, mono: true, label: countryLabel, click: setCountry, selected: state.country });
  renderGeoNote(d);
  renderCountry(d);

  renderTable("t-clients", ["Adresse", "Daten", "Anfragen"], d.clients.map((c) => clientRow(c)));
  renderTable("t-paths", ["Pfad", "Daten", "Anfragen"], d.paths.map((p) =>
    `<tr><td class="path">${esc(p.name)}${d.host ? "" : `<span class="host">${esc(p.host)}</span>`}</td><td class="num">${fmtBytes(p.bytes)}</td><td class="num">${nf.format(p.requests)}</td></tr>`));
}

// ---------- Unterseiten ----------

function readRoute() {
  const seg = location.pathname.replace(/\/+$/, "").split("/").pop();
  state.page = ROUTES[seg] || "overview";
  state.country = state.page === "geo" ? (new URLSearchParams(location.search).get("land") || "").toUpperCase() : "";
}

function showPage() {
  document.querySelectorAll(".page").forEach((p) => (p.hidden = p.dataset.page !== state.page));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === state.page));
  const title = document.querySelector(`.page[data-page="${state.page}"]`).dataset.title;
  document.title = state.page === "overview" ? "NPMcountdy" : `${title} · NPMcountdy`;
  hideTip();
}

// Seite wechseln, ohne neu zu laden; das Land steht als ?land=DE in der Adresse
function go(seg, country = "") {
  const url = (seg || "./") + (country ? "?land=" + encodeURIComponent(country) : "");
  history.pushState(null, "", url);
  const before = state.country;
  readRoute();
  showPage();
  window.scrollTo(0, 0);
  if (state.country !== before) load();
  else if (state.data) render(state.data);
}

function setCountry(cc) {
  go("laender", cc === state.country ? "" : cc);
}

// ---------- Steuerung ----------

function setHost(h) {
  state.host = h;
  store.set("host", h);
  $("host").value = h;
  load();
}

async function loadMeta() {
  const m = await getJSON("api/meta");
  state.meta = m;
  const sel = $("host");
  const cur = state.host;
  sel.innerHTML = '<option value="">Alle Domains</option>' + m.hosts.map((h) => `<option>${esc(h)}</option>`).join("");
  if (cur && !m.hosts.includes(cur)) sel.insertAdjacentHTML("beforeend", `<option>${esc(cur)}</option>`);
  sel.value = cur;
  const parts = [];
  if (m.first_day) parts.push("Daten seit " + new Date(m.first_day + "T12:00").toLocaleDateString("de-DE"));
  if (m.last_ingest) parts.push("zuletzt eingelesen " + new Date(m.last_ingest * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) + " Uhr");
  parts.push(`Dateien und Besucher werden ${m.detail_days} Tage aufbewahrt`);
  parts.push("gezählt wird nur, was ausgeliefert wurde (Downloads)");
  if (m.geo_date) parts.push("Länderdatenbank vom " + new Date(m.geo_date + "T12:00").toLocaleDateString("de-DE"));
  $("foot").innerHTML = esc(parts.join(" · "))
    + (m.geo ? ' · <a href="https://db-ip.com" target="_blank" rel="noopener">IP Geolocation by DB-IP</a>' : "")
    + " · Karte: Natural Earth"
    + (m.last_error ? `<br><span class="err">Fehler beim Einlesen: ${esc(m.last_error)}</span>` : "");
}

async function load() {
  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", Number(b.dataset.days) === state.days));
  try {
    const q = `days=${state.days}&host=${encodeURIComponent(state.host)}&country=${encodeURIComponent(state.country)}`;
    const d = await getJSON("api/summary?" + q);
    state.data = d;
    render(d);
  } catch (e) {
    $("foot").innerHTML = `<span class="err">Daten konnten nicht geladen werden: ${esc(e.message)}</span>`;
  }
}

document.querySelectorAll("#range button").forEach((b) => b.addEventListener("click", () => {
  state.days = Number(b.dataset.days);
  store.set("days", String(state.days));
  load();
}));
$("host").addEventListener("change", (e) => setHost(e.target.value));
$("country-clear").addEventListener("click", () => setCountry(state.country));
document.querySelectorAll("#nav a, a[data-link]").forEach((a) => a.addEventListener("click", (e) => {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;  // neuer Tab soll weiter gehen
  e.preventDefault();
  go(a.getAttribute("href").replace("./", ""));
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

readRoute();
showPage();
getJSON("world.json").then((w) => { state.world = w; if (state.data) renderMap(state.data); }).catch(() => {});
loadMeta().catch(() => {}).finally(load);
setInterval(() => { loadMeta().catch(() => {}); load(); }, 60000);
