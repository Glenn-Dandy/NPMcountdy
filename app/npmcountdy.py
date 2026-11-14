#!/usr/bin/env python3
"""NPMcountdy: follows the access logs of Nginx Proxy Manager and shows
how much data went to which domain, area and visitor.

Standard library only, so the image needs no pip.
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
from datetime import date, datetime, timedelta
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

VERSION = "0.1.9"
REPO = "https://github.com/Glenn-Dandy/NPMcountdy"
LOG_DIR = os.environ.get("LOG_DIR", "/logs")
DB_PATH = os.environ.get("DB_PATH", "/data/npmcountdy.db")
PORT = int(os.environ.get("PORT", "7881"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))
DETAIL_DAYS = int(os.environ.get("DETAIL_DAYS", "90"))
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGES = {"", "traffic", "countries", "visitors", "files", "settings", "about",  # sub pages, all serve index.html
         "verteilung", "laender", "besucher", "dateien", "einstellungen", "ueber"}   # German addresses
LIMIT = 500  # rows per table (visitors, files, errors) sent to the UI


def switch(name, default="on"):
    return os.environ.get(name, default).lower() not in ("off", "0", "false", "no")


DATA_DIR = os.path.dirname(DB_PATH)
GEOIP = switch("GEOIP")
CLEAR = switch("CLEAR")  # allow deleting a domain's data in the UI
ASN = switch("ASN")      # provider (autonomous system) of visitor IPs
UPDATE_CHECK = switch("UPDATE_CHECK")  # ask GitHub for newer released versions
BOT = "Bot/Scanner"      # client type that the UI can leave out

# https://www.cloudflare.com/ips/ : if one of these is in the log, the real visitor IP is unknown
CLOUDFLARE = [ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32")]

# [25/Oct/2026:01:53:31 +0000] - 200 200 - GET https host "/path" [Client 1.2.3.4] [Length 123] [Gzip -] [Sent-to x] "UA" "Referer"
LINE_RE = re.compile(
    r'^\[(?P<time>[^\]]+)\] (?P<codes>.*?) - (?P<method>[A-Z]+) (?P<scheme>\S+) (?P<host>\S+) '
    r'"(?P<uri>.*)" \[Client (?P<client>[^\]]+)\] \[Length (?P<length>\d+)\]'
    r'(?: \[Gzip (?P<gzip>[^\]]*)\])?(?: \[Sent-to (?P<upstream>[^\]]*)\])?'
    r'.*?"(?P<ua>.*)" "(?P<ref>.*)"$'
)
# file endings that nginx can usually pack well; already packed formats are not listed
PACKABLE = {"html", "htm", "js", "mjs", "css", "json", "geojson", "xml", "svg", "txt", "csv", "map", "md",
            "wasm", "webmanifest", "ics", "vcf", "yaml", "yml"}

# Client types: own ones from the settings page (table client_types) come first, then these.
BASE_RULES = [
    ("Nextcloud-Client", re.compile(r"Nextcloud-android|Nextcloud-iOS|mirall|DAVx5|davdroid|gvfs|davfs|ownCloud", re.I)),
    (BOT, re.compile(r"bot|crawl|spider|scan|curl|wget|python|go-http|zgrab|masscan|nuclei|httpx|expanse|censys|palo ?alto|okhttp|java/|libwww|headless", re.I)),
    ("Browser", re.compile(r"Mozilla/.*(Chrome|Firefox|Safari|Edg)", re.I)),
]
KIND_RULES = list(BASE_RULES)
# Apps: the first product token of a user agent, "MyApp/1.2.3 (Android 14)" -> ("MyApp", "1.2.3")
APP_RE = re.compile(r"^([A-Za-z][\w.\-]{0,39})(?:/([\w.\-+]{1,30}))?")
NOT_APPS = {"mozilla", "opera"}  # browsers, they have their own client type


def app_of(ua, kind):
    """Also for bots: an app that a rule counts as bot (e.g. "okhttp") can be set up as own client type."""
    if kind == "Browser":
        return None
    m = APP_RE.match(ua.strip())
    if not m or m.group(1).lower() in NOT_APPS:
        return None
    return m.group(1), m.group(2) or ""


def app_pattern(app):
    return "^" + re.escape(app) + r"(?:/|\s|$)"


def load_client_types(con):
    global KIND_RULES
    rules = [(n, re.compile(p, re.I)) for n, p in con.execute("SELECT name, pattern FROM client_types ORDER BY pos")]
    KIND_RULES = rules + BASE_RULES  # replaced as a whole, classify() never sees a half list


state_lock = threading.Lock()
wake = threading.Event()  # set to read the logs before the interval is over
db_lock = threading.Lock()  # never ingest and delete at the same time, or deleted rows come back
status = {"last_ingest": None, "last_lines": 0, "last_error": None}

# Tables kept for DETAIL_DAYS. Up to version 0.0.2 they had no client type in them, see db_init().
DETAIL_TABLES = {
    "daily_paths": ("day TEXT, host TEXT, path TEXT, kind TEXT, bytes INTEGER, requests INTEGER, "
                    "PRIMARY KEY (day, host, path, kind)", "day, host, path, '', bytes, requests"),
    "daily_clients": ("day TEXT, host TEXT, client TEXT, net TEXT, kind TEXT, bytes INTEGER, requests INTEGER, "
                      "PRIMARY KEY (day, host, client, kind)", "day, host, client, net, '', bytes, requests"),
    "daily_countries": ("day TEXT, host TEXT, country TEXT, kind TEXT, bytes INTEGER, requests INTEGER, "
                        "PRIMARY KEY (day, host, country, kind)", "day, host, country, '', bytes, requests"),
}
PC_LIMIT = 100  # visitor IPs per expanded path, area or failed request


def db_connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.create_function("site", 1, site_of, deterministic=True)
    return con


# ---------- Domains of one NPM proxy host ----------
# NPM writes one log file per proxy host. Domains found in the same file (example.com and
# www.example.com) belong together; the UI can show them as one, under the shortest name.
# The stored figures keep the real domain, grouping only happens when querying.

SITES = {}    # domain -> main domain of its proxy host
MEMBERS = {}  # main domain -> all domains of the proxy host


def site_of(host):
    return SITES.get(host, host)


def main_domain(hosts):
    return sorted(hosts, key=lambda h: (h.startswith("www."), len(h), h))[0]


def load_sites(con):
    groups = {}
    for host, base in con.execute("SELECT host, base FROM file_hosts"):
        groups.setdefault(base, []).append(host)
    sites, members = {}, {}
    for hosts in groups.values():
        main = main_domain(hosts)
        members[main] = sorted(hosts, key=lambda h: (h != main, h))
        for h in hosts:
            sites[h] = main
    with state_lock:
        SITES.clear(); SITES.update(sites)
        MEMBERS.clear(); MEMBERS.update(members)


HOST_RE = re.compile(r' - [A-Z]+ \S+ (\S+) "')


def scan_hosts(base):
    """Domains in a log file and its rotated copies, for databases from before file_hosts existed."""
    found = set()
    for path in [base] + rotated_of(base):
        try:
            with open_any(path) as f:
                for raw in f:
                    m = HOST_RE.search(raw.decode("utf-8", "replace"))
                    if m:
                        found.add(m.group(1).lower())
        except OSError:
            pass
    return found


def db_init():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = db_connect()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS hourly (
            ts INTEGER, day TEXT, hod INTEGER, host TEXT, area TEXT, kind TEXT,
            status TEXT, net TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (ts, host, area, kind, status, net));
        CREATE INDEX IF NOT EXISTS hourly_day ON hourly (day);
        CREATE TABLE IF NOT EXISTS daily_errors (
            day TEXT, host TEXT, path TEXT, status TEXT, kind TEXT, requests INTEGER,
            PRIMARY KEY (day, host, path, status, kind));
        CREATE TABLE IF NOT EXISTS daily_path_clients (
            day TEXT, host TEXT, area TEXT, path TEXT, code TEXT, client TEXT, net TEXT, kind TEXT,
            bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, area, path, code, client, kind));
        CREATE TABLE IF NOT EXISTS daily_referers (
            day TEXT, host TEXT, referer TEXT, kind TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, referer, kind));
        CREATE TABLE IF NOT EXISTS daily_upstreams (
            day TEXT, host TEXT, upstream TEXT, kind TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, upstream, kind));
        CREATE TABLE IF NOT EXISTS daily_apps (
            day TEXT, host TEXT, app TEXT, version TEXT, kind TEXT, bytes INTEGER, requests INTEGER,
            PRIMARY KEY (day, host, app, version, kind));
        CREATE TABLE IF NOT EXISTS client_types (pos INTEGER, name TEXT PRIMARY KEY, pattern TEXT, app TEXT);
        CREATE TABLE IF NOT EXISTS daily_gzip (
            day TEXT, host TEXT, path TEXT, kind TEXT, bytes INTEGER, orig INTEGER,
            PRIMARY KEY (day, host, path, kind));
        CREATE TABLE IF NOT EXISTS files (
            base TEXT PRIMARY KEY, inode INTEGER, offset INTEGER, head TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS file_hosts (host TEXT PRIMARY KEY, base TEXT, seen INTEGER);
        """
    )
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    for table, (cols, copy) in DETAIL_TABLES.items():
        have = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if not have:
            con.execute(f"CREATE TABLE {table} ({cols})")
        elif "kind" not in have:
            # database of version 0.0.2 or older: add the client type, old rows get an empty one.
            # detail_since tells the UI from when on bots and errors are known in these tables.
            con.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            con.execute(f"CREATE TABLE {table} ({cols})")
            con.execute(f"INSERT INTO {table} SELECT {copy} FROM {table}_old")
            con.execute(f"DROP TABLE {table}_old")
            con.execute("INSERT OR IGNORE INTO meta VALUES ('detail_since', ?)", (today,))
    if con.execute("SELECT 1 FROM meta WHERE key = 'kinds_moved'").fetchone() is None:
        # up to 0.1.8 "BoatSpeedy-App" was built in; keep it as an own client type where it was seen
        if con.execute("SELECT 1 FROM hourly WHERE kind = 'BoatSpeedy-App' LIMIT 1").fetchone():
            con.execute("INSERT OR IGNORE INTO client_types VALUES (0, 'BoatSpeedy-App', ?, 'BoatSpeedy')",
                        (app_pattern("BoatSpeedy"),))
        con.execute("INSERT INTO meta VALUES ('kinds_moved', '1')")
    con.commit()
    load_sites(con)
    load_client_types(con)
    con.close()


# ---------- Countries and providers for IP addresses (DB-IP Lite, CC BY 4.0) ----------

class MMDB:
    """Minimal reader for MaxMind DB files, enough for the DB-IP country database."""

    def __init__(self, buf):
        self.buf = buf
        marker = buf.rfind(b"\xab\xcd\xefMaxMind.com")
        if marker < 0:
            raise ValueError("not an MMDB file")
        self.data = 0
        meta = self.decode(marker + 14)[0]
        self.nodes, self.rsize = meta["node_count"], meta["record_size"]
        self.data = self.nodes * self.rsize // 4 + 16
        self.v6 = meta["ip_version"] == 6
        self.v4start = 0
        if self.v6:  # IPv4 sits in the IPv6 tree behind 96 zero bits
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
        if typ == 1:  # pointer into the data section
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


# The same DB-IP data is also on the npm CDN, in case download.db-ip.com cannot be reached
GEO = {
    "country": {"on": GEOIP, "url": os.environ.get("GEOIP_URL", ""),
                "path": os.environ.get("GEOIP_PATH", os.path.join(DATA_DIR, "dbip-country-lite.mmdb")),
                "mirror": "https://cdn.jsdelivr.net/npm/@ip-location-db/dbip-country-mmdb/dbip-country.mmdb"},
    "asn": {"on": ASN, "url": os.environ.get("ASN_URL", ""),
            "path": os.environ.get("ASN_PATH", os.path.join(DATA_DIR, "dbip-asn-lite.mmdb")),
            "mirror": "https://cdn.jsdelivr.net/npm/@ip-location-db/dbip-asn-mmdb/dbip-asn.mmdb"},
}
for _g in GEO.values():
    _g.update(db=None, mtime=None, tried=0, error=None)
country_cache = {}
asn_cache = {}


# ---------- Settings: data/config.yaml, written by the settings page ----------
# Environment variables (docker-compose.yml) are only the defaults. The log folder is not in here:
# the container only sees the folder Docker mounts as /logs (docker-compose.yml).

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(DATA_DIR, "config.yaml"))


def mount_source(path):
    """The folder on the server that is mounted at path, from /proc/self/mountinfo (only shown in the UI).
    It is the path inside its file system, so it can miss a prefix if that folder is on a file system of its own."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                fields = line.split()
                if len(fields) > 4 and fields[4] == path:
                    return fields[3].replace("\\040", " ")
    except OSError:
        pass
    return path


LOG_SOURCE = os.environ.get("LOG_SOURCE") or mount_source(LOG_DIR)
DEFAULTS = {"timezone": os.environ.get("TZ", "Europe/Berlin"), "interval": INTERVAL, "detail_days": DETAIL_DAYS,
            "countries": GEOIP, "providers": ASN, "update_check": UPDATE_CHECK,
            "geoip_url": GEO["country"]["url"], "asn_url": GEO["asn"]["url"]}
settings = dict(DEFAULTS)


def check_setting(key, value):
    """The value in its proper type, ValueError if it is not allowed."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("on", "true", "yes", "1"):
            return True
        if str(value).lower() in ("off", "false", "no", "0"):
            return False
        raise ValueError(f"{key}: on or off")
    if isinstance(default, int):
        lo, hi = {"interval": (10, 3600), "detail_days": (1, 3650)}[key]
        v = int(value)
        if not lo <= v <= hi:
            raise ValueError(f"{key}: {lo} to {hi}")
        return v
    v = str(value).strip()
    if key == "timezone":
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"timezone: unknown time zone {v!r}")
    return v


def read_config():
    """Simple YAML: one 'key: value' per line, # starts a comment. Unknown or broken lines are skipped."""
    out = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        if key in DEFAULTS:
            try:
                out[key] = check_setting(key, value.strip("'\""))
            except ValueError as e:
                print(f"config.yaml: {e}")
    return out


def write_config(values):
    def fmt(v):
        return ("on" if v else "off") if isinstance(v, bool) else f'"{v}"' if v == "" else str(v)
    text = "# NPMcountdy settings, written by the settings page. They override docker-compose.yml.\n"
    text += "".join(f"{k}: {fmt(values[k])}\n" for k in DEFAULTS if k in values)
    with open(CONFIG_PATH + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)


def apply_settings(values):
    global TZ, INTERVAL, DETAIL_DAYS, UPDATE_CHECK
    turned_on = values["update_check"] and not UPDATE_CHECK
    with state_lock:
        settings.update(values)
        TZ, INTERVAL, DETAIL_DAYS = ZoneInfo(values["timezone"]), values["interval"], values["detail_days"]
        UPDATE_CHECK = values["update_check"]
        for name, key, url in (("country", "countries", "geoip_url"), ("asn", "providers", "asn_url")):
            g = GEO[name]
            if g["url"] != values[url]:
                g.update(url=values[url], tried=0)
            g["on"] = values[key]
            if not g["on"]:  # forget the database, it is loaded again when switched back on
                g.update(db=None, mtime=None, error=None)
                (country_cache if name == "country" else asn_cache).clear()
    if turned_on:
        threading.Thread(target=check_update, daemon=True).start()
    wake.set()  # read the logs (and load switched on databases) right away


def load_settings():
    apply_settings({**DEFAULTS, **read_config()})


def log_info():
    files = glob(os.path.join(LOG_DIR, "proxy-host-*_access.log"))
    return {"source": LOG_SOURCE, "files": len(files), "readable": sum(os.access(f, os.R_OK) for f in files)}


def settings_info():
    with state_lock:
        values = dict(settings)
    return {"values": values, "defaults": DEFAULTS, "log": log_info()}


def save_settings(body):
    values = dict(settings)
    for key, value in body.items():
        if key in DEFAULTS:
            values[key] = check_setting(key, value)
    write_config(values)
    apply_settings(values)
    return settings_info()


def geo_download(name):
    """Fetches one DB-IP database (empty URL setting: the file of the current or previous month, then the mirror)."""
    g = GEO[name]
    now = datetime.now(TZ)
    prev = now.replace(day=1) - timedelta(days=1)
    urls = [g["url"]] if g["url"] else [
        f"https://download.db-ip.com/free/dbip-{name}-lite-{m:%Y-%m}.mmdb.gz" for m in (now, prev)] + [g["mirror"]]
    errors = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NPMcountdy"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            buf = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
            MMDB(buf)  # validate first, then replace the old file
            with open(g["path"] + ".tmp", "wb") as f:
                f.write(buf)
            os.replace(g["path"] + ".tmp", g["path"])
            return
        except Exception as e:
            why = getattr(e, "reason", None) or e  # URLError wraps the actual cause
            errors.append(f"{url}: {type(e).__name__}: {why}")
    raise RuntimeError(" | ".join(errors))


def geo_refresh_one(name):
    """Downloads a database if it is missing or older than 32 days (DB-IP renews them monthly)."""
    g = GEO[name]
    try:
        mtime = os.path.getmtime(g["path"])
    except OSError:
        mtime = None
    if (mtime is None or time.time() - mtime > 32 * 86400) and time.time() - g["tried"] > 600:
        g["tried"] = time.time()
        try:
            geo_download(name)
            g["error"] = None
            mtime = os.path.getmtime(g["path"])
        except Exception as e:  # keep using the old file, retry in 10 minutes
            g["error"] = str(e)
    if mtime is not None and mtime != g["mtime"]:
        with open(g["path"], "rb") as f:
            db = MMDB(f.read())
        with state_lock:
            g.update(db=db, mtime=mtime)
            (country_cache if name == "country" else asn_cache).clear()
        if name == "country":
            rebuild_countries()


def geo_refresh():
    for name, g in GEO.items():
        if not g["on"]:
            continue
        try:
            geo_refresh_one(name)
        except Exception as e:
            g["error"] = f"{type(e).__name__}: {e}"


def rebuild_countries():
    """Reassigns countries stored as ?? while there was no database, as far as the IPs are still kept."""
    con = db_connect()
    first = con.execute("SELECT MIN(day) FROM daily_clients").fetchone()[0]
    if first and con.execute("SELECT 1 FROM daily_countries WHERE country = '??' AND day >= ? LIMIT 1", (first,)).fetchone():
        agg = {}
        for day, host, client, kind, b, r in con.execute("SELECT day, host, client, kind, bytes, requests FROM daily_clients"):
            v = agg.setdefault((day, host, country_of(client), kind), [0, 0]); v[0] += b; v[1] += r
        con.execute("DELETE FROM daily_countries WHERE day >= ?", (first,))
        con.executemany("INSERT INTO daily_countries VALUES (?,?,?,?,?,?)", [k + tuple(v) for k, v in agg.items()])
        con.commit()
    con.close()


def public_ip(client):
    """The address as ip_address, None if it is not one. IPv4 inside IPv6 is unwrapped."""
    try:
        ip = ip_address(client)
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def country_of(client):
    """Country code, LAN, CLOUDFLARE (real IP unknown) or ?? (unknown)."""
    cc = country_cache.get(client)
    if cc:
        return cc
    ip = public_ip(client)
    if ip is None:
        return "??"
    if ip.is_private or ip.is_loopback:
        cc = "LAN"
    elif any(ip in n for n in CLOUDFLARE):
        cc = "CLOUDFLARE"
    else:
        db = GEO["country"]["db"]
        if db is None:
            return "??"
        rec = db.lookup(ip) or {}
        cc = (rec.get("country") or {}).get("iso_code") or rec.get("country_code") or "??"
    if len(country_cache) > 200000:
        country_cache.clear()
    country_cache[client] = cc
    return cc


def asn_of(client):
    """(AS number, organisation) of the network the address belongs to, or None."""
    if client in asn_cache:
        return asn_cache[client]
    db = GEO["asn"]["db"]
    ip = public_ip(client)
    if db is None or ip is None:
        return None
    res = None
    if not (ip.is_private or ip.is_loopback):
        rec = db.lookup(ip) or {}
        num = rec.get("autonomous_system_number")
        if num:
            res = (num, rec.get("autonomous_system_organization") or f"AS{num}")
    if len(asn_cache) > 200000:
        asn_cache.clear()
    asn_cache[client] = res
    return res


# ---------- Parsing a log line ----------

def classify(ua):
    for name, rule in KIND_RULES:
        if rule.search(ua):
            return name
    return "Sonstige"  # "other"; kept in German because it is stored in existing databases, the UI translates it


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
    host = m["host"].lower()
    ref = m["ref"].strip()
    if ref in ("", "-"):
        ref = ""
    else:
        try:
            u = urlparse(ref)
            ref = u.hostname or "?"
            if u.scheme not in ("http", "https"):
                ref = f"{u.scheme}://{ref}"  # e.g. android-app://com.example
            ref = ref[:100]
        except ValueError:
            ref = "?"
        if ref == host:
            ref = "="  # same domain: a page or file loaded from the own site
    try:
        ratio = float(m["gzip"]) if m["gzip"] not in (None, "", "-") else 0.0
    except ValueError:
        ratio = 0.0
    upstream = (m["upstream"] or "").strip()
    return {
        "ts": ts, "day": local.strftime("%Y-%m-%d"), "hod": local.hour,
        "host": host, "area": area, "path": short, "ref": ref, "ratio": ratio, "ua": m["ua"][:200],
        "upstream": "" if upstream == "-" else upstream[:100],
        "kind": classify(m["ua"]), "code": code, "status": code[0] + "xx",
        "net": net_of(client), "client": client, "cc": country_of(client), "bytes": int(m["length"]),
    }


def bump(table, key, size):
    v = table.setdefault(key, [0, 0])
    v[0] += size
    v[1] += 1


def upsert(con, table, rows, cols="bytes, requests"):
    marks = ",".join("?" * len(rows[0])) if rows else ""
    sets = ", ".join(f"{c} = {c} + excluded.{c}" for c in cols.split(", "))
    con.executemany(f"INSERT INTO {table} VALUES ({marks}) ON CONFLICT DO UPDATE SET {sets}", rows)


class Batch:
    """Figures of the lines read in one run. only_pc: only visitor IPs per path (catching up old logs).
    Large runs write their rows in between, the commit at the end of ingest() still covers everything."""

    def __init__(self, con, only=None):
        self.con, self.only = con, only  # only: set of tables to fill (catching up), None: all
        self.hourly, self.paths, self.clients, self.countries, self.errors, self.pclients = {}, {}, {}, {}, {}, {}
        self.referers, self.upstreams, self.gzip, self.apps = {}, {}, {}, {}
        self.lines, self.days = 0, set()
        self.base, self.hosts = None, {}  # log file being read, domain -> its log file

    def add(self, line):
        r = parse_line(line)
        if not r:
            return
        self.lines += 1
        day, host, kind, size = r["day"], r["host"], r["kind"], r["bytes"]
        only = self.only
        if not only or "daily_path_clients" in only:
            bump(self.pclients, (day, host, r["area"], r["path"], r["code"], r["client"], r["net"], kind), size)
        if not only or "daily_referers" in only:
            bump(self.referers, (day, host, r["ref"], kind), size)
        if not only or "daily_upstreams" in only:
            bump(self.upstreams, (day, host, r["upstream"], kind), size)
        if not only or "daily_apps" in only:
            app = app_of(r["ua"], kind)
            if app:
                bump(self.apps, (day, host, app[0], app[1], kind), size)
        if r["ratio"] and (not only or "daily_gzip" in only):
            v = self.gzip.setdefault((day, host, r["path"], kind), [0, 0])
            v[0] += size
            v[1] += round(size * r["ratio"])
        if len(self.pclients) + len(self.clients) > 200000:
            self.write_rows()
        if only:
            self.days.add(day)
            return
        self.hosts[host] = self.base
        bump(self.hourly, (r["ts"], day, r["hod"], host, r["area"], kind, r["status"], r["net"]), size)
        bump(self.paths, (day, host, r["path"], kind), size)
        bump(self.clients, (day, host, r["client"], r["net"], kind), size)
        bump(self.countries, (day, host, r["cc"], kind), size)
        if r["code"][0] in "45":
            k = (day, host, r["path"], r["code"], kind)
            self.errors[k] = self.errors.get(k, 0) + 1

    def write_rows(self):
        con = self.con
        for table, rows, cols in (
                ("hourly", self.hourly, None), ("daily_paths", self.paths, None), ("daily_clients", self.clients, None),
                ("daily_countries", self.countries, None), ("daily_path_clients", self.pclients, None),
                ("daily_referers", self.referers, None), ("daily_upstreams", self.upstreams, None),
                ("daily_apps", self.apps, None),
                ("daily_gzip", self.gzip, "bytes, orig")):
            if rows:
                upsert(con, table, [k + tuple(v) for k, v in rows.items()], cols or "bytes, requests")
                rows.clear()
        if self.errors:
            upsert(con, "daily_errors", [k + (v,) for k, v in self.errors.items()], "requests")
            self.errors.clear()

    def flush(self, con):
        self.write_rows()
        now = int(time.time())
        con.executemany("INSERT OR REPLACE INTO file_hosts VALUES (?,?,?)",
                        [(h, b, now) for h, b in self.hosts.items() if b])


# ---------- Reading log files, across rotations ----------

def open_any(path):
    return gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")


def first_line(path):
    try:
        with open_any(path) as f:
            return f.readline(300).decode("utf-8", "replace")
    except OSError:
        return ""


def read_from(path, offset, batch, end=None):
    """Reads from byte offset up to the last complete line (or up to end), returns the new offset."""
    with open_any(path) as f:
        if offset:
            if path.endswith(".gz"):
                f.read(offset)
            else:
                f.seek(offset)
        pos = offset
        for raw in f:
            if not raw.endswith(b"\n") or (end is not None and pos + len(raw) > end):
                break
            pos += len(raw)
            batch.add(raw.decode("utf-8", "replace").rstrip("\n"))
        return pos


def rotated_of(base):
    """Rotated files of base, oldest first: base.3.gz, base.2.gz, base.1 …"""
    def num(p):
        m = re.match(re.escape(base) + r"\.(\d+)(\.gz)?$", p)
        return int(m.group(1)) if m else None
    found = [(num(p), p) for p in glob(base + ".*")]
    return [p for n, p in sorted((n, p) for n, p in found if n is not None)][::-1]


def backfill(con, bases, known, tables):
    """Tables that older versions did not fill yet: reads the NPM logs that are still there once more,
    but only up to where they were already counted, and only for these tables. Returns the first day found."""
    batch = Batch(con, only=set(tables))
    for base in bases:
        prev = known.get(base)
        try:
            st = os.stat(base)
        except OSError:
            st = None
        rotated = prev is None or st is None or st.st_ino != prev[0] or (prev[2] and first_line(base) != prev[2])
        for old in rotated_of(base):
            end = prev[1] if prev and rotated and prev[2] and first_line(old) == prev[2] else None
            read_from(old, 0, batch, end)
        if prev and not rotated:
            read_from(base, 0, batch, prev[1])
    batch.write_rows()
    return min(batch.days) if batch.days else datetime.now(TZ).strftime("%Y-%m-%d")


def ingest():
    con = db_connect()
    batch = Batch(con)
    first_run = con.execute("SELECT value FROM meta WHERE key='backfilled'").fetchone() is None
    known = {r[0]: r[1:] for r in con.execute("SELECT base, inode, offset, head FROM files")}
    bases = sorted(glob(os.path.join(LOG_DIR, "proxy-host-*_access.log")))
    # tables added later: (meta key, tables), the key holds the first day they are complete from ("" = always)
    for key, tables in (("pc_since", ["daily_path_clients"]),
                        ("v018_since", ["daily_referers", "daily_upstreams", "daily_gzip"]),
                        ("apps_since", ["daily_apps"])):
        if con.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is None:
            since = "" if first_run else backfill(con, bases, known, tables)
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, since))
    if not first_run and not con.execute("SELECT 1 FROM file_hosts LIMIT 1").fetchone():
        # database of version 0.1.2 or older: find out once which domains share a proxy host
        now = int(time.time())
        con.executemany("INSERT OR REPLACE INTO file_hosts VALUES (?,?,?)",
                        [(h, base, now) for base in bases for h in scan_hosts(base)])
    for base in bases:
        batch.base = base
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
                # file was rotated: read the rest of the old file from the rotated copy
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
    for table in ("daily_paths", "daily_clients", "daily_errors", "daily_path_clients", "daily_referers", "daily_gzip"):
        con.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff,))
    con.commit()
    load_sites(con)
    con.close()
    return batch.lines


def ingest_loop():
    while True:
        geo_refresh()
        try:
            with db_lock:
                n = ingest()
            with state_lock:
                status.update(last_ingest=int(time.time()), last_lines=n, last_error=None)
        except Exception as e:  # keep running, show the error in the UI
            with state_lock:
                status.update(last_error=f"{type(e).__name__}: {e}")
        wake.wait(INTERVAL)
        wake.clear()


# ---------- Update check ----------
# Only released versions count: Git tags vX.Y.Z. Tags with a suffix (v1.2.0-dev3) are ignored.

TAGS_API = "https://api.github.com/repos/Glenn-Dandy/NPMcountdy/tags?per_page=100"
RELEASE_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
update = {"latest": None, "checked": None, "error": None, "tried": 0}


def version_key(v):
    return tuple(int(x) for x in v.split("."))


def check_update():
    update["tried"] = time.time()
    try:
        req = urllib.request.Request(TAGS_API, headers={
            "User-Agent": f"NPMcountdy/{VERSION}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            tags = json.load(r)
        found = [tuple(map(int, m.groups())) for t in tags if (m := RELEASE_TAG.match(str(t.get("name", ""))))]
        if not found:
            raise ValueError("no version tags found")
        with state_lock:
            update.update(latest=".".join(map(str, max(found))), checked=int(time.time()), error=None)
    except Exception as e:
        why = getattr(e, "reason", None) or e
        with state_lock:
            update["error"] = f"{type(e).__name__}: {why}"


def update_loop():
    while True:
        if UPDATE_CHECK:  # can be switched on and off in the settings
            check_update()
        time.sleep(3600 if update["error"] else 12 * 3600)  # after an error try again in an hour


def update_info():
    with state_lock:
        u = dict(update)
    latest = u["latest"]
    return {"on": UPDATE_CHECK, "current": VERSION, "latest": latest,
            "newer": bool(latest and version_key(latest) > version_key(VERSION)),
            "url": f"{REPO}/releases/tag/v{latest}" if latest else f"{REPO}/tags",
            "checked": u["checked"], "error": u["error"]}


def update_now(q):
    """Check on request from the about page, at most every 30 seconds."""
    if UPDATE_CHECK and q.get("check") and time.time() - update["tried"] > 30:
        check_update()
    return update_info()


# ---------- Queries for the UI ----------

def day_str(d):
    return d.strftime("%Y-%m-%d")


class Period:
    """Time range of a request: the last N days (days=) or fixed days (from=, to=, YYYY-MM-DD).
    Up to 2 days by hour, longer ones by day. prev_* is the range of the same length right before it."""

    def __init__(self, q):
        self.host = q.get("host", [""])[0]
        self.bots = q.get("bots", ["1"])[0] != "0"
        self.group = q.get("group", ["1"])[0] != "0"  # domains of one proxy host as one
        self.hcol = "site(host)" if self.group else "host"
        real_now = datetime.now(TZ)
        today = real_now.date()
        first, last = q.get("from", [""])[0], q.get("to", [""])[0]
        self.custom = bool(first and last)
        if self.custom:
            try:
                d0, d1 = date.fromisoformat(first), min(date.fromisoformat(last), today)
            except ValueError:
                raise ValueError("from and to must be dates (YYYY-MM-DD)")
            d0, d1 = min(d0, d1), max(d0, d1)
            d0 = max(d0, d1 - timedelta(days=3649))
            self.days = (d1 - d0).days + 1
        else:
            self.days = max(1, min(int(q.get("days", ["7"])[0]), 3650))
            d1 = today
            d0 = d1 - timedelta(days=self.days - 1)
        midnight = lambda d: datetime(d.year, d.month, d.day, tzinfo=TZ)
        # end of the range: now, or the end of its last day
        self.now = now = real_now if d1 == today else midnight(d1 + timedelta(days=1)) - timedelta(seconds=1)
        self.hourly = self.days <= 2
        self.hours = 24 * self.days if self.custom else (48 if self.days == 2 else 24)
        start = midnight(d0)
        self.start_day, self.end_day = day_str(d0), day_str(d1)
        end_ts = int(midnight(d1 + timedelta(days=1)).timestamp())
        if self.hourly:
            # last 24/48 hours, or whole days when chosen by date
            self.start_ts = int(start.timestamp()) if self.custom else int(now.timestamp()) // 3600 * 3600 - (self.hours - 1) * 3600
            self.where, self.args = "ts >= ? AND ts < ?", [self.start_ts, end_ts]
            self.detail_day = day_str(datetime.fromtimestamp(self.start_ts, TZ))
            self.prev = ("ts >= ? AND ts < ?", [self.start_ts - self.hours * 3600, self.start_ts])
            self.prev_day = day_str(datetime.fromtimestamp(self.start_ts - self.hours * 3600, TZ))
        else:
            self.where, self.args = "day >= ? AND day <= ?", [self.start_day, self.end_day]
            self.detail_day = self.start_day
            # same length and the same time of day, so a running day is compared with a running day
            self.prev = ("ts >= ? AND ts < ?", [int((start - timedelta(days=self.days)).timestamp()),
                                                int((now - timedelta(days=self.days)).timestamp())])
            self.prev_day = day_str(start - timedelta(days=self.days))
        # the tables kept per day (files, visitors, countries, errors) start at detail_day
        self.dwhere, self.dargs = "day >= ? AND day <= ?", [self.detail_day, self.end_day]
        extra, extra_args = "", []
        if self.host:
            extra, extra_args = f" AND {self.hcol} = ?", [self.host]
        # without the bot filter, for the share of bots
        self.where_all, self.args_all = self.where + extra, self.args + extra_args
        if not self.bots:
            extra += f" AND kind != '{BOT}'"
        self.where += extra; self.args += extra_args
        self.dwhere += extra; self.dargs += extra_args
        self.prev = (self.prev[0] + extra, self.prev[1] + extra_args)
        self.extra, self.extra_args = extra, extra_args


def client_row(r):
    a = asn_of(r[0])
    return {"name": r[0], "net": r[1], "cc": country_of(r[0]), "asn": a[0] if a else None, "org": a[1] if a else "",
            "bytes": r[2], "requests": r[3], "hosts": r[4], "kinds": [k for k in (r[5] or "").split(",") if k]}


def buckets_for(p, step):
    """Chart columns of a range of days: days, weeks (starting Monday) or months."""
    def key(day):
        if step == "week":
            d = date.fromisoformat(day)
            return (d - timedelta(days=d.weekday())).isoformat()
        return day[:7] if step == "month" else day
    order, ranges, of_day = [], {}, {}
    for i in range(p.days):
        day = day_str(p.now - timedelta(days=p.days - 1 - i))
        k = key(day)
        if k not in ranges:
            order.append(k)
            ranges[k] = [day, day]
        ranges[k][1] = day
        of_day[day] = len(order) - 1
    return order, [ranges[k] for k in order], of_day


def summary(q):
    p = Period(q)
    country = q.get("country", [""])[0].upper()[:10]
    step = q.get("step", ["day"])[0]
    if p.hourly or step not in ("week", "month"):
        step = "day"
    where, args, dwhere, dargs = p.where, p.args, p.dwhere, p.dargs
    group, hcol = ("area" if p.host else p.hcol), p.hcol

    con = db_connect()
    one = lambda sql, a: con.execute(sql, a).fetchall()

    def totals(w, a):
        r = one(f"SELECT COALESCE(SUM(bytes),0), COALESCE(SUM(requests),0), "
                f"COALESCE(SUM(CASE WHEN net = 'Internet' THEN bytes END),0) FROM hourly WHERE {w}", a)[0]
        top = one(f"SELECT ts, SUM(bytes) FROM hourly WHERE {w} AND net = 'Internet' "
                  "GROUP BY ts ORDER BY 2 DESC LIMIT 1", a)
        return {"bytes": r[0], "requests": r[1], "internet": r[2],
                "peak": {"ts": top[0][0], "bytes": top[0][1]} if top else None}

    total = totals(where, args)
    first = one(f"SELECT MIN(day) FROM hourly WHERE 1 = 1{p.extra}", p.extra_args)[0][0]
    prev = None
    if first and first <= p.prev_day:  # only compare with a range that is fully recorded
        prev = totals(*p.prev)
        prev["visitors"] = None
        keep_from = day_str(datetime.now(TZ) - timedelta(days=DETAIL_DAYS - 1))
        if not p.hourly and p.prev_day >= keep_from:
            prev["visitors"] = one(f"SELECT COUNT(DISTINCT client) FROM daily_clients WHERE day >= ? AND day < ?{p.extra}",
                                   [p.prev_day, p.start_day] + p.extra_args)[0][0]

    if p.hourly:
        buckets = list(range(p.start_ts, int(p.now.timestamp()) // 3600 * 3600 + 1, 3600))
        labels = [datetime.fromtimestamp(b, TZ).strftime("%H:00") for b in buckets]
        index, ranges = {b: i for i, b in enumerate(buckets)}, None
        series_rows = one(f"SELECT ts, {group}, SUM(bytes) FROM hourly WHERE {where} GROUP BY 1, 2", args)
    else:
        labels, ranges, index = buckets_for(p, step)
        series_rows = one(f"SELECT day, {group}, SUM(bytes) FROM hourly WHERE {where} GROUP BY 1, 2", args)
    series = {}
    for b, g, v in series_rows:
        if b in index:
            series.setdefault(g, [0] * len(labels))[index[b]] += v

    # requests of bots and scanners per chart column, counted even when the bot filter is off
    bots_share = [{"requests": 0, "bots": 0} for _ in labels]
    for b, r, br in one(f"SELECT {'ts' if p.hourly else 'day'}, SUM(requests), "
                        f"SUM(CASE WHEN kind = ? THEN requests ELSE 0 END) FROM hourly WHERE {p.where_all} GROUP BY 1",
                        [BOT] + p.args_all):
        if b in index:
            bots_share[index[b]]["requests"] += r
            bots_share[index[b]]["bots"] += br

    # data per weekday and hour, as the average of the days of that weekday in the range (Monday first)
    week = [[0] * 24 for _ in range(7)]
    for wd, h, v in one(f"SELECT CAST(strftime('%w', day) AS INTEGER), hod, SUM(bytes) FROM hourly "
                        f"WHERE {where} GROUP BY 1, 2", args):
        week[(wd + 6) % 7][h] += v
    count, d = [0] * 7, date.fromisoformat(max(p.detail_day, first or p.detail_day))  # only days that were recorded
    while d <= p.now.date():
        count[d.weekday()] += 1
        d += timedelta(days=1)
    week = [[v / count[i] if count[i] else 0 for v in row] for i, row in enumerate(week)]

    def breakdown(col, limit=50):
        return [{"name": r[0], "bytes": r[1], "requests": r[2]} for r in one(
            f"SELECT {col}, SUM(bytes), SUM(requests) FROM hourly WHERE {where} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}", args)]

    hod = [0] * 24
    for h, v in one(f"SELECT hod, SUM(bytes) FROM hourly WHERE {where} GROUP BY 1", args):
        hod[h] = v

    paths = [{"host": r[0], "name": r[1], "bytes": r[2], "requests": r[3]} for r in one(
        f"SELECT {hcol}, path, SUM(bytes), SUM(requests) FROM daily_paths WHERE {dwhere} "
        f"GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {LIMIT}", dargs)]
    errors = [{"host": r[0], "name": r[1], "status": r[2], "requests": r[3]} for r in one(
        f"SELECT {hcol}, path, status, SUM(requests) FROM daily_errors WHERE {dwhere} "
        f"GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT {LIMIT}", dargs)]
    clients = [client_row(r) for r in one(
        f"SELECT client, net, SUM(bytes), SUM(requests), COUNT(DISTINCT {hcol}), GROUP_CONCAT(DISTINCT kind) "
        f"FROM daily_clients WHERE {dwhere} GROUP BY 1, 2 ORDER BY 3 DESC", dargs)]

    providers = []
    if GEO["asn"]["db"] is not None:
        by = {}
        for c in clients:
            k = c["asn"] or ("LAN" if c["net"] == "LAN" else "??")
            v = by.setdefault(k, {"name": c["org"] or k, "asn": c["asn"], "bytes": 0, "requests": 0, "visitors": 0})
            v["bytes"] += c["bytes"]; v["requests"] += c["requests"]; v["visitors"] += 1
        providers = sorted(by.values(), key=lambda v: -v["bytes"])[:50]

    total["visitors"] = len(clients)

    # where visitors come from: "" = typed in / app / bookmark, "=" = from the own site
    refs = {}
    for h, ref, b, r in one(f"SELECT {hcol}, referer, SUM(bytes), SUM(requests) FROM daily_referers "
                            f"WHERE {dwhere} GROUP BY 1, 2", dargs):
        if ref and ref != "=" and site_of(ref) == site_of(h):
            ref = "="  # another domain of the same proxy host
        v = refs.setdefault(ref, {"name": ref, "bytes": 0, "requests": 0})
        v["bytes"] += b; v["requests"] += r
    referers = sorted(refs.values(), key=lambda v: -v["requests"])[:50]

    upstreams = [{"name": r[0], "bytes": r[1], "requests": r[2], "sub": r[3]} for r in one(
        f"SELECT upstream, SUM(bytes), SUM(requests), GROUP_CONCAT(DISTINCT {hcol}) FROM daily_upstreams "
        f"WHERE {dwhere} GROUP BY 1 ORDER BY 2 DESC LIMIT 25", dargs)]

    # compression: what nginx packed (and the size before), what went out unpacked although it could be packed
    packed = {(r[0], r[1]): (r[2], r[3]) for r in one(
        f"SELECT {hcol}, path, SUM(bytes), SUM(orig) FROM daily_gzip WHERE {dwhere} GROUP BY 1, 2", dargs)}
    unpacked, plain, sent = [], 0, 0
    for h, path, b, r in one(f"SELECT {hcol}, path, SUM(bytes), SUM(requests) FROM daily_paths "
                             f"WHERE {dwhere} GROUP BY 1, 2", dargs):
        sent += b
        rest = b - packed.get((h, path), (0, 0))[0]
        last = path.rsplit("/", 1)[-1]
        if rest > 0 and "." in last and last.rsplit(".", 1)[-1].lower() in PACKABLE:
            plain += rest
            unpacked.append({"host": h, "name": path, "bytes": rest, "requests": r})
    unpacked.sort(key=lambda v: -v["bytes"])
    gz_sent = sum(v[0] for v in packed.values())
    gz_orig = sum(v[1] for v in packed.values())
    gzip_info = {"sent": sent, "packed": gz_sent, "orig": gz_orig,
                 "plain": plain, "unpacked": unpacked[:LIMIT]}
    since = one("SELECT value FROM meta WHERE key = 'v018_since'", [])
    v018 = since[0][0] if since and since[0][0] and since[0][0] > p.detail_day else None

    result = {
        "days": p.days, "host": p.host, "bots": p.bots, "hourly": p.hourly, "step": "hour" if p.hourly else step,
        "custom": p.custom, "from": p.start_day, "to": p.end_day, "bots_share": bots_share, "week": week,
        "group": group, "start_day": p.detail_day,
        "total": total, "prev": prev,
        "labels": labels, "ranges": ranges,
        "weeks": [date.fromisoformat(k).isocalendar()[1] for k in labels] if step == "week" and not p.hourly else None,
        "series": [{"name": g, "values": v} for g, v in sorted(series.items(), key=lambda x: -sum(x[1]))],
        "hosts": breakdown(hcol), "kinds": breakdown("kind"),
        "areas": [{"name": r[1], "sub": r[0], "bytes": r[2], "requests": r[3]} for r in one(
            f"SELECT {hcol}, area, SUM(bytes), SUM(requests) FROM hourly WHERE {where} "
            "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25", args)],
        "statuses": breakdown("status"), "nets": breakdown("net"),
        "referers": referers, "upstreams": upstreams, "gzip": gzip_info, "v018_since": v018,
        "apps": [a for a in apps_info(q)["apps"] if a["kind"] != BOT][:12],
        "hod": hod, "paths": paths, "errors": errors, "clients": clients[:LIMIT], "providers": providers,
        "countries": [{"name": r[0], "bytes": r[1], "requests": r[2]} for r in one(
            f"SELECT country, SUM(bytes), SUM(requests) FROM daily_countries WHERE {dwhere} "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 300", dargs)],
    }
    if country:
        cargs = dargs + [country]
        day_list = []
        d = date.fromisoformat(p.detail_day)
        while d <= p.now.date():
            day_list.append(d.isoformat()); d += timedelta(days=1)
        per_day = dict(one(f"SELECT day, SUM(bytes) FROM daily_countries WHERE {dwhere} AND country = ? GROUP BY 1", cargs))
        matching = [c for c in clients if c["cc"] == country]
        result["country"] = {
            "code": country,
            "hosts": [{"name": r[0], "bytes": r[1], "requests": r[2]} for r in one(
                f"SELECT {hcol}, SUM(bytes), SUM(requests) FROM daily_countries WHERE {dwhere} AND country = ? "
                "GROUP BY 1 ORDER BY 2 DESC", cargs)],
            "days": [{"day": x, "bytes": per_day.get(x, 0)} for x in day_list],
            "clients": matching[:50], "visitors": len(matching),
        }
    con.close()
    return result


def client_detail(q):
    """Everything stored about one visitor IP in the time range: domains, client types, data per day."""
    p = Period(q)
    ip = q.get("ip", [""])[0].strip()[:64]
    con = db_connect()
    rows = con.execute(f"SELECT day, {p.hcol}, kind, SUM(bytes), SUM(requests) FROM daily_clients "
                       f"WHERE client = ? AND {p.dwhere} GROUP BY 1, 2, 3", [ip] + p.dargs).fetchall()
    con.close()
    hosts, kinds, per_day = {}, {}, {}
    for day, host, kind, b, r in rows:
        for table, k in ((hosts, host), (kinds, kind)):
            v = table.setdefault(k, {"name": k, "bytes": 0, "requests": 0})
            v["bytes"] += b; v["requests"] += r
        per_day[day] = per_day.get(day, 0) + b
    day_list, d = [], date.fromisoformat(p.detail_day)
    while d <= p.now.date():
        day_list.append(d.isoformat()); d += timedelta(days=1)
    a = asn_of(ip)
    by_bytes = lambda t: sorted(t.values(), key=lambda v: -v["bytes"])
    return {
        "ip": ip, "cc": country_of(ip), "asn": a[0] if a else None, "org": a[1] if a else "",
        "hosts": by_bytes(hosts), "kinds": [k for k in by_bytes(kinds) if k["name"]],
        "days": [{"day": x, "bytes": per_day.get(x, 0)} for x in day_list],
        "active": sum(1 for x in day_list if x in per_day), "last": max(per_day) if per_day else None,
    }


def path_clients(q):
    """Visitor IPs behind one row of the failed requests (path + code), files (path) or areas (area)."""
    p = Period(q)
    by = q.get("by", [""])[0]
    host, name, code = (q.get(k, [""])[0][:200] for k in ("of", "name", "code"))
    col = {"error": "path", "path": "path", "area": "area"}.get(by)
    if not col:
        return {"error": "by must be error, path or area"}
    where, args = f"{p.dwhere} AND {col} = ?", p.dargs + [name]
    if host:
        where += f" AND {p.hcol} = ?"; args.append(host)
    if by == "error":
        where += " AND code = ?"; args.append(code)
    con = db_connect()
    rows = con.execute(f"SELECT client, net, SUM(bytes), SUM(requests), COUNT(DISTINCT {p.hcol}), GROUP_CONCAT(DISTINCT kind) "
                       f"FROM daily_path_clients WHERE {where} GROUP BY 1, 2 ORDER BY 4 DESC, 3 DESC", args).fetchall()
    since = con.execute("SELECT value FROM meta WHERE key = 'pc_since'").fetchone()
    con.close()
    return {"clients": [client_row(r) for r in rows[:PC_LIMIT]], "visitors": len(rows),
            "since": since[0] if since and since[0] > p.detail_day else None}


def apps_info(q):
    """Apps seen in the range (first product token of the user agent) with their versions and client types."""
    p = Period(q)
    con = db_connect()
    rows = con.execute(f"SELECT app, version, kind, GROUP_CONCAT(DISTINCT {p.hcol}), SUM(bytes), SUM(requests) "
                       f"FROM daily_apps WHERE {p.dwhere} GROUP BY 1, 2, 3", p.dargs).fetchall()
    types = [{"name": n, "app": a, "pattern": pt} for n, a, pt in
             con.execute("SELECT name, app, pattern FROM client_types ORDER BY pos")]
    since = con.execute("SELECT value FROM meta WHERE key = 'apps_since'").fetchone()
    con.close()
    apps = {}
    for app, version, kind, hosts, b, r in rows:
        a = apps.setdefault(app.lower(), {"name": app, "bytes": 0, "requests": 0, "versions": {}, "kinds": {}, "hosts": set()})
        a["bytes"] += b; a["requests"] += r
        v = a["versions"].setdefault(version, {"name": version, "bytes": 0, "requests": 0})
        v["bytes"] += b; v["requests"] += r
        a["kinds"][kind] = a["kinds"].get(kind, 0) + r
        a["hosts"].update((hosts or "").split(","))
    out = []
    for a in sorted(apps.values(), key=lambda a: -a["requests"])[:50]:
        a["versions"] = sorted(a["versions"].values(), key=lambda v: -v["requests"])[:20]
        own = next((t["name"] for t in types if (t["app"] or "").lower() == a["name"].lower()), None)
        a["kind"] = own or max(a["kinds"], key=a["kinds"].get)  # own type, or the one most requests were counted as
        a["hosts"] = sorted(h for h in a["hosts"] if h)
        del a["kinds"]
        out.append(a)
    return {"apps": out, "types": types, "since": since[0] if since and since[0] > p.detail_day else None}


def change_client_type(body):
    """add: own client type for an app (name, app), delete: remove one (name). Applies to new log lines."""
    action, name = body.get("action"), str(body.get("name", "")).strip()[:40]
    app = str(body.get("app", "")).strip()[:40]
    if not name:
        raise ValueError("name: missing")
    if action not in ("add", "delete"):
        raise ValueError("action must be add or delete")
    if action == "add" and not APP_RE.fullmatch(app):
        raise ValueError("app: invalid")
    if action == "add" and (name == "Sonstige" or any(name == n for n, _ in BASE_RULES)):
        raise ValueError("name: already a built-in client type")
    with db_lock:
        con = db_connect()
        if action == "add":
            con.execute("DELETE FROM client_types WHERE app = ? COLLATE NOCASE", (app,))
            pos = con.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM client_types").fetchone()[0]
            con.execute("INSERT OR REPLACE INTO client_types VALUES (?, ?, ?, ?)", (pos, name, app_pattern(app), app))
        else:
            con.execute("DELETE FROM client_types WHERE name = ?", (name,))
        con.commit()
        load_client_types(con)
        con.close()
    return apps_info({"days": ["30"]})


def clear_host(host, group=True):
    """Deletes all stored figures of a domain (grouped: all domains of its proxy host).
    The NPM log files stay untouched."""
    col = "site(host)" if group else "host"
    with db_lock:
        con = db_connect()
        b, r = con.execute(f"SELECT COALESCE(SUM(bytes),0), COALESCE(SUM(requests),0) FROM hourly WHERE {col} = ?",
                           (host,)).fetchone()
        for table in ("hourly", "daily_paths", "daily_clients", "daily_countries", "daily_errors", "daily_path_clients",
                      "daily_referers", "daily_upstreams", "daily_gzip", "daily_apps"):
            con.execute(f"DELETE FROM {table} WHERE {col} = ?", (host,))
        con.commit()
        con.close()
    return {"host": host, "bytes": b, "requests": r}


def meta():
    con = db_connect()
    hosts = [r[0] for r in con.execute("SELECT host FROM hourly GROUP BY host ORDER BY SUM(bytes) DESC")]
    first = con.execute("SELECT MIN(day) FROM hourly").fetchone()[0]
    since = con.execute("SELECT value FROM meta WHERE key = 'detail_since'").fetchone()
    con.close()
    size = sum(os.path.getsize(DB_PATH + x) for x in ("", "-wal") if os.path.exists(DB_PATH + x))
    with state_lock:
        s = dict(status)
    with state_lock:
        sites = []
        for h in hosts:  # proxy hosts, ordered like their largest domain
            main = SITES.get(h, h)
            if main not in (x["name"] for x in sites):
                sites.append({"name": main, "members": MEMBERS.get(main, [main])})
    out = {"version": VERSION, "repo": REPO, "hosts": hosts, "sites": sites, "first_day": first, "detail_days": DETAIL_DAYS,
           "detail_since": since[0] if since else None, "clear": CLEAR, "interval": INTERVAL, "db_size": size,
           "update": update_info(), **s}
    for name, prefix in (("country", "geo"), ("asn", "asn")):
        g = GEO[name]
        out.update({prefix: g["on"], prefix + "_ready": g["db"] is not None, prefix + "_error": g["error"],
                    prefix + "_date": datetime.fromtimestamp(g["mtime"], TZ).strftime("%Y-%m-%d") if g["mtime"] else None})
    return out


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
        api = {"/api/summary": lambda: summary(parse_qs(u.query)), "/api/client": lambda: client_detail(parse_qs(u.query)),
               "/api/requesters": lambda: path_clients(parse_qs(u.query)), "/api/settings": settings_info,
               "/api/apps": lambda: apps_info(parse_qs(u.query)),
               "/api/meta": meta, "/api/update": lambda: update_now(parse_qs(u.query))}
        for path, fn in api.items():
            if u.path.endswith(path):
                try:
                    return self.send(200, json.dumps(fn()).encode(), "application/json")
                except Exception as e:
                    return self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        name = "index.html" if u.path.strip("/") in PAGES else os.path.basename(u.path)
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return self.send(404, b"Not found", "text/plain; charset=utf-8")
        ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css", "svg": "image/svg+xml",
                 "json": "application/json"}.get(
            name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            self.send(200, f.read(), ctype + "; charset=utf-8")

    def do_POST(self):
        err = lambda code, msg: self.send(code, json.dumps({"error": msg}).encode(), "application/json")
        path = urlparse(self.path).path
        if path.endswith("/api/settings") or path.endswith("/api/client-types"):
            if self.headers.get("X-NPMcountdy") != "settings":
                return err(403, "Only from the UI")
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Invalid request")
                res = save_settings(body) if path.endswith("/api/settings") else change_client_type(body)
                return self.send(200, json.dumps(res).encode(), "application/json")
            except (ValueError, TypeError) as e:
                return err(400, str(e))
            except OSError as e:
                return err(500, f"config.yaml: {e}")
        if not urlparse(self.path).path.endswith("/api/clear"):
            return err(404, "Not found")
        if not CLEAR:
            return err(403, "Deleting is turned off (CLEAR=off)")
        # Custom header: other websites would have to ask via CORS first, which this server never allows
        if self.headers.get("X-NPMcountdy") != "clear":
            return err(403, "Only from the UI")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
            host = str(body.get("host", "")).strip().lower()
            group = body.get("group", True) is not False
        except (ValueError, AttributeError):
            return err(400, "Invalid request")
        if not host:
            return err(400, "No domain given")
        try:
            return self.send(200, json.dumps(clear_host(host, group)).encode(), "application/json")
        except Exception as e:
            return err(500, str(e))


def main():
    print(f"NPMcountdy {VERSION}, Port {PORT}")
    os.makedirs(DATA_DIR, exist_ok=True)
    load_settings()
    db_init()
    threading.Thread(target=ingest_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
