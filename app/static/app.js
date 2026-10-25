"use strict";

const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const STATUS_NAMES = {
  "2xx": "2xx erfolgreich",
  "3xx": "3xx Weiterleitung oder unverändert",
  "4xx": "4xx abgelehnt oder nicht gefunden",
  "5xx": "5xx Serverfehler",
};
const nf = new Intl.NumberFormat("de-DE");
const $ = (id) => document.getElementById(id);

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* egal */ } },
};
const state = { days: Number(store.get("days", "7")) || 7, host: store.get("host", ""), data: null };

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
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0) + " %";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + ": " + r.status);
  return r.json();
}

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
  const [y, m, d] = l.split("-");
  return `${Number(d)}.${Number(m)}.`;
}

function renderChart(d) {
  const el = $("chart");
  const W = el.clientWidth || 800, H = el.clientHeight || 260;
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

  const tip = $("tip");
  el.querySelectorAll("g.col").forEach((g) => {
    g.addEventListener("mousemove", (ev) => {
      const i = Number(g.dataset.i);
      const rows = series.map((s, si) => [s.name, s.values[i], si]).filter((r) => r[1] > 0).sort((a, b) => b[1] - a[1]);
      const title = d.hourly ? d.labels[i] + " Uhr" : new Date(d.labels[i] + "T12:00").toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "long" });
      tip.innerHTML = `<b>${title}: ${fmtBytes(sums[i])}</b>` + (rows.length
        ? rows.map((r) => `<div class="row"><span><i style="background:${color(r[2])}"></i>${esc(r[0])}</span><span>${fmtBytes(r[1])}</span></div>`).join("")
        : "<div>kein Verkehr</div>");
      tip.hidden = false;
      const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
      tip.style.left = x + "px";
      tip.style.top = Math.max(8, ev.clientY - tip.offsetHeight - 12) + "px";
    });
    g.addEventListener("mouseleave", () => (tip.hidden = true));
  });
}

// ---------- Listen und Tabellen ----------

function renderBars(id, rows, opts = {}) {
  const el = $(id);
  if (!rows.length) { el.innerHTML = '<div class="empty">Keine Daten im Zeitraum</div>'; return; }
  const total = rows.reduce((a, r) => a + r.bytes, 0);
  const top = rows[0].bytes || 1;
  el.innerHTML = rows.slice(0, opts.limit || 8).map((r, i) => {
    const name = opts.names?.[r.name] || r.name;
    return `<div class="bar-row${opts.click ? " click" : ""}" data-name="${esc(r.name)}" title="${nf.format(r.requests)} Anfragen">
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

function renderTable(id, head, rows) {
  const el = $(id);
  if (!rows.length) { el.innerHTML = '<tr><td class="empty">Keine Daten im Zeitraum</td></tr>'; return; }
  el.innerHTML = "<thead><tr>" + head.map((h, i) => `<th${i ? ' class="num"' : ""}>${h}</th>`).join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody>";
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
  renderBars("b-areas", d.areas, { limit: 10, mono: true, sub: !d.host });
  renderBars("b-kinds", d.kinds);
  renderBars("b-status", d.statuses, { names: STATUS_NAMES });
  renderBars("b-nets", d.nets);
  renderHod(d.hod);

  renderTable("t-paths", ["Pfad", "Daten", "Anfragen"], d.paths.map((p) =>
    `<tr><td class="path">${esc(p.name)}${d.host ? "" : `<span class="host">${esc(p.host)}</span>`}</td><td class="num">${fmtBytes(p.bytes)}</td><td class="num">${nf.format(p.requests)}</td></tr>`));
  renderTable("t-clients", ["Adresse", "Daten", "Anfragen"], d.clients.map((c) =>
    `<tr><td>${esc(c.name)}<span class="badge">${esc(c.net)}</span></td><td class="num">${fmtBytes(c.bytes)}</td><td class="num">${nf.format(c.requests)}</td></tr>`));
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
  $("foot").innerHTML = esc(parts.join(" · ")) + (m.last_error ? `<br><span class="err">Fehler beim Einlesen: ${esc(m.last_error)}</span>` : "");
}

async function load() {
  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", Number(b.dataset.days) === state.days));
  try {
    const d = await getJSON(`api/summary?days=${state.days}&host=${encodeURIComponent(state.host)}`);
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
let rt;
window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => state.data && renderChart(state.data), 120); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => state.data && render(state.data));

loadMeta().catch(() => {}).finally(load);
setInterval(() => { loadMeta().catch(() => {}); load(); }, 60000);
