# NPMcountdy

**English** · [Deutsch](README.de.md)

Version 0.1.7

Traffic dashboard for Nginx Proxy Manager (NPM). Reads its access logs and shows data and
requests per domain, path, visitor IP, country, provider and client type.

## Requirements

Docker with the Compose plugin. Check: `docker compose version`

Ubuntu/Debian: `sudo apt install docker.io docker-compose-v2`

## Installation

```bash
mkdir ~/npmcountdy && cd ~/npmcountdy
curl -O https://raw.githubusercontent.com/Glenn-Dandy/NPMcountdy/main/docker-compose.yml
nano docker-compose.yml
mkdir data && sudo chown 1000:1000 data
sudo docker compose up -d
```

In `docker-compose.yml` under `volumes`, put the NPM log folder (left of the `:`):

| NPM runs | Line |
|---|---|
| in Docker | `- /opt/data/logs:/logs:ro` (path of its `/data` volume + `/logs`) |
| without Docker | `- /data/logs:/logs:ro` |

Open: `http://<server-IP>:7881`. No login, only run it in your LAN.

## Updating

```bash
cd ~/npmcountdy
sudo docker compose pull
sudo docker compose up -d
```

`docker-compose.yml` and `data/` stay as they are.

### Switching from 0.1.5 or older (installed with git)

```bash
cd ~/npmcountdy
git checkout docker-compose.yml
git pull
nano docker-compose.yml        # check the log folder, see the table above
sudo docker compose pull
sudo docker compose up -d
```

`.env` is no longer read and can be deleted. After that, only the update commands above.

## Settings

On the settings page: time zone, read interval, retention, countries, providers, update check.
Stored in `data/config.yaml`, applies right away.

## Behind Cloudflare

To get the real visitor IP into the log instead of Cloudflare's, create `nginx/custom/server_proxy.conf`
in the NPM data folder with `real_ip_header CF-Connecting-IP;` and reload NPM.

## Sources

- Countries and providers: [DB-IP Lite](https://db-ip.com/db/lite.php) (CC BY 4.0), downloaded monthly
- World map: [Natural Earth](https://www.naturalearthdata.com/) (public domain)
