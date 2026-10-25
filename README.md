# NPMcountdy

**English** · [Deutsch](README.de.md)

Version 0.0.2

Shows how much data Nginx Proxy Manager (NPM) has served: per domain, per area
(e.g. `/mapdata/` of BoatSpeedy), per file, per visitor, by time of day, by client type
(browser, BoatSpeedy app, Nextcloud clients, bots) and by country.

- Follows NPM's `/opt/data/logs/proxy-host-*_access.log` read-only, once a minute.
- Stores the figures in `./data/npmcountdy.db` (SQLite). Hourly figures are kept forever,
  even after NPM deletes its logs. Files and visitor IPs are deleted after `DETAIL_DAYS` (90) days.
- On the first start, logs that were already rotated (`.1`, `.2.gz` …) are read as well.
- It counts `[Length …]` from the NPM log, so only data that was actually served. Uploads are not included.
- Countries come from the free [DB-IP IP to Country Lite](https://db-ip.com/db/download/ip-to-country-lite)
  database (CC BY 4.0). It is downloaded to `./data` on start and renewed monthly. If `download.db-ip.com`
  cannot be reached, the same data is fetched via [jsDelivr](https://www.jsdelivr.com/package/npm/@ip-location-db/dbip-country-mmdb).
  If both fail, you can put the file in place by hand as `./data/dbip-country-lite.mmdb`.
  Visitor IPs never leave the server, lookups happen locally.
- If the country database only arrives later, countries are assigned afterwards, as far as the
  visitor IPs are still stored (`DETAIL_DAYS`).
- The world map (`app/static/world.json`) is based on [Natural Earth](https://www.naturalearthdata.com/)
  (public domain) and built with `tools/make_world.py`, see the header of that file.
- The UI is available in English and German. It follows the browser language, the switch is at
  the bottom of the menu on the left, and the browser remembers the choice.
- Python standard library only, no extra packages.

## Running it on the NPM host (<NPM-IP>)

```bash
cd ~/npmcountdy
sudo docker compose up -d --build
```

UI: `http://<NPM-IP>:7881` with these pages

| Address | Content |
|---|---|
| `/` | Overview: key figures, history, domains, client types, top countries |
| `/traffic` | Areas, time of day, responses, LAN or internet |
| `/countries` | World map and all countries, `/countries?country=DE` shows one country in detail |
| `/visitors` | Top visitor IPs with country |
| `/files` | Largest files and paths |

The German addresses (`/verteilung`, `/laender?land=DE`, `/besucher`, `/dateien`) work as well.

There is no login. Only run it in your LAN. To reach it as `traffic.example.com` through NPM:

1. NPM proxy host pointing to `<NPM-IP>:7881`, access list LAN only
2. Local DNS record (e.g. Pi-hole) `traffic.example.com` pointing to `<NPM-IP>`
3. Firewall, because the NPM container calls the host's own LAN address:
   `sudo ufw allow from 172.16.0.0/12 to any port 7881 proto tcp comment 'NPMcountdy for NPM proxy'`

## Settings (docker-compose.yml)

| Variable | Default | Meaning |
|---|---|---|
| `INTERVAL` | 60 | Seconds between two log reads |
| `DETAIL_DAYS` | 90 | How long files and visitor IPs are kept |
| `TZ` | Europe/Berlin | Time zone for days and hours |
| `NPM_LOGS` | /opt/data/logs | NPM log folder on the host |
| `CLEAR` | on | Allow deleting a domain's data in the UI, `off` disables it |
| `GEOIP` | on | Country lookup, `off` disables it |
| `GEOIP_URL` | empty | Custom address for the MMDB file (`.gz` works too), empty: DB-IP of the current month |

## Real visitor IP behind Cloudflare

If a domain is proxied by Cloudflare (orange cloud), the NPM log contains the address of a
Cloudflare server instead of the visitor. NPMcountdy lists these requests under countries as
"Cloudflare, origin unknown".

NPM already trusts the Cloudflare addresses (it loads their list on start), but reads the header
`X-Real-IP`, which Cloudflare does not send. Fix for all proxy hosts at once: in NPM's data folder,
create the file `nginx/custom/server_proxy.conf` containing

```nginx
real_ip_header CF-Connecting-IP;
```

and reload NPM: `sudo docker exec <npm-container> nginx -s reload`. From then on the log contains the
real addresses. nginx only accepts the header from Cloudflare and from the LAN. With
`IP_RANGES_FETCH_ENABLED=false` in NPM the Cloudflare list is missing and the line has no effect.
Other programs reading the NPM logs (e.g. CrowdSec) will see the real IPs as well.

## Deleting a domain's data

Pick the domain in the menu on the left, click "Delete data for this domain" below it and confirm.
This deletes all stored figures of that domain in `./data/npmcountdy.db`: history, files, visitors and
countries. NPM's log files stay untouched and are not read again. New requests to the domain are
counted from then on. Handy for old domains that no longer exist.

Since there is no login, anyone who can reach the UI can do this. `CLEAR: "off"` disables it.

## Starting over

```bash
sudo docker compose down
sudo rm -r data
sudo docker compose up -d
```
