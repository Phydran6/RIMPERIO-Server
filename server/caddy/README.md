# Caddy

Reverse Proxy und TLS. Nimmt Port 80 und 443 an und verteilt nach hinten:
`/api/*` ans Backend, alles andere ans Frontend.

← [Übersicht](../README.md) · [Alle Dokumente](../docs/README.md)

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

Caddy lauscht intern auf `:2019`. Nur im Docker-Netz erreichbar; das Backend
spielt darüber neue Konfigurationen ein.
