"use strict";

const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
// Page in the HTML to the last part of the address, per language. Both spellings always work.
const PATHS = {
  overview: { en: "", de: "" },
  traffic: { en: "traffic", de: "verteilung" },
  geo: { en: "countries", de: "laender" },
  visitors: { en: "visitors", de: "besucher" },
  paths: { en: "files", de: "dateien" },
  settings: { en: "settings", de: "einstellungen" },
  about: { en: "about", de: "ueber" },
};
const ROUTES = Object.fromEntries(Object.entries(PATHS).flatMap(([page, p]) => [[p.en, page], [p.de, page]]));
const BOT = "Bot/Scanner";
const PAGE_ROWS = 50;  // table rows shown at first and added per click
const $ = (id) => document.getElementById(id);

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* not important */ } },
};
// language of browser and system: German if it is set to German, otherwise English
const browserLang = () => ((navigator.languages || [navigator.language]).find((l) => /^(en|de)/i.test(l)) || "en").slice(0, 2).toLowerCase();
const langPref = I18N[store.get("lang", "")] ? store.get("lang", "") : "auto";  // "auto", "en" or "de"
const state = {
  days: Number(store.get("days", "7")) || 7, host: store.get("host", ""),
  bots: store.get("bots", "1") !== "0",  // bots are counted unless switched off
  group: store.get("group", "1") !== "0",  // domains of one NPM proxy host shown as one
  step: "",  // chart columns chosen by hand, empty: automatic
  langPref, lang: langPref === "auto" ? browserLang() : langPref,
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

function fmtUnits(v, units, base) {
  let i = 0;
  while (v >= base && i < units.length - 1) { v /= base; i++; }
  const digits = v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2;
  return v.toLocaleString(t("locale"), { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " " + units[i];
}
const fmtBytes = (b) => fmtUnits(b, ["B", "KB", "MB", "GB", "TB"], 1024);
const fmtRate = (bps) => fmtUnits(bps, ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"], 1000);
// Flag from regional indicator symbols, Windows only shows the two letters
const flag = (cc) => (/^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map((c) => 0x1f1a5 + c.charCodeAt(0))) : "");
function countryName(cc) {
  if (t("special_cc")[cc]) return t("special_cc")[cc];
  try { return regionNames?.of(cc) || cc; } catch { return cc; }
}
const countryLabel = (cc) => (flag(cc) ? flag(cc) + " " : "") + countryName(cc);
const kindName = (k) => t("kinds")[k] || k;
const pctText = (p) => p + (state.lang === "de" ? " %" : "%");
const pct = (a, b) => pctText(b ? Math.round((a / b) * 100) : 0);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (day, opts) => new Date(day + "T12:00").toLocaleDateString(t("locale"), opts);
const shortDate = (day) => fmtDate(day, { day: "numeric", month: "numeric" });
const fullDate = (day) => fmtDate(day, { day: "numeric", month: "numeric", year: "numeric" });
const fmtTime = (sec) => new Date(sec * 1000).toLocaleTimeString(t("locale"), { hour: "2-digit", minute: "2-digit" });
const fmtDay = (sec) => new Date(sec * 1000).toLocaleDateString(t("locale"), { day: "numeric", month: "numeric", year: "numeric" });
// provider of a visitor (row with org) or a provider row (name is the organisation, LAN or ??)
function providerName(p) {
  if (p.org) return p.org;
  if ("visitors" in p) return t("special_cc")[p.name] || p.name;
  return t("special_cc")[p.net === "LAN" || p.cc === "LAN" ? "LAN" : "??"];
}
const statusLabel = (code) => code + (t("codes")[code] ? " " + t("codes")[code] : "");

// ---------- Domains of one proxy host ----------
// With state.group the server reports each proxy host under its main domain; members lists all its domains.

const siteOf = (host) => (state.group && state.meta?.sites.find((s) => s.members.includes(host))?.name) || host;
const membersOf = (name) => (state.group && state.meta?.sites.find((s) => s.name === name)?.members) || [name];
const otherMembers = (name) => membersOf(name).filter((h) => h !== name).join(", ");
const withMembers = (name) => (otherMembers(name) ? t("with_members", { host: name, others: otherMembers(name) }) : name);
const filterParams = () => `host=${encodeURIComponent(state.host)}&bots=${state.bots ? 1 : 0}&group=${state.group ? 1 : 0}`;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + ": " + r.status);
  return r.json();
}

// ---------- Tooltips, for mouse and touch ----------
// Elements carry data-tip (plain text) or data-tipfn (name of a function in tipFns that returns HTML).

const tipFns = {};

function showTip(ev, html) {
  const tip = $("tip");
  tip.innerHTML = html;
  tip.hidden = false;
  const x = Math.max(8, Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8));
  tip.style.left = x + "px";
  tip.style.top = Math.max(8, ev.clientY - tip.offsetHeight - 12) + "px";
}
const hideTip = () => ($("tip").hidden = true);

function tipAt(ev) {
  const el = ev.target.closest?.("[data-tip], [data-tipfn]");
  if (!el) return null;
  if (el.dataset.tip) return esc(el.dataset.tip);
  return tipFns[el.dataset.tipfn]?.(el) || null;
}
document.addEventListener("pointermove", (ev) => {
  if (ev.pointerType !== "mouse") return;
  const html = tipAt(ev);
  if (html) showTip(ev, html);
  else if (!$("tip").hidden) hideTip();
});
// touch and pen: a tap shows the tip, a tap elsewhere hides it
document.addEventListener("pointerdown", (ev) => {
  if (ev.pointerType === "mouse") return;
  const html = tipAt(ev);
  if (html) showTip(ev, html); else hideTip();
});
window.addEventListener("scroll", hideTip, { passive: true });

// ---------- Language ----------

// Static texts in the HTML carry data-i18n (text), data-i18n-aria (aria-label), data-i18n-tip (tooltip) or a page key in data-page
function applyLang() {
  nf = new Intl.NumberFormat(t("locale"));
  try { regionNames = new Intl.DisplayNames([state.lang], { type: "region" }); } catch { regionNames = null; }
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
  document.querySelectorAll("[data-i18n-tip]").forEach((el) => (el.dataset.tip = t(el.dataset.i18nTip)));
  document.querySelectorAll(".nav a, a[data-link]").forEach((a) => a.setAttribute("href", PATHS[a.dataset.page][state.lang] || "./"));
  document.querySelectorAll("#lang button").forEach((b) => {
    const on = b.dataset.lang === state.langPref;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on);
  });
  $("lang-auto").textContent = t("lang_auto", { l: I18N[browserLang()] === I18N.de ? "Deutsch" : "English" });
  const opt = $("host").querySelector('option[value=""]');
  if (opt) opt.textContent = t("all_domains");
  if (!$("clear-box").hidden) setClearAsk();
}

function setLang(pref) {
  state.langPref = pref;
  state.lang = pref === "auto" ? browserLang() : pref;
  store.set("lang", pref);
  applyLang();
  // keep the address in the chosen language
  history.replaceState(null, "", pageUrl(state.page, state.country));
  showPage();
  if (state.meta) renderMeta(state.meta);
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

function dayLabel(l) {
  const [, m, d] = l.split("-");
  return state.lang === "de" ? `${Number(d)}.${Number(m)}.` : `${Number(d)}/${Number(m)}`;
}
const longDay = (day) => fmtDate(day, { weekday: "short", day: "numeric", month: "long" });

// label under a chart column and title of its tooltip, for hours, days, weeks and months
function columnLabel(d, i) {
  if (d.hourly) return d.labels[i];
  if (d.step === "week") return t("week_short", { n: d.weeks[i] });
  if (d.step === "month") return fmtDate(d.labels[i] + "-15", { month: "short", year: "2-digit" });
  return dayLabel(d.labels[i]);
}
function columnTitle(d, i) {
  if (d.hourly) return t("hour_title", { h: d.labels[i] });
  const [from, to] = d.ranges[i];
  if (d.step === "week") return t("week_title", { n: d.weeks[i], from: shortDate(from), to: fullDate(to) });
  if (d.step === "month") {
    const name = fmtDate(d.labels[i] + "-15", { month: "long", year: "numeric" });
    return from.endsWith("-01") ? name : name + " " + t("month_from", { d: shortDate(from) });
  }
  return longDay(d.labels[i]);
}

// automatic chart columns: weeks for a year, otherwise days; weeks and months can be picked from 90 days on
const chartStep = () => (state.days < 90 ? "day" : state.step || (state.days >= 365 ? "week" : "day"));

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
    svg += `<g class="col" data-i="${i}" data-tipfn="chart">`;
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
      svg += `<text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(columnLabel(d, i))}</text>`;
    }
  }
  svg += "</svg>";
  el.innerHTML = svg;

  $("legend").innerHTML = series.map((s, i) => `<span><i style="background:${color(i)}"></i>${esc(s.name)}</span>`).join("");

  tipFns.chart = (g) => {
    const i = Number(g.dataset.i);
    const rows = series.map((s, si) => [s.name, s.values[i], si]).filter((r) => r[1] > 0).sort((a, b) => b[1] - a[1]);
    return `<b>${esc(columnTitle(d, i))}: ${fmtBytes(sums[i])}</b>` + (rows.length
      ? rows.map((r) => `<div class="row"><span><i style="background:${color(r[2])}"></i>${esc(r[0])}</span><span>${fmtBytes(r[1])}</span></div>`).join("")
      : `<div>${t("no_traffic")}</div>`);
  };
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
    svg += `<path d="${path}" data-cc="${cc}" data-tipfn="map" class="${c && c.bytes > 0 ? "has" : ""}${cc === state.country ? " sel" : ""}"${style}/>`;
  }
  el.innerHTML = svg + "</svg>";
  // selected country on top so its outline is not covered
  el.querySelector("path.sel")?.parentNode.appendChild(el.querySelector("path.sel"));

  const total = d.countries.reduce((a, c) => a + c.bytes, 0);
  tipFns.map = (p) => {
    const c = by[p.dataset.cc];
    return `<b>${esc(countryLabel(p.dataset.cc))}</b>` + (c
      ? `<div class="row"><span>${t("data")}</span><span>${fmtBytes(c.bytes)} · ${pct(c.bytes, total)}</span></div>`
        + `<div class="row"><span>${t("requests")}</span><span>${nf.format(c.requests)}</span></div>`
      : `<div>${t("no_traffic")}</div>`);
  };
  el.querySelectorAll("path").forEach((p) => p.addEventListener("click", () => { hideTip(); setCountry(p.dataset.cc); }));
}

// ---------- Lists ----------

function barsHtml(rows, opts = {}) {
  if (!rows.length) return `<div class="empty">${t("empty")}</div>`;
  const total = opts.total ?? rows.reduce((a, r) => a + r.bytes, 0);
  const top = rows[0].bytes || 1;
  return rows.slice(0, opts.limit || 8).map((r, i) => {
    const name = opts.label ? opts.label(r) : r.name;
    const sel = opts.selected && opts.selected === r.name ? " sel" : "";
    const extra = opts.extra ? " · " + opts.extra(r) : "";
    return `<div class="bar-row${opts.click ? " click" : ""}${sel}" data-name="${esc(r.name)}" title="${t("n_requests", { n: nf.format(r.requests) })}">
      <span class="n">${esc(name)}${opts.sub && r.sub ? `<small>${esc(r.sub)}</small>` : ""}</span>
      <span class="v">${r.bytes ? fmtBytes(r.bytes) + " · " + pct(r.bytes, total) : t("n_requests_no_data", { n: nf.format(r.requests) })}${esc(extra)}</span>
      <div class="track"><div class="fill" style="width:${(r.bytes / top) * 100}%;background:${color(opts.mono ? 0 : i)}"></div></div>
    </div>`;
  }).join("");
}

// the other domains of a proxy host, shown small next to its name
const memberSub = (r) => ({ ...r, sub: otherMembers(r.name) });

function renderBars(id, rows, opts = {}) {
  const el = $(id);
  el.innerHTML = barsHtml(rows, opts);
  if (opts.click) el.querySelectorAll(".bar-row").forEach((row) => row.addEventListener("click", () => opts.click(row.dataset.name)));
}

function daysHtml(days) {
  const max = Math.max(1, ...days.map((x) => x.bytes));
  return `<div class="days" style="grid-template-columns:repeat(${days.length},1fr)">${days.map((x) =>
    `<div style="height:${(x.bytes / max) * 100}%" data-tip="${esc(longDay(x.day) + ": " + fmtBytes(x.bytes))}"></div>`).join("")}</div>`;
}

function renderHod(hod) {
  const max = Math.max(1, ...hod);
  $("hod").innerHTML = hod.map((v, h) => `<div style="height:${(v / max) * 100}%" data-tip="${esc(t("hod_tip", { h, h2: h + 1, v: fmtBytes(v) }))}"></div>`).join("");
  let axis = $("hod").nextElementSibling;
  if (!axis || !axis.classList.contains("hod-axis")) {
    axis = document.createElement("div");
    axis.className = "hod-axis";
    $("hod").after(axis);
  }
  axis.innerHTML = t("hod_axis").map((l) => `<span>${l}</span>`).join("");
}

// ---------- Tables: sorting, search, more rows, details per row ----------
// cols: { key, label, num, value(row) for sorting, html(row) }; opts: { key(row), search(row), placeholder, sort, expand(key) }

const tables = {};

function dataTable(id, cols, rows, opts) {
  let s = tables[id];
  if (!s) {
    const box = $(id);
    box.innerHTML = (opts.search ? `<input type="search" class="search">` : "")
      + `<div class="table-wrap"><table></table></div><div class="tbl-foot"><span class="count"></span><button class="link more-rows" hidden></button></div>`;
    s = tables[id] = { sort: opts.sort, dir: -1, q: "", n: PAGE_ROWS, open: new Map() };
    box.querySelector(".search")?.addEventListener("input", (e) => { s.q = e.target.value; s.n = PAGE_ROWS; drawTable(id); });
    box.querySelector(".more-rows").addEventListener("click", () => { s.n += PAGE_ROWS; drawTable(id); });
    box.querySelector("table").addEventListener("click", (e) => {
      const th = e.target.closest("th[data-key]");
      if (th) {
        const col = s.cols.find((c) => c.key === th.dataset.key);
        if (s.sort === col.key) s.dir = -s.dir; else { s.sort = col.key; s.dir = col.num ? -1 : 1; }
        return drawTable(id);
      }
      const tr = e.target.closest("tr.row");
      if (tr && s.opts.expand && !e.target.closest("a")) toggleRow(id, tr.dataset.key);
    });
    box.querySelector("table").addEventListener("keydown", (e) => {
      const tr = e.target.closest("tr.row");
      if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleRow(id, tr.dataset.key); }
    });
  }
  Object.assign(s, { cols, rows, opts });
  drawTable(id);
}

function setTableSearch(id, q) {
  const s = tables[id];
  if (!s) return;
  s.q = q; s.n = PAGE_ROWS;
  const input = $(id).querySelector(".search");
  if (input) input.value = q;
  drawTable(id);
}

function drawTable(id) {
  const s = tables[id], box = $(id), { cols, opts } = s;
  const input = box.querySelector(".search");
  if (input) input.placeholder = t(opts.placeholder);
  const q = s.q.trim().toLowerCase();
  const rows = q ? s.rows.filter((r) => opts.search(r).toLowerCase().includes(q)) : s.rows.slice();
  const col = cols.find((c) => c.key === s.sort);
  if (col) {
    rows.sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), t("locale"), { numeric: true })) * s.dir;
    });
  }
  const table = box.querySelector("table");
  const head = "<thead><tr>" + cols.map((c) => {
    const on = c.key === s.sort;
    const sortAttr = on ? ` aria-sort="${s.dir > 0 ? "ascending" : "descending"}"` : "";
    return `<th class="sortable${c.num ? " num" : ""}${c.wide ? " wide-only" : ""}${on ? " on" : ""}" data-key="${c.key}"${sortAttr}>${esc(t(c.label))}<span class="arrow">${on ? (s.dir > 0 ? "▲" : "▼") : ""}</span></th>`;
  }).join("") + "</tr></thead>";
  if (!rows.length) {
    table.innerHTML = head + `<tbody><tr><td class="empty" colspan="${cols.length}">${t(s.rows.length ? "no_match" : "empty")}</td></tr></tbody>`;
  } else {
    table.innerHTML = head + "<tbody>" + rows.slice(0, s.n).map((r) => {
      const key = opts.key(r), open = s.open.has(key);
      const attrs = opts.expand ? ` class="row click${open ? " open" : ""}" tabindex="0" aria-expanded="${open}"` : "";
      let h = `<tr data-key="${esc(key)}"${attrs}>` + cols.map((c) => `<td class="${cellClass(c)}">${c.html(r)}</td>`).join("") + "</tr>";
      if (open) h += `<tr class="detail"><td colspan="${cols.length}">${s.open.get(key) || `<span class="empty">${t("loading_short")}</span>`}</td></tr>`;
      return h;
    }).join("") + "</tbody>";
  }
  box.querySelector(".count").textContent = rows.length > PAGE_ROWS || q ? t("shown", { n: nf.format(Math.min(s.n, rows.length)), total: nf.format(rows.length) }) : "";
  const more = box.querySelector(".more-rows");
  more.hidden = rows.length <= s.n;
  more.textContent = t("show_more", { n: nf.format(Math.min(PAGE_ROWS, rows.length - s.n)) });
}

// wide: column only shown on wider screens, its content is in the details too
const cellClass = (c) => [c.num && "num", c.cls, c.wide && "wide-only"].filter(Boolean).join(" ");

async function toggleRow(id, key) {
  const s = tables[id];
  if (s.open.has(key)) { s.open.delete(key); drawTable(id); return; }
  s.open.set(key, "");
  drawTable(id);
  let html;
  try { html = await s.opts.expand(key); } catch (e) { html = `<span class="err">${esc(e.message)}</span>`; }
  if (s.open.has(key)) { s.open.set(key, html); drawTable(id); }
}

// details already loaded belong to the old time range or domain
const closeDetails = () => Object.values(tables).forEach((s) => s.open.clear());

const sizeCols = [
  { key: "bytes", label: "col_data", num: true, value: (r) => r.bytes, html: (r) => fmtBytes(r.bytes) },
  { key: "requests", label: "col_requests", num: true, value: (r) => r.requests, html: (r) => nf.format(r.requests) },
];
const pathCell = (r, d) => `${esc(r.name)}${d.host ? "" : `<span class="host">${esc(r.host)}</span>`}`;
const kindsText = (r) => r.kinds.map(kindName).join(", ");

function visitorCols() {
  const cols = [
    { key: "name", label: "col_address", value: (r) => r.name, cls: "addr",
      html: (r) => `${esc(r.name)}<span class="badge">${esc(r.net)}</span>${r.kinds.length ? `<span class="host">${esc(kindsText(r))}</span>` : ""}` },
    { key: "cc", label: "col_country", wide: true, value: (r) => countryName(r.cc), html: (r) => esc(countryLabel(r.cc)) },
  ];
  if (state.meta?.asn_ready) cols.push({ key: "org", label: "col_provider", wide: true, value: (r) => providerName(r), html: (r) => esc(providerName(r)) });
  return cols.concat(sizeCols);
}

async function visitorDetail(ip) {
  const q = `ip=${encodeURIComponent(ip)}&days=${state.days}&${filterParams()}`;
  const c = await getJSON("api/client?" + q);
  const prov = c.asn ? `${providerName(c)} (AS${c.asn})` : providerName(c);
  const facts = [
    [t("d_country"), countryLabel(c.cc)],
    [t("d_provider"), prov],
    [t("d_kinds"), c.kinds.map((k) => kindName(k.name)).join(", ") || t("unknown")],
    [t("d_active"), c.last ? t("d_active_days", { n: c.active, m: c.days.length, d: shortDate(c.last) }) : t("unknown")],
  ];
  return `<div class="visitor">
    <dl class="facts compact">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    <div><p class="sub-head">${t("d_domains")}</p><div class="bars">${barsHtml(c.hosts.map(memberSub), { mono: true, limit: 10, sub: true })}</div></div>
    <div><p class="sub-head">${t("d_days")}</p>${daysHtml(c.days)}</div>
  </div>`;
}

// ---------- Pages ----------

const clientRow = (c) =>
  `<tr><td>${esc(c.name)}<span class="badge">${esc(c.net)}</span>${c.org ? `<span class="host">${esc(c.org)}</span>` : ""}</td>`
  + `<td class="num">${fmtBytes(c.bytes)}</td><td class="num">${nf.format(c.requests)}</td></tr>`;

function renderSimpleTable(id, head, rows) {
  const el = $(id);
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">${t("empty")}</td></tr>`; return; }
  el.innerHTML = "<thead><tr>" + head.map((h, i) => `<th${i ? ' class="num"' : ""}>${t(h)}</th>`).join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody>";
}

function dbNote(on, ready, error, prefix) {
  if (on === false) return t(prefix + "_off");
  if (!ready) return error ? t(prefix + "_error", { error }) : t(prefix + "_loading");
  return "";
}

function renderGeoNote(d) {
  const m = state.meta || {};
  const total = d.countries.reduce((a, c) => a + c.bytes, 0);
  const cf = d.countries.find((c) => c.name === "CLOUDFLARE");
  let note = dbNote(m.geo, m.geo_ready, m.geo_error, "geo");
  if (!note && cf && total && cf.bytes / total > 0.2) note = t("geo_cloudflare", { p: pct(cf.bytes, total) });
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
  el.innerHTML = `
    <div class="mini-kpis">
      <div><span class="label">${t("data")}</span><b>${fmtBytes(sum)}</b><span class="hint">${t("of_all_countries", { p: pct(sum, total) })}</span></div>
      <div><span class="label">${t("requests")}</span><b>${nf.format(req)}</b></div>
      <div><span class="label">${t("kpi_visitors")}</span><b>${nf.format(c.visitors)}</b></div>
    </div>
    <p class="sub-head">${t("country_days")}</p>
    ${daysHtml(c.days)}
    <p class="sub-head">${t("domains")}</p>
    <div class="bars">${barsHtml(c.hosts.map(memberSub), { mono: true, limit: 10, sub: true })}</div>
    <p class="sub-head">${t("country_clients")}</p>
    <div class="table-wrap"><table id="c-clients"></table></div>`;
  renderSimpleTable("c-clients", ["col_address", "col_data", "col_requests"], c.clients.slice(0, 15).map(clientRow));
}

// change against the previous period of the same length, below a key figure
function setDelta(id, cur, prev, fmt) {
  const el = $(id);
  el.className = "delta";
  if (prev == null) { el.textContent = ""; el.removeAttribute("data-tip"); return; }
  if (!prev) {
    el.textContent = cur ? t("prev_new") : "";
  } else {
    const p = Math.round((cur / prev - 1) * 100);
    el.textContent = (p > 0 ? "▲ +" : p < 0 ? "▼ " : "") + t("vs_prev", { p: pctText(p) });
    el.classList.add(p > 0 ? "up" : p < 0 ? "down" : "same");
  }
  el.dataset.tip = t("prev_value", { v: fmt(prev) });
}

function renderKpis(d) {
  const days = d.hourly ? 1 : d.days, tot = d.total, prev = d.prev;
  const bps = (p) => (p ? (p.bytes * 8) / 3600 : 0);
  $("k-bytes").textContent = fmtBytes(tot.bytes);
  $("k-bytes-day").textContent = d.hourly ? t("last_24h") : t("per_day", { v: fmtBytes(tot.bytes / days) });
  $("k-req").textContent = nf.format(tot.requests);
  $("k-req-avg").textContent = tot.requests ? t("per_request", { v: fmtBytes(tot.bytes / tot.requests) }) : "";
  $("k-inet").textContent = fmtBytes(tot.internet);
  $("k-inet-share").textContent = t("share_of_bytes", { p: pct(tot.internet, tot.bytes) });
  $("k-peak").textContent = fmtRate(bps(tot.peak));
  $("k-peak-when").textContent = tot.peak ? t("peak_when", {
    when: new Date(tot.peak.ts * 1000).toLocaleString(t("locale"), { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }),
  }) : t("peak_none");
  $("k-vis").textContent = nf.format(tot.visitors);
  setDelta("k-bytes-d", tot.bytes, prev?.bytes, fmtBytes);
  setDelta("k-req-d", tot.requests, prev?.requests, (v) => nf.format(v));
  setDelta("k-inet-d", tot.internet, prev?.internet, fmtBytes);
  setDelta("k-peak-d", bps(tot.peak), prev ? bps(prev.peak) : null, fmtRate);
  setDelta("k-vis-d", tot.visitors, prev?.visitors, (v) => nf.format(v));
}

function render(d) {
  renderKpis(d);
  $("chart-title").textContent = t(d.hourly ? "chart_hour" : "chart_" + d.step) + " " + t(d.host ? "by_area" : "by_domain");
  $("step").hidden = d.days < 90;
  document.querySelectorAll("#step button").forEach((b) => b.classList.toggle("on", b.dataset.step === d.step));
  renderChart(d);

  const hosts = d.hosts.map(memberSub);
  if (d.host) {
    $("t-group").textContent = t("domain");
    renderBars("b-group", hosts, { mono: true, sub: true });
  } else {
    $("t-group").textContent = t("domains");
    renderBars("b-group", hosts, { click: (h) => setHost(h), sub: true });
  }
  renderBars("b-kinds", d.kinds, { label: (r) => kindName(r.name) });
  renderBars("b-countries-top", d.countries, { limit: 6, mono: true, label: (r) => countryLabel(r.name), click: (cc) => go("geo", cc) });

  renderBars("b-areas", d.areas, { limit: 15, mono: true, sub: !d.host });
  renderBars("b-status", d.statuses, { label: (r) => t("status")[r.name] || r.name });
  renderBars("b-nets", d.nets);
  renderHod(d.hod);
  renderNotes(d);
  dataTable("t-errors", [
    { key: "name", label: "col_path", value: (r) => r.name, cls: "path", html: (r) => pathCell(r, d) },
    { key: "status", label: "col_status", value: (r) => r.status, html: (r) => `<span class="code c${esc(r.status[0])}">${esc(r.status)}</span> ${esc(t("codes")[r.status] || "")}` },
    sizeCols[1],
  ], d.errors, {
    key: (r) => r.host + r.name + r.status, sort: "requests", placeholder: "search_errors",
    search: (r) => `${r.name} ${r.host} ${statusLabel(r.status)}`,
  });

  renderMap(d);
  renderBars("b-countries", d.countries, { limit: 300, mono: true, label: (r) => countryLabel(r.name), click: setCountry, selected: state.country });
  renderGeoNote(d);
  renderCountry(d);

  const m = state.meta || {};
  const asnNote = dbNote(m.asn, m.asn_ready, m.asn_error, "asn");
  $("providers-note").textContent = asnNote || t("providers_note");
  const search = tables["t-clients"]?.q || "";
  renderBars("b-providers", d.providers, {
    limit: 12, mono: true, label: (r) => providerName(r), selected: d.providers.find((p) => providerName(p) === search)?.name,
    extra: (r) => (r.visitors === 1 ? t("n_ip") : t("n_ips", { n: nf.format(r.visitors) })),
    click: (name) => {
      const p = d.providers.find((x) => x.name === name);
      const label = providerName(p);
      setTableSearch("t-clients", tables["t-clients"]?.q === label ? "" : label);
      render(state.data);
    },
  });
  dataTable("t-clients", visitorCols(), d.clients, {
    key: (r) => r.name, sort: "bytes", placeholder: "search_visitors", expand: visitorDetail,
    search: (r) => `${r.name} ${countryName(r.cc)} ${r.cc} ${providerName(r)} ${r.asn ? "AS" + r.asn : ""} ${kindsText(r)}`,
  });
  dataTable("t-paths", [
    { key: "name", label: "col_path", value: (r) => r.name, cls: "path", html: (r) => pathCell(r, d) },
    ...sizeCols,
  ], d.paths, { key: (r) => r.host + r.name, sort: "bytes", placeholder: "search_paths", search: (r) => r.name + " " + r.host });
}

// notes about data that is only recorded since the update to 0.0.3
function renderNotes(d) {
  const since = state.meta?.detail_since;
  const old = since && d.start_day < since;
  $("bots-note").hidden = !(old && !state.bots);
  $("bots-note").textContent = t("bots_note", { d: since ? fullDate(since) : "" });
  $("errors-note").textContent = t("errors_note") + (old ? " " + t("errors_since", { d: fullDate(since) }) : "");
}

// GitHub issue with the version filled in, like in BoatSpeedy
function issueUrl(repo, prefix, label, version) {
  const body = `\n\n---\n**NPMcountdy:** ${version}\n`;
  return `${repo}/issues/new?labels=${encodeURIComponent(label)}&title=${encodeURIComponent(prefix)}&body=${encodeURIComponent(body)}`;
}

let updateChecking = false;

function renderUpdate(u) {
  const box = $("update-box");
  if (!u) { box.innerHTML = ""; return; }
  if (!u.on) { box.innerHTML = `<p class="muted">${esc(t("update_off"))}</p>`; return; }
  let html;
  if (updateChecking) html = `<p class="muted">${esc(t("checking"))}</p>`;
  else if (u.newer) {
    html = `<p class="update-new">${esc(t("update_available", { v: u.latest }))}</p>
      <a class="more" href="${esc(u.url)}" target="_blank" rel="noopener">${esc(t("update_open"))}</a>
      <p class="muted">${esc(t("update_how"))}</p>
      <pre class="cmd">cd ~/npmcountdy\ngit pull\nsudo docker compose up -d --build</pre>`;
  } else if (u.latest) html = `<p>${esc(t("up_to_date"))}</p>`;
  else html = u.error ? `<p class="err">${esc(t("update_failed", { e: u.error }))}</p>` : `<p class="muted">${esc(t("checking"))}</p>`;
  if (!updateChecking && u.latest && u.error) html += `<p class="err">${esc(t("update_failed", { e: u.error }))}</p>`;
  if (u.checked) html += `<p class="muted">${esc(t("update_checked", { d: fmtDay(u.checked), t: fmtTime(u.checked) }))}</p>`;
  html += `<button class="btn" id="update-check"${updateChecking ? " disabled" : ""}>${esc(t("check_update"))}</button>`
    + `<p class="note">${esc(t("update_note"))}</p>`;
  box.innerHTML = html;
  $("update-check").addEventListener("click", checkUpdate);
}

async function checkUpdate() {
  updateChecking = true;
  renderUpdate(state.meta?.update);
  try {
    const u = await getJSON("api/update?check=1");
    if (state.meta) state.meta.update = u;
  } catch (e) {
    if (state.meta?.update) state.meta.update.error = e.message;
  }
  updateChecking = false;
  if (state.meta) renderMeta(state.meta);
}

function renderAbout(m) {
  $("about-version").textContent = m.version ? t("version_label", { v: m.version }) : "";
  const repo = m.repo || "https://github.com/Glenn-Dandy/NPMcountdy";
  document.querySelectorAll(".repo-link").forEach((a) => (a.href = repo));
  $("report-bug").href = issueUrl(repo, "[Bug] ", "bug", m.version);
  $("request-feature").href = issueUrl(repo, "[Feature] ", "enhancement", m.version);
  renderUpdate(m.update);
  const db = (on, ready, error, date) => (on === false ? t("v_off") : ready ? t("v_from", { d: fullDate(date) })
    : error ? t("v_error", { e: error }) : t("v_loading"));
  const facts = [
    ["f_version", m.version],
    ["f_since", m.first_day ? fullDate(m.first_day) : t("unknown")],
    ["f_read", m.last_ingest ? t("v_read", { d: fmtDay(m.last_ingest), t: fmtTime(m.last_ingest) }) : t("unknown")],
    ["f_lines", nf.format(m.last_lines || 0)],
    ["f_interval", t("v_interval", { n: m.interval })],
    ["f_domains", nf.format(m.hosts.length)],
    ["f_db", fmtBytes(m.db_size || 0)],
    ["f_history", t("v_forever")],
    ["f_kept", t("v_days", { n: m.detail_days })],
    ["f_geo", db(m.geo, m.geo_ready, m.geo_error, m.geo_date)],
    ["f_asn", db(m.asn, m.asn_ready, m.asn_error, m.asn_date)],
    ["f_clear", m.clear ? t("v_on") : t("v_off")],
  ];
  if (m.last_error) facts.push(["f_error", m.last_error]);
  $("about-status").innerHTML = facts.map(([k, v]) => `<dt>${esc(t(k))}</dt><dd>${esc(v)}</dd>`).join("");
}

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
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === state.page));
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
  closeDetails();
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
  $("clear-ask").innerHTML = esc(before) + `<b>${esc(withMembers(state.host))}</b>` + esc(after);
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
      body: JSON.stringify({ host, group: state.group }),
    });
    const res = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(res.error || "HTTP " + r.status);
    $("clear-box").hidden = true;
    setHost("");
    showClearMsg(t("clear_done", { host: withMembers(host), bytes: fmtBytes(res.bytes), requests: nf.format(res.requests) }));
    loadMeta().catch(() => {});
  } catch (e) {
    showClearMsg(t("clear_failed", { error: e.message }), true);
  } finally {
    $("clear-yes").disabled = false;
  }
}

function renderFoot(m) {
  const parts = [];
  if (m.first_day) parts.push(esc(t("foot_since", { d: fmtDate(m.first_day) })));
  if (m.last_ingest) parts.push(esc(t("foot_read", { d: fmtDay(m.last_ingest), t: fmtTime(m.last_ingest) })));
  parts.push(esc(t("foot_kept", { n: m.detail_days })));
  parts.push(esc(t("foot_counted")));
  if (m.geo_date) parts.push(esc(t("foot_geo", { d: fmtDate(m.geo_date) })));
  if (m.geo || m.asn) parts.push('<a href="https://db-ip.com" target="_blank" rel="noopener">IP Geolocation by DB-IP</a>');
  parts.push(esc(t("foot_map")));
  const version = m.version ? `<a href="${PATHS.about[state.lang]}" data-page="about" data-link>NPMcountdy ${esc(m.version)}</a> · ` : "";
  const update = m.update?.newer
    ? `<a class="update-hint" href="${PATHS.about[state.lang]}" data-page="about" data-link>${esc(t("foot_update", { v: m.update.latest }))}</a><br>` : "";
  $("foot").innerHTML = update + version + parts.join(" · ")
    + (m.last_error ? `<br><span class="err">${esc(t("foot_error", { error: m.last_error }))}</span>` : "");
}

function renderMeta(m) {
  renderFoot(m);
  renderAbout(m);
  renderSites(m);
}

function renderHostSelect() {
  const m = state.meta;
  if (!m) return;
  const names = state.group ? m.sites.map((s) => s.name) : m.hosts;
  const sel = $("host");
  const cur = state.host;
  sel.innerHTML = `<option value="">${esc(t("all_domains"))}</option>` + names.map((h) => {
    const n = membersOf(h).length - 1;
    return `<option value="${esc(h)}" title="${esc(membersOf(h).join(", "))}">${esc(h)}${n ? ` (+${n})` : ""}</option>`;
  }).join("");
  if (cur && !names.includes(cur)) sel.insertAdjacentHTML("beforeend", `<option>${esc(cur)}</option>`);
  sel.value = cur;
}

// settings page: which proxy hosts have more than one domain
function renderSites(m) {
  const multi = m.sites.filter((s) => s.members.length > 1);
  $("site-list").innerHTML = multi.length
    ? multi.map((s) => `<li><b>${esc(s.name)}</b>: ${esc(s.members.join(", "))}</li>`).join("")
    : `<li class="empty">${esc(t("group_none"))}</li>`;
}

async function loadMeta() {
  const m = await getJSON("api/meta");
  state.meta = m;
  if (state.group && state.host && siteOf(state.host) !== state.host) {
    state.host = siteOf(state.host);  // a www. domain chosen before grouping was switched on
    store.set("host", state.host);
  }
  renderHostSelect();
  updateClear();
  renderMeta(m);
}

async function load() {
  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", Number(b.dataset.days) === state.days));
  $("bots").checked = state.bots;
  $("group").checked = state.group;
  try {
    const q = `days=${state.days}&country=${encodeURIComponent(state.country)}&${filterParams()}&step=${chartStep()}`;
    const d = await getJSON("api/summary?" + q);
    state.data = d;
    render(d);
  } catch (e) {
    $("foot").innerHTML = `<span class="err">${esc(t("load_failed", { error: e.message }))}</span>`;
  }
}

document.querySelectorAll("#range button").forEach((b) => b.addEventListener("click", () => {
  state.days = Number(b.dataset.days);
  state.step = "";
  store.set("days", String(state.days));
  closeDetails();
  load();
}));
document.querySelectorAll("#step button").forEach((b) => b.addEventListener("click", () => {
  state.step = b.dataset.step;
  load();
}));
$("bots").addEventListener("change", (e) => {
  state.bots = e.target.checked;
  store.set("bots", state.bots ? "1" : "0");
  closeDetails();
  load();
});
$("group").addEventListener("change", (e) => {
  state.group = e.target.checked;
  store.set("group", state.group ? "1" : "0");
  if (state.group && state.host) { state.host = siteOf(state.host); store.set("host", state.host); }
  renderHostSelect();
  updateClear();
  closeDetails();
  load();
});
document.querySelectorAll("#lang button").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
// system language changed while the page is open
window.addEventListener("languagechange", () => state.langPref === "auto" && setLang("auto"));
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
// links to pages, also those drawn later (footer)
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-page]");
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;  // let new tabs through
  e.preventDefault();
  go(a.dataset.page);
});
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
