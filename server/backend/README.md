# Backend

FastAPI. Nimmt Logs an, liefert sie aus, und verwaltet den Server selbst.

← [Übersicht](../../README.md) · [API-Dokumentation](../../docs/api/README.md) · [Alle Dokumente](../../docs/README.md)

## Aufbau

```
app/
  main.py          App, Ingest, öffentliche Webhooks, Hintergrundaufgaben
  config.py        Einstellungen aus der Umgebung
  database.py      Async-SQLAlchemy
  models.py        Tabellen
  schemas.py       Ein- und Ausgabeformate (Pydantic)
  auth.py          JWT, MFA-Zwischentoken, Sperren nach Fehlversuchen

  logparse.py      Rohzeilen lesbar machen (Anzeige, ändert nie Daten)
  backup.py        Sicherung und Wiederherstellung (ZIP, granular, verschlüsselbar)
  guard.py         Pflicht-Rückfrage "vorher sichern?" vor Systemeingriffen
  updater.py       Patchmanagement: Versionen, Releases, Kanal, Beobachter
  events.py        Ereignisverteiler (Server-Sent Events)
  ai.py            KI-Auswertung: Claude, ChatGPT, n8n
  mail.py          Mailversand und Postfix-Konfiguration
  stacks.py        Zusatzdienste ein-/ausschalten
  shell.py         Terminal im Browser (PTY auf dem Host)
  hostexec.py      Befehle auf dem Host ausführen (nsenter/systemd-run)
  archiving.py     Alte Logs auf SFTP/FTP/SMB auslagern
  diagnostics.py   Systemcheck (24 Prüfungen)
  branding.py      Whitelabel
  fritzbox.py      FRITZ!Box-Ereignisse einstufen
  ldap_auth.py     Anmeldung gegen Verzeichnis
  limiter.py       Rate-Limits

  routes/          Ein Modul je Bereich
scripts/
  rimperio-update.sh Wartungsskript — läuft auf dem HOST, nicht im Container
```

## Zwei Dinge, die überraschen könnten

**Warum manches auf dem Host läuft.** Ein Update baut die Container neu — auch
das Backend, das es angestoßen hat. Ein Prozess im Container stürbe dabei mitten
im Lauf. Deshalb betritt `hostexec.py` per `nsenter` die Namensräume des
Host-Init und startet den Lauf über `systemd-run` als eigene Unit. Dasselbe gilt
für Neustart, Zusatzdienste und Terminal.

Der Preis steht in [`docker-compose.yml`](../../docker-compose.yml): `privileged`,
`pid: host`, `SYS_BOOT`. Wer das nicht will, nimmt
[`server/deploy/hardened.yml`](../deploy/README.md) — dann entfallen genau diese
Funktionen, alles andere läuft weiter.

**Warum Migrationen beim Start laufen.** Fehlende Spalten, Indizes und Tabellen
legt das Backend beim Hochfahren selbst an (`ALTER TABLE … IF NOT EXISTS`).
Kein Alembic: Ein Update soll ohne zusätzlichen Schritt durchlaufen, auch auf
einer Installation, die drei Versionen übersprungen hat. Indizes auf `logs`
entstehen `CONCURRENTLY`, damit der Aufbau auf einer großen Tabelle keine
Schreibzugriffe blockiert.

## Hintergrundaufgaben

| Aufgabe | Takt | Zweck |
|---------|------|-------|
| `disk_monitor` | 5 min | Platte voll? Dreistufiges Aufräumen ab 80/90/95 % |
| `agent_retention_task` | 1 h | Aufbewahrungsregeln je Gerät durchsetzen |
| `archiving_task` | 10 min | Läuft die Archivierung zur eingestellten Stunde? |
| `updater.watch_task` | 2 min | Liegt auf GitHub etwas Neues? Wenn ja: sofort melden |

## Entwickeln

In das Verzeichnis wechseln:

```bash
cd backend
```

Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

Umgebung setzen (im selben Terminal):

```bash
export DB_HOST=127.0.0.1 DB_USER=rimperio DB_PASSWORD=… JWT_SECRET=$(openssl rand -hex 32)
```

Starten:

```bash
uvicorn app.main:app --reload --port 8000
```

API-Dokumentation dann unter `http://127.0.0.1:8000/api/docs` – für die lokale Entwicklung am einfachsten mit `RIMPERIO_API_DOCS=public` (sonst nur mit Admin-Ticket, siehe docs/api).

Änderungen gehören ins [Changelog](../../docs/changelog/backend.md); der Versionsstand
im Datei-Kopf wandert mit.
