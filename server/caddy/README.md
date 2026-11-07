# Caddy

Reverse Proxy und TLS. Nimmt Port 80 und 443 an und verteilt nach hinten:
`/api/*` ans Backend, alles andere ans Frontend.

← [Übersicht](../../README.md) · [Alle Dokumente](../../docs/README.md)

## Was hier liegt

[`Caddyfile`](Caddyfile) ist **nur der Zustand beim ersten Start**: reines HTTP
auf Port 80, kein Zertifikat, keine Domain.

Alles Weitere wird im Web-UI unter *Einstellungen → Netzwerk → Reverse Proxy*
eingerichtet. Die dort erzeugte Konfiguration wird gespeichert und beim Start
erneut geladen (`backend/app/routes/caddy.py`) — die Datei hier wird dabei nicht
überschrieben.

## Vier Betriebsarten

| Modus | Zertifikat | Voraussetzung |
|-------|-----------|---------------|
| **HTTP** | keins | nichts |
| **Let's Encrypt** | automatisch | FQDN, Port 80+443 aus dem Internet erreichbar, E-Mail |
| **Intern** | Caddy stellt selbst aus | nichts — der Browser warnt einmalig |
| **Eigenes** | selbst hochgeladen | Zertifikat und Schlüssel |

## Zwei Vorkehrungen gegen das Aussperren

**Port 80 bleibt immer erreichbar.** In jeder erzeugten Konfiguration, ohne
Zwangs-Umleitung auf HTTPS. Wer sich mit einem falschen Zertifikat oder einem
nicht auflösenden FQDN aussperrt, kommt über die IP auf Port 80 zurück ins UI.

**Notausstieg ohne UI.** Wenn TLS kaputt ist und gar nichts mehr geht:

In die `.env` eintragen:

```
CADDY_FORCE_HTTP=true
```

Backend neu starten — die gespeicherte TLS-Konfiguration wird verworfen und
alles läuft wieder auf reinem HTTP.

Zusätzlich wird jede Konfiguration **vor** dem Anwenden geprüft; schlägt das
fehl, fährt Caddy selbsttätig auf den vorherigen Stand zurück.

## Zusätzliche Adressen

Unter *Einstellungen → Netzwerk* lassen sich weitere Namen und IPs eintragen,
die ebenfalls per HTTPS bedient werden — nützlich, wenn ein Server intern anders
heißt als von außen.

## Admin-API

Die Admin-API lauscht **nicht im Netz**, sondern auf einem Unix-Socket
(`/caddy-admin/admin.sock` im Volume `caddy_admin`). Eingehängt haben das Volume
nur Caddy und das Backend. Früher lauschte sie auf `0.0.0.0:2019` im Docker-Netz,
und damit war sie ohne Anmeldung für jeden Container dort erreichbar, auch für n8n,
Open WebUI und Portainer.

Das Backend setzt die Admin-Adresse in jeder Caddyfile, die es lädt, selbst auf den
Socket (`app/caddy_admin.py`). Eine Zeile `admin 0.0.0.0:2019` aus einer alten
gespeicherten Konfiguration öffnet den Port also nicht wieder.

**Wächter:** Caddy liest beim Start nur die statische `Caddyfile` (HTTP). Startet der
Container neu, merkt das Backend binnen 30 Sekunden, dass die laufende Konfiguration
von der gespeicherten abweicht, und spielt die gespeicherte (z. B. HTTPS) wieder ein.

Von Hand am Server:

```bash
docker compose exec backend python -c "import httpx;print(httpx.get('http://127.0.0.1/config/',transport=httpx.HTTPTransport(uds='/caddy-admin/admin.sock')).text)"
```
