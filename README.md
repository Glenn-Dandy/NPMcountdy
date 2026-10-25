# Countdy

Zeigt, wie viele Daten der Nginx Proxy Manager (NPM) ausgeliefert hat: pro Domain, pro Bereich
(z. B. `/mapdata/` bei BoatSpeedy), pro Datei, pro Besucher, nach Tageszeit und nach Client-Art
(Browser, BoatSpeedy-App, Nextcloud-Clients, Bots).

- Liest `/opt/data/logs/proxy-host-*_access.log` des NPM nur mit (read-only), jede Minute.
- Speichert die Zahlen in `./data/countdy.db` (SQLite). Die Stundenwerte bleiben dauerhaft,
  auch wenn der NPM seine Logs löscht. Dateien und Besucher-IPs werden nach `DETAIL_DAYS` (90) Tagen gelöscht.
- Beim ersten Start werden auch die schon rotierten Logs (`.1`, `.2.gz` …) eingelesen.
- Gezählt wird `[Length …]` aus dem NPM-Log, also nur, was ausgeliefert wurde. Uploads fehlen.
- Nur Python-Standardbibliothek, keine Zusatzpakete.

## Betrieb auf dem NPM (<NPM-IP>)

```bash
cd ~/countdy
sudo docker compose up -d --build
```

Oberfläche: `http://<NPM-IP>:7881`

Keine eigene Anmeldung. Nur im LAN betreiben. Als `traffic.example.com` über den NPM:

1. NPM Proxy Host auf `<NPM-IP>:7881`, Access List nur LAN
2. Pi-hole Local DNS `traffic.example.com` auf `<NPM-IP>`
3. Firewall, weil der NPM-Container die eigene LAN-Adresse anruft:
   `sudo ufw allow from 172.16.0.0/12 to any port 7881 proto tcp comment 'Countdy fuer NPM-Proxy'`

## Einstellungen (docker-compose.yml)

| Variable | Standard | Bedeutung |
|---|---|---|
| `INTERVAL` | 60 | Sekunden zwischen zwei Einlesevorgängen |
| `DETAIL_DAYS` | 90 | Aufbewahrung von Dateien und Besucher-IPs |
| `TZ` | Europe/Berlin | Zeitzone für Tage und Uhrzeiten |
| `NPM_LOGS` | /opt/data/logs | Log-Ordner des NPM auf dem Host |

## Neu anfangen

```bash
sudo docker compose down
sudo rm -r data
sudo docker compose up -d
```
