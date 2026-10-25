#!/usr/bin/env python3
"""NPMcountdy: liest die Access-Logs des Nginx Proxy Manager mit und zeigt,
wie viele Daten an welche Domain, welchen Bereich und welche Besucher gingen.

Nur Standardbibliothek, damit das Image ohne pip auskommt.
"""

import gzip
import json
import os
import re
import sqlite3
import struct
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

LOG_DIR = os.environ.get("LOG_DIR", "/logs")
DB_PATH = os.environ.get("DB_PATH", "/data/npmcountdy.db")
PORT = int(os.environ.get("PORT", "7881"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))
DETAIL_DAYS = int(os.environ.get("DETAIL_DAYS", "90"))
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGES = {"", "verteilung", "laender", "besucher", "dateien"}  # Unterseiten, alle liefern index.html
GEOIP = os.environ.get("GEOIP", "on").lower() not in ("off", "0", "false", "no")
GEOIP_PATH = os.environ.get("GEOIP_PATH", os.path.join(os.path.dirname(DB_PATH), "dbip-country-lite.mmdb"))
GEOIP_URL = os.environ.get("GEOIP_URL", "")  # leer: DB-IP Country Lite des aktuellen Monats

# https://www.cloudflare.com/ips/ : steht so eine Adresse im Log, ist die echte Besucher-IP unbekannt
CLOUDFLARE = [ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32")]

# [25/Oct/2026:01:53:31 +0000] - 200 200 - GET https host "/pfad" [Client 1.2.3.4] [Length 123] [Gzip -] [Sent-to x] "UA" "Referer"
LINE_RE = re.compile(
    r'^\[(?P<time>[^\]]+)\] (?P<codes>.*?) - (?P<method>[A-Z]+) (?P<scheme>\S+) (?P<host>\S+) '
    r'"(?P<uri>.*)" \[Client (?P<client>[^\]]+)\] \[Length (?P<length>\d+)\]'
    r'.*?"(?P<ua>.*)" "(?P<ref>.*)"$'
)

KIND_RULES = [
    ("BoatSpeedy-App", re.compile(r"^BoatSpeedy", re.I)),
    ("Nextcloud-Client", re.compile(r"Nextcloud-android|Nextcloud-iOS|mirall|DAVx5|davdroid|gvfs|davfs|ownCloud", re.I)),
    ("Bot/Scanner", re.compile(r"bot|crawl|spider|scan|curl|wget|python|go-http|zgrab|masscan|nuclei|httpx|expanse|censys|palo ?alto|okhttp|java/|libwww|headless", re.I)),
    ("Browser", re.compile(r"Mozilla/.*(Chrome|Firefox|Safari|Edg)", re.I)),
]

state_lock = threading.Lock()
status = {"last_ingest": None, "last_lines": 0, "last_error": None}


def db_connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def db_init():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = db_connect()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS hourly (
            ts INTEGER, day TEXT, hod INTEGER, host TEXT, area TEXT, kind TEXT,
            status TEXT, net TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (ts, host, area, kind, status, net));
        CREATE INDEX IF NOT EXISTS hourly_day ON hourly (day);
        CREATE TABLE IF NOT EXISTS daily_paths (
            day TEXT, host TEXT, path TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, path));
        CREATE TABLE IF NOT EXISTS daily_clients (
            day TEXT, host TEXT, client TEXT, net TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, client));
        CREATE TABLE IF NOT EXISTS daily_countries (
            day TEXT, host TEXT, country TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, country));
        CREATE TABLE IF NOT EXISTS files (
            base TEXT PRIMARY KEY, inode INTEGER, offset INTEGER, head TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.commit()
    con.close()


# ---------- Länder zu IP-Adressen (DB-IP Lite, CC BY 4.0) ----------

class MMDB:
    """Minimaler Leser für MaxMind-DB-Dateien, reicht für die Länderdatenbank von DB-IP."""

    def __init__(self, buf):
        self.buf = buf
        marker = buf.rfind(b"\xab\xcd\xefMaxMind.com")
        if marker < 0:
            raise ValueError("keine MMDB-Datei")
        self.data = 0
        meta = self.decode(marker + 14)[0]
        self.nodes, self.rsize = meta["node_count"], meta["record_size"]
        self.data = self.nodes * self.rsize // 4 + 16
        self.v6 = meta["ip_version"] == 6
        self.v4start = 0
        if self.v6:  # IPv4 liegt im IPv6-Baum hinter 96 Null-Bits
            for _ in range(96):
                if self.v4start >= self.nodes:
                    break
                self.v4start = self.record(self.v4start, 0)

    def record(self, node, bit):
        b = self.buf
        if self.rsize == 24:
            o = node * 6 + bit * 3
            return int.from_bytes(b[o:o + 3], "big")
        if self.rsize == 28:
            o = node * 7
            if bit == 0:
                return ((b[o + 3] & 0xF0) << 20) | int.from_bytes(b[o:o + 3], "big")
            return ((b[o + 3] & 0x0F) << 24) | int.from_bytes(b[o + 4:o + 7], "big")
        o = node * 8 + bit * 4
        return int.from_bytes(b[o:o + 4], "big")

    def decode(self, off):
        b = self.buf
        ctrl = b[off]; off += 1
        typ, size = ctrl >> 5, ctrl & 31
        if typ == 1:  # Zeiger in den Datenbereich
            ss, v = (ctrl >> 3) & 3, ctrl & 7
            n = ss + 1
            p = int.from_bytes(b[off:off + n], "big")
            p = p if ss == 3 else (v << (8 * n)) | p
            p += (0, 2048, 526336, 0)[ss]
            return self.decode(self.data + p)[0], off + n
        if typ == 0:
            typ = 7 + b[off]; off += 1
        if size >= 29:
            n = size - 28
            size = (29, 285, 65821)[n - 1] + int.from_bytes(b[off:off + n], "big")
            off += n
        if typ == 2:
            return b[off:off + size].decode("utf-8", "replace"), off + size
        if typ == 7:
            out = {}
            for _ in range(size):
                k, off = self.decode(off)
                out[k], off = self.decode(off)
            return out, off
        if typ == 11:
            out = []
            for _ in range(size):
                v, off = self.decode(off)
                out.append(v)
            return out, off
        if typ == 3:
            return struct.unpack(">d", b[off:off + 8])[0], off + 8
        if typ == 15:
            return struct.unpack(">f", b[off:off + 4])[0], off + 4
        if typ == 14:
            return bool(size), off
        v = int.from_bytes(b[off:off + size], "big")
        if typ == 8 and size == 4 and v >= 1 << 31:
            v -= 1 << 32
        return (bytes(b[off:off + size]) if typ == 4 else v), off + size

    def lookup(self, ip):
        if ip.version == 6 and not self.v6:
            return None
        packed = ip.packed
        node = self.v4start if ip.version == 4 else 0
        for i in range(len(packed) * 8):
            if node >= self.nodes:
                break
            node = self.record(node, (packed[i >> 3] >> (7 - (i & 7))) & 1)
        if node <= self.nodes:
            return None
        return self.decode(self.data + node - self.nodes - 16)[0]


geo = {"db": None, "mtime": None, "tried": 0, "error": None}
country_cache = {}


def geo_download():
    now = datetime.now(TZ)
    prev = now.replace(day=1) - timedelta(days=1)
    urls = [GEOIP_URL] if GEOIP_URL else [
        f"https://download.db-ip.com/free/dbip-country-lite-{m:%Y-%m}.mmdb.gz" for m in (now, prev)]
    err = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NPMcountdy"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            buf = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
            MMDB(buf)  # erst prüfen, dann die alte Datei ersetzen
            with open(GEOIP_PATH + ".tmp", "wb") as f:
                f.write(buf)
            os.replace(GEOIP_PATH + ".tmp", GEOIP_PATH)
            return
        except Exception as e:
            err = f"{url}: {type(e).__name__}: {e}"
    raise RuntimeError(err)


def geo_refresh():
    """Lädt die Länderdatenbank, wenn sie fehlt oder älter als 32 Tage ist (DB-IP erneuert sie monatlich)."""
    if not GEOIP:
        return
    try:
        mtime = os.path.getmtime(GEOIP_PATH)
    except OSError:
        mtime = None
    if (mtime is None or time.time() - mtime > 32 * 86400) and time.time() - geo["tried"] > 3600:
        geo["tried"] = time.time()
        try:
            geo_download()
            geo["error"] = None
            mtime = os.path.getmtime(GEOIP_PATH)
        except Exception as e:  # mit der alten Datei weitermachen, in einer Stunde neu versuchen
            geo["error"] = str(e)
    if mtime is not None and mtime != geo["mtime"]:
        with open(GEOIP_PATH, "rb") as f:
            db = MMDB(f.read())
        with state_lock:
            geo.update(db=db, mtime=mtime)
            country_cache.clear()


def country_of(client):
    """Ländercode, LAN, CLOUDFLARE (echte IP unbekannt) oder ?? (unbekannt)."""
    cc = country_cache.get(client)
    if cc:
        return cc
    try:
        ip = ip_address(client)
    except ValueError:
        return "??"
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_private or ip.is_loopback:
        cc = "LAN"
    elif any(ip in n for n in CLOUDFLARE):
        cc = "CLOUDFLARE"
    else:
        db = geo["db"]
        if db is None:
            return "??"
        rec = db.lookup(ip) or {}
        cc = (rec.get("country") or {}).get("iso_code") or rec.get("country_code") or "??"
    if len(country_cache) > 200000:
        country_cache.clear()
    country_cache[client] = cc
    return cc


# ---------- Auswertung einer Logzeile ----------

def classify(ua):
    for name, rule in KIND_RULES:
        if rule.search(ua):
            return name
    return "Sonstige"


def net_of(client):
    try:
        ip = ip_address(client.strip())
    except ValueError:
        return "Internet"
    return "LAN" if (ip.is_private or ip.is_loopback) else "Internet"


def split_uri(uri):
    path = uri.split("?", 1)[0] or "/"
    parts = [p for p in path.split("/") if p]
    if not parts:
        area = "/"
    elif len(parts) == 1 and not path.endswith("/"):
        area = "/" + parts[0]
    else:
        area = "/" + parts[0] + "/"
    short = "/" + "/".join(parts[:3])
    if len(parts) > 3:
        short += "/…"
    return area[:60], short[:120]


def parse_line(line):
    m = LINE_RE.match(line)
    if not m:
        return None
    try:
        t = datetime.strptime(m["time"], "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    codes = [c for c in m["codes"].split() if c.isdigit() and len(c) == 3]
    code = codes[-1] if codes else "000"
    area, short = split_uri(m["uri"])
    ts = int(t.timestamp()) // 3600 * 3600
    local = datetime.fromtimestamp(ts, TZ)
    client = m["client"].strip()
    return {
        "ts": ts, "day": local.strftime("%Y-%m-%d"), "hod": local.hour,
        "host": m["host"].lower(), "area": area, "path": short,
        "kind": classify(m["ua"]), "status": code[0] + "xx",
        "net": net_of(client), "client": client, "cc": country_of(client), "bytes": int(m["length"]),
    }


class Batch:
    def __init__(self):
        self.hourly, self.paths, self.clients, self.countries, self.lines = {}, {}, {}, {}, 0

    def add(self, line):
        r = parse_line(line)
        if not r:
            return
        self.lines += 1
        k = (r["ts"], r["day"], r["hod"], r["host"], r["area"], r["kind"], r["status"], r["net"])
        v = self.hourly.setdefault(k, [0, 0]); v[0] += r["bytes"]; v[1] += 1
        k = (r["day"], r["host"], r["path"])
        v = self.paths.setdefault(k, [0, 0]); v[0] += r["bytes"]; v[1] += 1
        k = (r["day"], r["host"], r["client"], r["net"])
        v = self.clients.setdefault(k, [0, 0]); v[0] += r["bytes"]; v[1] += 1
        k = (r["day"], r["host"], r["cc"])
        v = self.countries.setdefault(k, [0, 0]); v[0] += r["bytes"]; v[1] += 1

    def flush(self, con):
        con.executemany(
            "INSERT INTO hourly VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
            "bytes = bytes + excluded.bytes, requests = requests + excluded.requests",
            [k + tuple(v) for k, v in self.hourly.items()])
        con.executemany(
            "INSERT INTO daily_paths VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET "
            "bytes = bytes + excluded.bytes, requests = requests + excluded.requests",
            [k + tuple(v) for k, v in self.paths.items()])
        con.executemany(
            "INSERT INTO daily_clients VALUES (?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
            "bytes = bytes + excluded.bytes, requests = requests + excluded.requests",
            [k + tuple(v) for k, v in self.clients.items()])
        con.executemany(
            "INSERT INTO daily_countries VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET "
            "bytes = bytes + excluded.bytes, requests = requests + excluded.requests",
            [k + tuple(v) for k, v in self.countries.items()])


# ---------- Logdateien lesen, auch über Rotationen hinweg ----------

def open_any(path):
    return gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")


def first_line(path):
    try:
        with open_any(path) as f:
            return f.readline(300).decode("utf-8", "replace")
    except OSError:
        return ""


def read_from(path, offset, batch):
    """Liest ab Byte offset bis zur letzten vollständigen Zeile, gibt das neue Offset zurück."""
    with open_any(path) as f:
        if offset:
            if path.endswith(".gz"):
                f.read(offset)
            else:
                f.seek(offset)
        pos = offset
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            pos += len(raw)
            batch.add(raw.decode("utf-8", "replace").rstrip("\n"))
        return pos


def rotated_of(base):
    """Rotierte Dateien zu base, älteste zuerst: base.3.gz, base.2.gz, base.1 …"""
    def num(p):
        m = re.match(re.escape(base) + r"\.(\d+)(\.gz)?$", p)
        return int(m.group(1)) if m else None
    found = [(num(p), p) for p in glob(base + ".*")]
    return [p for n, p in sorted((n, p) for n, p in found if n is not None)][::-1]


def ingest():
    con = db_connect()
    batch = Batch()
    first_run = con.execute("SELECT value FROM meta WHERE key='backfilled'").fetchone() is None
    known = {r[0]: r[1:] for r in con.execute("SELECT base, inode, offset, head FROM files")}
    for base in sorted(glob(os.path.join(LOG_DIR, "proxy-host-*_access.log"))):
        try:
            st = os.stat(base)
        except OSError:
            continue
        head = first_line(base)
        prev = known.get(base)
        if prev is None:
            if first_run:
                for old in rotated_of(base):
                    read_from(old, 0, batch)
            offset = 0
        else:
            inode, offset, prev_head = prev
            if inode != st.st_ino or (prev_head and head != prev_head) or st.st_size < offset:
                # Datei wurde rotiert: Rest der alten Datei in der rotierten Kopie nachlesen
                for old in rotated_of(base):
                    if prev_head and first_line(old) == prev_head:
                        read_from(old, offset, batch)
                        break
                offset = 0
        new_offset = read_from(base, offset, batch)
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (base, st.st_ino, new_offset, head))
    batch.flush(con)
    if first_run:
        con.execute("INSERT OR REPLACE INTO meta VALUES ('backfilled', ?)", (str(int(time.time())),))
    cutoff = (datetime.now(TZ) - timedelta(days=DETAIL_DAYS)).strftime("%Y-%m-%d")
    con.execute("DELETE FROM daily_paths WHERE day < ?", (cutoff,))
    con.execute("DELETE FROM daily_clients WHERE day < ?", (cutoff,))
    con.commit()
    con.close()
    return batch.lines


def ingest_loop():
    while True:
        try:
            geo_refresh()
        except Exception as e:
            geo["error"] = f"{type(e).__name__}: {e}"
        try:
            n = ingest()
            with state_lock:
                status.update(last_ingest=int(time.time()), last_lines=n, last_error=None)
        except Exception as e:  # weiterlaufen, Fehler in der Oberfläche anzeigen
            with state_lock:
                status.update(last_error=f"{type(e).__name__}: {e}")
        time.sleep(INTERVAL)


# ---------- Abfragen für die Oberfläche ----------

def summary(q):
    days = max(1, min(int(q.get("days", ["7"])[0]), 3650))
    host = q.get("host", [""])[0]
    country = q.get("country", [""])[0].upper()[:10]
    now = datetime.now(TZ)
    start_day = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    hourly_view = days <= 2
    start_ts = int((now - timedelta(hours=47 if days == 2 else 23)).timestamp()) // 3600 * 3600

    where, args = ("ts >= ?", [start_ts]) if hourly_view else ("day >= ?", [start_day])
    dwhere, dargs = "day >= ?", [start_day if not hourly_view else datetime.fromtimestamp(start_ts, TZ).strftime("%Y-%m-%d")]
    if host:
        where += " AND host = ?"; args.append(host)
        dwhere += " AND host = ?"; dargs.append(host)
    group = "area" if host else "host"

    con = db_connect()
    one = lambda sql, a: con.execute(sql, a).fetchall()

    total = one(f"SELECT COALESCE(SUM(bytes),0), COALESCE(SUM(requests),0) FROM hourly WHERE {where}", args)[0]
    inet = one(f"SELECT COALESCE(SUM(bytes),0) FROM hourly WHERE {where} AND net='Internet'", args)[0][0]
    bucket = "ts" if hourly_view else "day"
    series_rows = one(f"SELECT {bucket}, {group}, SUM(bytes) FROM hourly WHERE {where} GROUP BY 1, 2", args)

    if hourly_view:
        buckets = list(range(start_ts, int(now.timestamp()) // 3600 * 3600 + 1, 3600))
        labels = [datetime.fromtimestamp(b, TZ).strftime("%H:00") for b in buckets]
    else:
        buckets = [(now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
        labels = buckets
    idx = {b: i for i, b in enumerate(buckets)}
    series = {}
    for b, g, v in series_rows:
        if b in idx:
            series.setdefault(g, [0] * len(buckets))[idx[b]] += v

    def breakdown(col, limit=50):
        return [{"name": r[0], "bytes": r[1], "requests": r[2]} for r in one(
            f"SELECT {col}, SUM(bytes), SUM(requests) FROM hourly WHERE {where} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}", args)]

    hod = [0] * 24
    for h, v in one(f"SELECT hod, SUM(bytes) FROM hourly WHERE {where} GROUP BY 1", args):
        hod[h] = v

    paths = [{"host": r[0], "name": r[1], "bytes": r[2], "requests": r[3]} for r in one(
        f"SELECT host, path, SUM(bytes), SUM(requests) FROM daily_paths WHERE {dwhere} "
        "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50", dargs)]
    client_sql = (f"SELECT client, net, SUM(bytes), SUM(requests), COUNT(DISTINCT host) FROM daily_clients "
                  f"WHERE {dwhere} GROUP BY 1, 2 ORDER BY 3 DESC")
    client_row = lambda r: {"name": r[0], "net": r[1], "cc": country_of(r[0]), "bytes": r[2], "requests": r[3], "hosts": r[4]}
    clients = [client_row(r) for r in one(client_sql + " LIMIT 50", dargs)]
    visitors = one(f"SELECT COUNT(DISTINCT client) FROM daily_clients WHERE {dwhere}", dargs)[0][0]
    result = {
        "days": days, "host": host, "hourly": hourly_view, "group": group,
        "total": {"bytes": total[0], "requests": total[1], "internet": inet, "visitors": visitors},
        "labels": labels,
        "series": [{"name": g, "values": v} for g, v in sorted(series.items(), key=lambda x: -sum(x[1]))],
        "hosts": breakdown("host"), "kinds": breakdown("kind"),
        "areas": [{"name": r[1], "sub": r[0], "bytes": r[2], "requests": r[3]} for r in one(
            f"SELECT host, area, SUM(bytes), SUM(requests) FROM hourly WHERE {where} "
            "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25", args)],
        "statuses": breakdown("status"), "nets": breakdown("net"),
        "hod": hod, "paths": paths, "clients": clients,
        "countries": [{"name": r[0], "bytes": r[1], "requests": r[2]} for r in one(
            f"SELECT country, SUM(bytes), SUM(requests) FROM daily_countries WHERE {dwhere} "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 300", dargs)],
    }
    if country:
        cargs = dargs + [country]
        first_day = dargs[0]
        day_list = []
        d = datetime.strptime(first_day, "%Y-%m-%d").date()
        while d <= now.date():
            day_list.append(d.isoformat()); d += timedelta(days=1)
        per_day = dict(one(f"SELECT day, SUM(bytes) FROM daily_countries WHERE {dwhere} AND country = ? GROUP BY 1", cargs))
        matching = [c for c in map(client_row, one(client_sql, dargs)) if c["cc"] == country]
        result["country"] = {
            "code": country,
            "hosts": [{"name": r[0], "bytes": r[1], "requests": r[2]} for r in one(
                f"SELECT host, SUM(bytes), SUM(requests) FROM daily_countries WHERE {dwhere} AND country = ? "
                "GROUP BY 1 ORDER BY 2 DESC", cargs)],
            "days": [{"day": x, "bytes": per_day.get(x, 0)} for x in day_list],
            "clients": matching[:50], "visitors": len(matching),
        }
    con.close()
    return result


def meta():
    con = db_connect()
    hosts = [r[0] for r in con.execute("SELECT host FROM hourly GROUP BY host ORDER BY SUM(bytes) DESC")]
    first = con.execute("SELECT MIN(day) FROM hourly").fetchone()[0]
    con.close()
    with state_lock:
        s = dict(status)
    geo_info = {"geo": GEOIP, "geo_ready": geo["db"] is not None, "geo_error": geo["error"],
                "geo_date": datetime.fromtimestamp(geo["mtime"], TZ).strftime("%Y-%m-%d") if geo["mtime"] else None}
    return {"hosts": hosts, "first_day": first, "detail_days": DETAIL_DAYS, **s, **geo_info}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path.endswith("/api/summary"):
                return self.send(200, json.dumps(summary(parse_qs(u.query))).encode(), "application/json")
            if u.path.endswith("/api/meta"):
                return self.send(200, json.dumps(meta()).encode(), "application/json")
        except Exception as e:
            return self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        name = "index.html" if u.path.strip("/") in PAGES else os.path.basename(u.path)
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return self.send(404, b"Nicht gefunden", "text/plain; charset=utf-8")
        ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css", "svg": "image/svg+xml",
                 "json": "application/json"}.get(
            name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            self.send(200, f.read(), ctype + "; charset=utf-8")


def main():
    db_init()
    threading.Thread(target=ingest_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
