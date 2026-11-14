# NPMcountdy

[English](README.md) · **Deutsch**

Version 0.1.9

Traffic-Dashboard für den Nginx Proxy Manager (NPM). Liest dessen Access-Logs und zeigt Daten
und Anfragen pro Domain, Pfad, Besucher-IP, Land, Anbieter und Client-Art.

## Voraussetzungen

Docker mit Compose-Plugin. Prüfen: `docker compose version`

Ubuntu/Debian: `sudo apt install docker.io docker-compose-v2`

## Installation

```bash
mkdir ~/npmcountdy && cd ~/npmcountdy
curl -O https://raw.githubusercontent.com/Glenn-Dandy/NPMcountdy/main/docker-compose.yml
nano docker-compose.yml
mkdir data && sudo chown 1000:1000 data
sudo docker compose up -d
```

In der `docker-compose.yml` unter `volumes` den Log-Ordner des NPM eintragen (links vom `:`):

| NPM läuft | Zeile |
|---|---|
| im Docker | `- /opt/data/logs:/logs:ro` (Pfad des `/data`-Volumes + `/logs`) |
| ohne Docker | `- /data/logs:/logs:ro` |

Aufrufen: `http://<Server-IP>:7881`. Keine Anmeldung, nur im LAN betreiben.

## Update

```bash
cd ~/npmcountdy
sudo docker compose pull
sudo docker compose up -d
```

Die `docker-compose.yml` und `data/` bleiben dabei unverändert.

### Umstieg von 0.1.5 oder älter (installiert mit git)

```bash
cd ~/npmcountdy
git checkout docker-compose.yml
git pull
nano docker-compose.yml        # Log-Ordner prüfen, siehe Tabelle oben
sudo docker compose pull
sudo docker compose up -d
```

Eine `.env` wird nicht mehr gelesen und kann weg. Danach nur noch die Update-Befehle oben.

## Einstellungen

Unter Einstellungen in der Oberfläche: Zeitzone, Einlese-Intervall, Aufbewahrung, Länder, Anbieter,
Update-Prüfung. Gespeichert in `data/config.yaml`, gilt sofort.

## Hinter Cloudflare

Damit im Log die echte Besucher-IP steht statt der von Cloudflare, im Datenordner des NPM
`nginx/custom/server_proxy.conf` anlegen mit `real_ip_header CF-Connecting-IP;` und den NPM neu laden.

## Quellen

- Länder und Anbieter: [DB-IP Lite](https://db-ip.com/db/lite.php) (CC BY 4.0), wird monatlich geladen
- Weltkarte: [Natural Earth](https://www.naturalearthdata.com/) (gemeinfrei)
