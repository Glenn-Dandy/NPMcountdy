# NPMcountdy

**English** · [Deutsch](README.de.md)

Version 0.1.5

Traffic dashboard for Nginx Proxy Manager (NPM). Reads its access logs and shows data and
requests per domain, path, visitor IP, country, provider and client type.

## Requirements

Docker with the Compose plugin, and git. Check: `docker compose version`

Ubuntu/Debian: `sudo apt install docker.io docker-compose-v2 git`

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

`.env` is kept. If `git pull` stops because `docker-compose.yml` was changed: put the log folder
into `.env`, `git checkout docker-compose.yml`, then again.

## Settings

On the settings page: time zone, read interval, retention, countries, providers, update check.
Stored in `data/config.yaml`, applies right away and is kept on updates.

Only the log folder is in `.env` (`NPM_LOGS`), because Docker mounts it at start.
After a change: `sudo docker compose up -d`

## Behind Cloudflare

To get the real visitor IP into the log instead of Cloudflare's, create `nginx/custom/server_proxy.conf`
in the NPM data folder with `real_ip_header CF-Connecting-IP;` and reload NPM.

## Sources

- Countries and providers: [DB-IP Lite](https://db-ip.com/db/lite.php) (CC BY 4.0), downloaded monthly
- World map: [Natural Earth](https://www.naturalearthdata.com/) (public domain)
