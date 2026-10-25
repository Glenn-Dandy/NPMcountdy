#!/usr/bin/env python3
"""Countdy: liest die Access-Logs des Nginx Proxy Manager mit und zeigt,
wie viele Daten an welche Domain, welchen Bereich und welche Besucher gingen.

Nur Standardbibliothek, damit das Image ohne pip auskommt.
"""

import gzip
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

LOG_DIR = os.environ.get("LOG_DIR", "/logs")
DB_PATH = os.environ.get("DB_PATH", "/data/countdy.db")
PORT = int(os.environ.get("PORT", "7881"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))
DETAIL_DAYS = int(os.environ.get("DETAIL_DAYS", "90"))
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

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
        CREATE TABLE IF NOT EXISTS files (
            base TEXT PRIMARY KEY, inode INTEGER, offset INTEGER, head TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.commit()
    con.close()


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
        "net": net_of(client), "client": client, "bytes": int(m["length"]),
    }


class Batch:
    def __init__(self):
        self.hourly, self.paths, self.clients, self.lines = {}, {}, {}, 0

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
        "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25", dargs)]
    clients = [{"name": r[0], "net": r[1], "bytes": r[2], "requests": r[3], "hosts": r[4]} for r in one(
        f"SELECT client, net, SUM(bytes), SUM(requests), COUNT(DISTINCT host) FROM daily_clients WHERE {dwhere} "
        "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25", dargs)]
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
    return {"hosts": hosts, "first_day": first, "detail_days": DETAIL_DAYS, **s}


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
            if u.path == "/api/summary":
                return self.send(200, json.dumps(summary(parse_qs(u.query))).encode(), "application/json")
            if u.path == "/api/meta":
                return self.send(200, json.dumps(meta()).encode(), "application/json")
        except Exception as e:
            return self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        name = "index.html" if u.path in ("/", "") else os.path.basename(u.path)
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return self.send(404, b"Nicht gefunden", "text/plain; charset=utf-8")
        ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}.get(
            name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            self.send(200, f.read(), ctype + "; charset=utf-8")


def main():
    db_init()
    threading.Thread(target=ingest_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
