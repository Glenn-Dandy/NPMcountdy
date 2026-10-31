# NPMcountdy

[English](README.md) · **Deutsch**

Version 0.0.3

Zeigt, wie viele Daten der Nginx Proxy Manager (NPM) ausgeliefert hat: pro Domain, pro Bereich
(z. B. `/mapdata/` bei BoatSpeedy), pro Datei, pro Besucher, nach Tageszeit und nach Client-Art
(Browser, BoatSpeedy-App, Nextcloud-Clients, Bots), nach Ländern und nach Anbietern (Netz der Besucher-IP).

- Liest `/opt/data/logs/proxy-host-*_access.log` des NPM nur mit (read-only), jede Minute.
- Speichert die Zahlen in `./data/npmcountdy.db` (SQLite). Die Stundenwerte bleiben dauerhaft,
  auch wenn der NPM seine Logs löscht. Dateien und Besucher-IPs werden nach `DETAIL_DAYS` (90) Tagen gelöscht.
- Beim ersten Start werden auch die schon rotierten Logs (`.1`, `.2.gz` …) eingelesen.
- Gezählt wird `[Length …]` aus dem NPM-Log, also nur, was ausgeliefert wurde. Uploads fehlen.
- Länder kommen aus der freien Datenbank [DB-IP IP to Country Lite](https://db-ip.com/db/download/ip-to-country-lite)
  (CC BY 4.0). Sie wird beim Start nach `./data` geladen und monatlich erneuert. Ist `download.db-ip.com`
  nicht erreichbar, kommen dieselben Daten über [jsDelivr](https://www.jsdelivr.com/package/npm/@ip-location-db/dbip-country-mmdb).
  Klappt beides nicht, kann man die Datei auch von Hand als `./data/dbip-country-lite.mmdb` ablegen.
  Die Besucher-IPs verlassen dabei den Server nicht, nachgeschlagen wird nur lokal.
- Anbieter kommen aus [DB-IP IP to ASN Lite](https://db-ip.com/db/download/ip-to-asn-lite) (CC BY 4.0),
  genauso geladen und erneuert wie die Länder, Ersatz ebenfalls über jsDelivr, von Hand als
  `./data/dbip-asn-lite.mmdb`.
- Kommt die Länderdatenbank erst später, werden die Länder nachträglich zugeordnet, soweit die
  Besucher-IPs noch gespeichert sind (`DETAIL_DAYS`).
- Die Weltkarte (`app/static/world.json`) stammt aus [Natural Earth](https://www.naturalearthdata.com/)
  (gemeinfrei) und wird mit `tools/make_world.py` erzeugt, Anleitung im Kopf der Datei.
- Oberfläche auf Englisch und Deutsch. Sie richtet sich automatisch nach der Sprache von Browser
  und System, unter Einstellungen (unten im Menü) lässt sie sich fest wählen.
- Kennzahlen mit Vergleich zum gleich langen Zeitraum davor, Spitzenlast ins Internet in Mbit/s
  (Stundenmittel), Jahresansicht nach Wochen oder Monaten.
- Schalter „Bots und Scanner mitzählen“ im Menü (Standard: an). Ausgeschaltet fehlen sie in allen Zahlen.
- Fehlerhafte Anfragen (4xx, 5xx) je Pfad und Statuscode unter Verteilung, z. B. fehlende Kartenkacheln.
- Tabellen lassen sich sortieren und durchsuchen, ein Klick auf eine Besucher-IP zeigt Domains,
  Client-Arten und Verlauf. Tooltips gehen auch per Antippen auf dem Handy.
- Seite „Über“ mit Status, Feedback-Links und Update-Prüfung: Der Server fragt alle 12 Stunden bei
  GitHub nach dem höchsten Versions-Tag `vX.Y.Z` (Tags mit Zusatz wie `-dev` zählen nicht). Gibt es
  eine neuere Version, steht das unten auf jeder Seite.
- Nur Python-Standardbibliothek, keine Zusatzpakete.

## Betrieb auf dem NPM (<NPM-IP>)

```bash
cd ~/npmcountdy
sudo docker compose up -d --build
```

Oberfläche: `http://<NPM-IP>:7881` mit den Unterseiten

| Adresse | Inhalt |
|---|---|
| `/` | Übersicht: Kennzahlen, Verlauf, Domains, Client-Arten, wichtigste Länder |
| `/verteilung` | Bereiche, Tageszeit, Antworten, LAN oder Internet, fehlerhafte Anfragen |
| `/laender` | Weltkarte und alle Länder, `/laender?land=DE` zeigt ein Land im Detail |
| `/besucher` | Anbieter und Besucher-IPs mit Land, Anbieter und Details |
| `/dateien` | größte Dateien und Pfade |
| `/einstellungen` | Sprache |
| `/ueber` | Version, Update, Feedback, Status, Quellen und Lizenzen |

Die englischen Adressen (`/traffic`, `/countries?country=DE`, `/visitors`, `/files`, `/settings`, `/about`) gehen genauso.

Keine eigene Anmeldung. Nur im LAN betreiben. Als `traffic.example.com` über den NPM:

1. NPM Proxy Host auf `<NPM-IP>:7881`, Access List nur LAN
2. Pi-hole Local DNS `traffic.example.com` auf `<NPM-IP>`
3. Firewall, weil der NPM-Container die eigene LAN-Adresse anruft:
   `sudo ufw allow from 172.16.0.0/12 to any port 7881 proto tcp comment 'NPMcountdy fuer NPM-Proxy'`

## Einstellungen (docker-compose.yml)

| Variable | Standard | Bedeutung |
|---|---|---|
| `INTERVAL` | 60 | Sekunden zwischen zwei Einlesevorgängen |
| `DETAIL_DAYS` | 90 | Aufbewahrung von Dateien und Besucher-IPs |
| `TZ` | Europe/Berlin | Zeitzone für Tage und Uhrzeiten |
| `NPM_LOGS` | /opt/data/logs | Log-Ordner des NPM auf dem Host |
| `CLEAR` | on | Daten einer Domain in der Oberfläche löschen, `off` schaltet das ab |
| `GEOIP` | on | Länderzuordnung, `off` schaltet sie ab |
| `GEOIP_URL` | leer | eigene Adresse für die MMDB-Datei (auch `.gz`), leer: DB-IP des aktuellen Monats |
| `ASN` | on | Anbieterzuordnung, `off` schaltet sie ab |
| `ASN_URL` | leer | eigene Adresse für die Anbieter-MMDB, leer: DB-IP des aktuellen Monats |
| `UPDATE_CHECK` | on | bei GitHub nach neuen Versionen fragen, `off` schaltet das ab |

## Update

```bash
cd ~/npmcountdy
git pull
sudo docker compose up -d --build
```

## Update von 0.0.2 oder älter

Beim ersten Start von 0.0.3 bekommen die Tabellen für Dateien, Besucher und Länder eine Spalte für
die Client-Art. Ältere Zeilen haben keine, deshalb wirkt der Bot-Schalter dort erst für Daten ab dem
Update, und fehlerhafte Anfragen werden erst ab dann erfasst. Die Oberfläche weist darauf hin.
Der Verlauf (Stundenwerte) kannte die Client-Art schon immer.

## Echte Besucher-IP hinter Cloudflare

Läuft eine Domain über Cloudflare (orange Wolke), steht im NPM-Log statt des Besuchers die Adresse
eines Cloudflare-Servers. NPMcountdy zeigt diese Anfragen unter Länder als „Cloudflare, Herkunft unbekannt“.

Der NPM vertraut den Cloudflare-Adressen schon (er lädt ihre Liste beim Start), liest aber den Header
`X-Real-IP`, den Cloudflare nicht mitschickt. Abhilfe für alle Proxy Hosts auf einmal: im Datenordner
des NPM die Datei `nginx/custom/server_proxy.conf` anlegen mit

```nginx
real_ip_header CF-Connecting-IP;
```

und den NPM neu laden: `sudo docker exec <npm-container> nginx -s reload`. Danach stehen im Log die
echten Adressen. Den Header nimmt nginx nur von Cloudflare und aus dem LAN an. Bei
`IP_RANGES_FETCH_ENABLED=false` im NPM fehlt die Cloudflare-Liste, dann wirkt die Zeile nicht.
Auch andere Programme, die die NPM-Logs lesen (z. B. CrowdSec), sehen danach die echten IPs.

## Daten einer Domain löschen

Domain im Menü links wählen, darunter „Daten dieser Domain löschen“ und bestätigen. Gelöscht werden
alle gespeicherten Zahlen dieser Domain in `./data/npmcountdy.db`: Verlauf, Dateien, Besucher und Länder.
Die Logdateien des NPM bleiben unverändert und werden auch nicht erneut eingelesen. Kommen neue
Zugriffe auf die Domain, wird ab dann wieder gezählt. Praktisch für alte Domains, die es nicht mehr gibt.

Weil es keine Anmeldung gibt, kann das jeder, der die Oberfläche erreicht. Mit `CLEAR: "off"` ist es abgeschaltet.

## Neu anfangen

```bash
sudo docker compose down
sudo rm -r data
sudo docker compose up -d
```
