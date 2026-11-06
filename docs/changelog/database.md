# Changelog — Datenbank / Deployment

Datenbank-Image & Deploy-Konfiguration (`docker-compose.yml`, `db/`, `install.sh`).
Versionsformat: `YYYY.MM.DD.HH.MM.SS`.

## 2026.10.11.20.00.00

### Added
- `deploy/optional.yml`: Log-Rotation (json-file 5 × 10 MB) auch für die Zusatzdienste Portainer, Tugtainer, n8n, Open WebUI und Postfix.

## 2026.10.10.22.00.00

### Added
- `docker-compose.yml`: Log-Rotation für alle Kern-Container (`x-logging`, json-file 5 × 10 MB). Vorher wuchsen die Container-Protokolle ohne Grenze — und genau dort landet jede Zeile einer Wiederholschleife, außer Reichweite des Plattenwächters.
- Neue Umgebungsvariablen: `DISK_INGEST_STOP`, `DISK_RESERVE_MB`, `LOG_REPEAT_WINDOW`, `ALERT_TELEGRAM_BOT_TOKEN`, `ALERT_TELEGRAM_CHAT_ID`, `ALERT_WEBHOOK_URL` (alle optional, sinnvolle Vorgaben).

## 2026.10.08.22.00.00

### Added
- Neue Tabelle `discovered_devices` für die Netzwerksuche — bewusst getrennt von `agents`: dort steht, wer Logs *liefert*, hier, wer im Netz *gefunden* wurde. Bestandsinstallationen bekommen sie beim Start des Backends (`ensure_discovery_table`), Neuinstallationen über `db/init.sql`.

## 2026.09.23.10.00.00

### Changed
- **Die Container heißen jetzt so, dass man ihnen ansieht, wem sie gehören.** Vorher hieß alles `logbot-irgendwas` — auch PostgreSQL und Caddy. Das war irreführend: PostgreSQL ist nicht „von LogBot“, es ist PostgreSQL. Und daran hängt eine praktische Frage, nämlich *wie* man ein Ding aktualisiert. Deshalb:

  | Präfix | Was es ist | Wie es aktualisiert wird |
  |---|---|---|
  | `logbot-app-*` | aus diesem Quellcode gebaut (backend, frontend, syslog) | System → Updates |
  | `logbot-ext-*` | fremdes, fertiges Image (postgres, caddy, n8n, …) | System → Container |

  Zusätzlich trägt jeder Dienst Labels (`de.logbot.origin`, `de.logbot.update`, `de.logbot.component`, `de.logbot.role`) — der Name ist für Menschen, die Labels für die Container-Übersicht. Die alten Namen bleiben im Docker-Netz als Alias erreichbar, gespeicherte Adressen wie `http://logbot-n8n:5678` funktionieren also weiter. `db/migrate.sh` akzeptiert beide Namen.

### Removed
- **Watchtower ist raus.** Es hat Images geholt *und* Container eigenmächtig ausgetauscht — nachts, ohne Ansage, hinterher schwer zuzuordnen. Auf einem Log-Server will das niemand.

### Added
- **Tugtainer** (`ghcr.io/quenary/tugtainer`, Profil `tugtainer`) nimmt seinen Platz ein und macht standardmäßig nur eines: nachsehen und melden. Der Docker-Socket ist deshalb nur **lesend** eingehängt — damit kann dieser Container gar nichts austauschen, selbst wenn ihn jemand umstellt. Wer es anders will: `TUGTAINER_SOCKET_MODE=rw`. Eingespielt wird in LogBots Oberfläche unter System → Container, in Tugtainers eigener Oberfläche oder auf der Kommandozeile.
- **Open WebUI** (Profil `openwebui`): KI-Oberfläche, die dieselbe Schnittstelle wie OpenAI spricht, dahinter aber ein lokales Modell haben kann. Mit Ollama verlässt bei der KI-Auswertung keine Logzeile den Server. Ollama selbst ist bewusst *nicht* Teil dieses Stacks — ein Sprachmodell will Arbeitsspeicher und möglichst eine Grafikkarte, das gehört nicht nebenbei auf einen Log-Server; `OLLAMA_BASE_URL` zeigt auf eine vorhandene Installation.
- Schema: `agent_tokens` bekommt `token_hash`, `prefix`, `kind`, `agent_id`, `max_uses`, `use_count`, `allowed_cidrs`, `expires_at`, `revoked_at`, `last_used_at`, `last_used_ip`, `created_by` und `note`; `token` (Klartext) wird nullable und bleibt nur für Altbestände gefüllt. Neue Tabelle `system_events` für das Systemtagebuch. Beides legt die Startup-Migration auch auf bestehenden Datenbanken an.
- Portainer und Tugtainer bekommen den Docker-Socket nur lesend. Der Installer würfelt fehlende Pflicht-Geheimnisse (`TUGTAINER_AGENT_SECRET`, `OPENWEBUI_SECRET_KEY`) aus, damit niemand an einer kryptischen Compose-Meldung hängenbleibt.

## 2026.09.15.20.00.00
### Added
- **Setup-Assistent `setup.sh`** im Wurzelverzeichnis, als Einzeiler startbar
  (`curl -sSL …/setup.sh | sudo bash`). Ein Menü für alles, was auf einem Linux-Rechner
  geht: Server installieren, aktualisieren, deinstallieren, komplett entfernen; Linux-Agent
  installieren, testen, deinstallieren, komplett entfernen; Systemprüfung. Jede Option wird
  abgefragt, der Token verdeckt. Vor dem Start zeigt der Assistent den passenden direkten
  Einzeiler und ruft dann `install.sh` bzw. `agents/install-linux.sh` mit `--yes` auf.
  *Warum ein eigenes Skript:* Der bisherige Einzeiler läuft ohne Tastendruck mit
  Standardwerten durch. Das ist richtig für Automatisierung, aber als Einstieg zeigt es die
  Möglichkeiten nicht. Ein Menü im Installer hätte dieses Verhalten geändert.
  Löschen verlangt die Eingabe `LÖSCHEN`. Die Token der Agents gehen per Umgebung weiter,
  nicht über die Kommandozeile.

### Changed
- README: Schnellstart mit dem Assistenten, darunter die direkten Einzeiler für Server,
  Linux- und Windows-Agent. Neuer Abschnitt **Deinstallieren** mit Tabelle, was bleibt und
  was gelöscht wird. Wichtig daran: `uninstall-purge` löscht auch die Sicherungs-ZIPs
  (Volume `backup_data`), und ohne `--yes` bricht es ohne Tastendruck ab. Dazu die Reste
  (`/opt/logbot-backups`, Images) und die richtige Reihenfolge (erst Agents, dann Server).

## 2026.09.12.12.00.00
### Added
- **`--ref` im Installer** (`install.sh`, auch als `LOGBOT_REF`): holt ein Release, Tag oder
  Commit statt des Zweigkopfes — beim Installieren wie beim Aktualisieren. Aufgelöst wird wie
  im Wartungsskript (Tag, dann Zweig auf dem Server, dann roher Commit), der flache Clone
  bekommt einen vollen Clone als Rückfallebene, weil `--branch` keinen Commit annimmt.
  *Warum:* Die Update-Seite gibt für einen festgelegten Stand den Einzeiler
  `install.sh … update -y --ref <tag>` aus. Den Schalter kannte bisher nur das Wartungsskript —
  der Installer verwarf ihn mit einer Warnung und aktualisierte trotzdem auf den Zweigkopf.

### Changed
- `install.sh`: Der Abschluss nennt zusätzlich das Wartungsskript (Sicherung und selbsttätiger
  Rückfall) und verweist auf `docs/updates/README.md`.
- `install.sh`: setzt vor Git-Zugriffen `safe.directory` für das Installationsverzeichnis —
  sonst bricht `git pull` als root mit „dubious ownership“ ab.

## 2026.09.09.22.00.00
### Added
- **`deploy/optional.yml`**: Portainer, Watchtower, n8n und Postfix, je hinter einem eigenen
  Compose-Profil. Keiner startet ungefragt. Portainer bekommt den Docker-Socket nur lesend;
  Watchtower fasst LogBots eigene Container nicht an (`WATCHTOWER_LABEL_ENABLE`), damit sich
  nicht zwei Update-Wege in die Quere kommen.
- **`install/preflight.sh`**: Systemprüfung vor der Installation — Architektur, Kernel,
  Betriebssystem, Rechte, RAM, Swap, Platte, Kerne, Docker samt Compose-Plugin, belegte
  Ports, DNS, GitHub, NTP. Gerechnet gegen die gewählten Zusatzdienste, mit dreistufiger
  Antwort (0 passt / 2 wird knapp / 1 geht nicht).
  *Warum dreistufig:* Ein kleiner Server trägt LogBot problemlos — nur eben nicht mit
  Portainer, n8n und Postfix obendrauf. Das gehört gesagt, nicht verboten.
- **Feature-Auswahl im Installer**: `--with`, `--no-addons`, `--skip-preflight`; im manuellen
  Modus als Menü. Passwörter für die gewählten Dienste werden erzeugt und in der `.env`
  abgelegt, `COMPOSE_FILE` und `COMPOSE_PROFILES` gesetzt.
- **`--ref` im Wartungsskript** (`backend/scripts/logbot-update.sh`): holt ein bestimmtes
  Release, Tag oder Commit statt des Zweigkopfes. Reihenfolge beim Auflösen: Tag, Zweig auf
  dem Server, roher Commit — damit ein Tag, der wie ein Zweig heisst, nicht zufällig gewinnt.
  Der flache Clone bekommt einen vollen Clone als Rückfallebene, weil `--branch` keinen
  Commit annimmt.
- Neue Volumes `backup_data` (Sicherungen) und `app_data` (Branding, Postfix-Konfiguration) —
  beide überstehen einen Neubau der Container.

### Changed
- Compose-Varianten liegen jetzt unter `deploy/` (`external-db.yml`, `hardened.yml`,
  `optional.yml`) statt im Wurzelverzeichnis.
- Neue Umgebungsvariablen: `LOGBOT_UPDATE_WATCH`, `LOGBOT_UPDATE_WATCH_INTERVAL`,
  `LOGBOT_WEBHOOK_SECRET`, `LOGBOT_BACKUP_DIR`, `LOGBOT_KEEP_BACKUPS`, `LOGBOT_WEBSHELL`
  (samt Grenzen) und `LOGBOT_DEFAULT_LANGUAGE`.

## 2026.08.14.12.00.00
### Added
- **`VERSION`** im Wurzelverzeichnis: der installierte Stand, gegen den die Update-Prüfung den
  Stand auf GitHub vergleicht. Bei jedem Release mit anheben.
- **`.gitattributes`**: `*.sh text eol=lf`. Ein Checkout mit CRLF lässt jedes Shell-Skript unter
  Linux mit `bad interpreter: /bin/bash^M` scheitern — das betrifft `install.sh` genauso wie das
  neue Wartungsskript.
- `docker-compose.yml` (Backend): `LOGBOT_INSTALL_DIR` (Vorgabe `/opt/logbot`),
  `LOGBOT_REPO_SLUG`, `LOGBOT_BRANCH`, `GITHUB_TOKEN` — alle optional, alle mit Vorgabe.
  Sie steuern das Patchmanagement unter *System → Updates*.

### Changed
- `install.sh`: Abschluss nennt jetzt beide Update-Wege (Oberfläche und Einzeiler).
- `docker-compose.hardened.yml`: hält fest, dass mit den engeren Rechten auch das Update über
  die Oberfläche wegfällt — Updates laufen dann nur noch über den Einzeiler.

## 2026.08.02.18.00.00
### Security
- **PostgreSQL war auf allen Netzwerkschnittstellen offen** (`ports: "5432:5432"`). Die
  Datenbank hing damit im Netz und war nur durch das Passwort geschützt. Der Port ist jetzt an
  `127.0.0.1` gebunden; wer den Zugriff von außen wirklich braucht, setzt `DB_BIND=0.0.0.0`.
- **`docker-compose.hardened.yml`** (neu): nimmt dem Backend `privileged`, `pid: host`,
  `seccomp=unconfined` und `SYS_BOOT` und setzt `no-new-privileges`. Diese Rechte existieren nur
  für den Neustart-Knopf und das Auslesen der Host-DNS-Server; sie heben die Trennung zwischen
  Container und Server praktisch auf. Ohne sie laufen alle Kernfunktionen weiter — nur der
  Neustart-Knopf und die automatische DNS-Übernahme entfallen. Der Grund für die Rechte steht
  jetzt auch als Warnung im Haupt-Compose.

### Added
- `webauthn_credentials` in `db/init.sql` (Passkeys) sowie `users.auth_source` für
  Neuinstallationen.

## 2026.08.02.16.00.00
### Added
- **`docker-compose.external-db.yml`**: LogBot mit einer Datenbank betreiben, die nicht Teil des
  Stacks ist. Der mitgelieferte Postgres-Container wird über ein nie aktiviertes Profil
  stillgelegt (Volume und Daten bleiben erhalten, der Rückweg ist offen), die Wartebedingungen
  von `backend` und `syslog` entfallen. Start:
  `docker compose -f docker-compose.yml -f docker-compose.external-db.yml up -d`
- `.env`: `DB_HOST`/`DB_PORT` sind jetzt überschreibbar, neu sind `DATABASE_URL` (komplette
  Verbindung) und `DB_SSLMODE`. Beide werden an **backend und syslog** durchgereicht.

## 2026.07.31.23.30.00
### Added
- **`logs.dedup_key`** (`VARCHAR(64)`) plus partieller Unique-Index
  `idx_logs_dedup_key ... WHERE dedup_key IS NOT NULL` für die Duplikat-Erkennung beim
  HTTPS-Ingest. Partiell, damit die bestehenden Syslog-Zeilen (Schlüssel NULL) unberührt
  bleiben. Bestands-Datenbanken bekommen beides beim Start über eine Migration in
  `backend/app/main.py` (`CREATE INDEX CONCURRENTLY`, blockiert keine Schreibzugriffe).

## 2026.07.31.21.40.00
### Added
- **`install.sh` als One-Liner nutzbar**: `curl -sSL .../install.sh | sudo bash`. Fehlt das
  Repo lokal, klont der Installer es selbst (`--repo`/`--branch`, Default `main`).
- **Aktionen** `install` (Standard), `update`, `uninstall`, `uninstall-purge` sowie Optionen
  `--dir`, `--repo`, `--branch`, `--no-build`, `--yes`, `--timeout` (auch als `LOGBOT_*`).
- **Start-Gate wie beim Agent-Installer**: 5 s Countdown, Tastendruck schaltet auf Rückfragen,
  sonst läuft alles automatisch durch (`read </dev/tty`, funktioniert auch hinter der Pipe).
- `docker compose up -d` läuft jetzt mit **`--remove-orphans`**, damit beim Update Container
  verschwinden, die nicht mehr in der Compose-Datei stehen (z. B. der alte portainer-agent).

### Fixed
- **Bestehende `.env` wird nicht mehr überschrieben.** Bisher erzeugte eine erneute
  Installation neue Zufallspasswörter, während das Postgres-Volume die alten behielt —
  das Backend kam danach nicht mehr an die Datenbank. Beim Ersetzen einer Installation
  wird die `.env` zusätzlich aus dem Backup übernommen.
- Installation aus dem Zielverzeichnis heraus (`/opt/logbot/install.sh`) bricht nicht mehr
  mit „same file" ab; das Kopieren entfällt dann bzw. folgt dem verschobenen Backup.
- `generate_password` fällt ohne `openssl` auf `/dev/urandom` zurück.

## 2026.07.31.20.06.53
### Removed
- **`portainer-agent` aus `docker-compose.yml` entfernt** (Container `logbot-portainer-agent`,
  Port `9001`). LogBot bringt damit kein Fremd-/Management-System mehr mit; Verwaltungs-
  werkzeuge betreibt man getrennt vom LogBot-Stack.

### Security
- Der Portainer-Agent mountete `/var/run/docker.sock` und `/var/lib/docker/volumes`.
  Ein Zugriff auf den Docker-Socket entspricht faktisch Root-Rechten auf dem Host —
  diese Angriffsfläche entfällt jetzt.

### Hinweis (Update auf Bestandssystemen)
- Der alte Container läuft nach `git pull` noch weiter. Einmalig aufräumen mit:
  `docker compose up -d --remove-orphans` (bzw. `docker rm -f logbot-portainer-agent`).
- Falls Portainer weiter genutzt wird: den Agent separat starten, z. B.
  `docker run -d -p 9001:9001 --name portainer-agent --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v /var/lib/docker/volumes:/var/lib/docker/volumes portainer/agent:latest`

## 2026.07.19.16.00.00
### Added
- `db/migrate.sh` **curl-fest**: findet das Projektverzeichnis selbst
  (aktuelles Verzeichnis → skriptrelativ → `/opt/logbot`), Rückfrage über `/dev/tty`.
  One-Liner: `curl -sSL .../db/migrate.sh | sudo bash` (bzw. `| sudo bash -s -- 18 -y`).
- Robustere Parameter (`[Ziel-Major] [-y]`, Reihenfolge egal).

## 2026.07.19.15.00.00
### Changed
- **PostgreSQL-Image von `16-alpine` auf `17-alpine` (neu + stabil) angehoben.**
- Version über `POSTGRES_VERSION` in `.env` steuerbar: `image: postgres:${POSTGRES_VERSION:-17}-alpine`.
  Neuinstallationen bekommen direkt 17. Bestehende 16er-DBs können als **Notbremse**
  `POSTGRES_VERSION=16` setzen und laufen sofort weiter.

### Added
- `db/migrate.sh` — datenerhaltendes Major-Upgrade (Dump → Volume neu → Restore),
  Zielversion als Parameter (`sudo bash db/migrate.sh [17|18] [-y]`).
- README-Abschnitt „Update / PostgreSQL-Major-Upgrade" mit **drei Wegen**
  (Daten behalten / frische DB / Neuinstallation) + Notbremse.
- `POSTGRES_VERSION` in `.env.example` und im vom `install.sh` generierten `.env`.
- `.gitignore`: Migrations-Dumps (`logbot-db-backup-*.dump`, `*.dump`, `backup.sql`).

### Hinweis
- PostgreSQL-Datenverzeichnisse sind zwischen Major-Versionen **nicht** kompatibel –
  ein reiner Image-Tausch von 16 auf 17 startet nicht. Deshalb Migration bzw. Notbremse nutzen.

_Änderungen vor Einführung des Changelogs wurden nicht einzeln erfasst._
