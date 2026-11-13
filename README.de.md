# NPMcountdy

[English](README.md) · **Deutsch**

Version 0.1.4

Traffic-Dashboard für den Nginx Proxy Manager (NPM). Liest dessen Access-Logs und zeigt Daten
und Anfragen pro Domain, Pfad, Besucher-IP, Land, Anbieter und Client-Art.

## Installation

```bash
git clone https://github.com/Glenn-Dandy/NPMcountdy.git ~/npmcountdy
cd ~/npmcountdy
cp .env.example .env
nano .env
sudo chown 1000:1000 data
sudo docker compose up -d --build
```

In der `.env` den Log-Ordner des NPM eintragen:

| NPM läuft | `.env` |
|---|---|
| im Docker | `NPM_LOGS=/opt/data/logs` (Pfad des `/data`-Volumes + `/logs`) |
| ohne Docker | `NPM_LOGS=/data/logs` |

Aufrufen: `http://<Server-IP>:7881`. Keine Anmeldung, nur im LAN betreiben.

## Update

```bash
cd ~/npmcountdy
git pull
sudo docker compose up -d --build
```

Die `.env` bleibt dabei erhalten. Bricht `git pull` wegen einer geänderten `docker-compose.yml` ab:
Werte in die `.env` übernehmen, `git checkout docker-compose.yml`, dann nochmal.

## Einstellungen

Alle in der `.env`, siehe `.env.example`. Nach einer Änderung: `sudo docker compose up -d`

| Variable | Standard | |
|---|---|---|
| `NPM_LOGS` | /opt/data/logs | Log-Ordner des NPM |
| `TZ` | Europe/Berlin | Zeitzone |
| `INTERVAL` | 60 | Sekunden zwischen zwei Einlesevorgängen |
| `DETAIL_DAYS` | 90 | Tage, die Pfade und Besucher-IPs gespeichert bleiben |
| `CLEAR` | on | Daten einer Domain in der Oberfläche löschen |
| `GEOIP` / `ASN` | on | Länder / Anbieter zu IPs |
| `UPDATE_CHECK` | on | bei GitHub nach neuen Versionen fragen |

## Hinter Cloudflare

Damit im Log die echte Besucher-IP steht statt der von Cloudflare, im Datenordner des NPM
`nginx/custom/server_proxy.conf` anlegen mit `real_ip_header CF-Connecting-IP;` und den NPM neu laden.

## Quellen

- Länder und Anbieter: [DB-IP Lite](https://db-ip.com/db/lite.php) (CC BY 4.0), wird monatlich geladen
- Weltkarte: [Natural Earth](https://www.naturalearthdata.com/) (gemeinfrei)
