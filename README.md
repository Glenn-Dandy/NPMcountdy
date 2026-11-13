# NPMcountdy

**English** · [Deutsch](README.de.md)

Version 0.1.4

Traffic dashboard for Nginx Proxy Manager (NPM). Reads its access logs and shows data and
requests per domain, path, visitor IP, country, provider and client type.

## Installation

```bash
git clone https://github.com/Glenn-Dandy/NPMcountdy.git ~/npmcountdy
cd ~/npmcountdy
cp .env.example .env
nano .env
sudo chown 1000:1000 data
sudo docker compose up -d --build
```

Put the NPM log folder into `.env`:

| NPM runs | `.env` |
|---|---|
| in Docker | `NPM_LOGS=/opt/data/logs` (path of its `/data` volume + `/logs`) |
| without Docker | `NPM_LOGS=/data/logs` |

Open: `http://<server-IP>:7881`. No login, only run it in your LAN.

## Updating

```bash
cd ~/npmcountdy
git pull
sudo docker compose up -d --build
```

`.env` is kept. If `git pull` stops because `docker-compose.yml` was changed: move the values
into `.env`, `git checkout docker-compose.yml`, then again.

## Settings

All in `.env`, see `.env.example`. After a change: `sudo docker compose up -d`

| Variable | Default | |
|---|---|---|
| `NPM_LOGS` | /opt/data/logs | NPM log folder |
| `TZ` | Europe/Berlin | Time zone |
| `INTERVAL` | 60 | Seconds between two log reads |
| `DETAIL_DAYS` | 90 | Days that paths and visitor IPs are kept |
| `CLEAR` | on | Delete a domain's data in the UI |
| `GEOIP` / `ASN` | on | Countries / providers for IPs |
| `UPDATE_CHECK` | on | Ask GitHub for new versions |

## Behind Cloudflare

To get the real visitor IP into the log instead of Cloudflare's, create `nginx/custom/server_proxy.conf`
in the NPM data folder with `real_ip_header CF-Connecting-IP;` and reload NPM.

## Sources

- Countries and providers: [DB-IP Lite](https://db-ip.com/db/lite.php) (CC BY 4.0), downloaded monthly
- World map: [Natural Earth](https://www.naturalearthdata.com/) (public domain)
