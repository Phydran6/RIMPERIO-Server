# Changelog — Backend

FastAPI-API (`backend/`). Versionsformat: `YYYY.MM.DD.HH.MM.SS`.

## 2026.10.18.22.00.00

### Fixed
- `_apply_caddyfile` sendet `Cache-Control: must-revalidate` an `/load` — ohne erzwang Caddy bei unveränderter Config kein Neuladen, ein ausgetauschtes `custom.crt` blieb unbenutzt.
- `POST /api/caddy/certificates` lädt Caddy sofort neu, wenn die gespeicherte Config `custom.crt` nutzt, und prüft vorher Zertifikat/Key (`_check_cert_and_key`: PEM gültig, Key passt, kein Passwort). Antwort enthält `reloaded` und `not_after`.

## 2026.10.11.21.00.00

### Fixed
- `update_status(force=True)` holte die Release-Liste nicht neu (`resolve_target_ref`/`latest_release` ohne `force`) — ein gerade veröffentlichtes Release war bis zu 15 min unsichtbar. Der Beobachter (`version_check`) bleibt absichtlich beim Zwischenspeicher (GitHub-Limit 60/h ohne Token).

## 2026.10.11.20.00.00

### Fixed
- **Ingest-Limit pro Agent statt pro IP** (`limiter.agent_key`, Abdruck des Schlüssels): hinter NAT/Proxy teilten sich alle Agents 600 Anfragen pro Minute.
- **Aufbewahrung je Gerät** (`_enforce_agent_retention`) löscht in Häppchen mit Commit je Häppchen statt mit einem DELETE über alle Zeilen.
- **Abgewiesene Lieferungen** (401/403) werden je Absender/Gerät/Grund höchstens alle 10 Minuten ins Tagebuch geschrieben, mit Zähler der unterdrückten.
- **Update-Skript** (`scripts/logbot-update.sh`): Datenbank-Abzug wird übersprungen, wenn weniger als halbe Datenbankgröße + 1 GB frei ist; `prune_backups` läuft auch nach Fehlschlägen (die neueste Sicherung bleibt immer).
- Ungültige Escape-Sequenz in einem Docstring (`routes/logs.py`, Warnung ab Python 3.12).

## 2026.10.10.23.30.00

### Fixed
- `GET /api/updates/releases` lieferte aus dem Zwischenspeicher keine `channels` (und kein `source`/`error`) — die Kanalauswahl der Update-Seite war dann leer.

## 2026.10.10.23.00.00

### Fixed
- **Update-Prüfung verschwieg neuere Stände ohne Release.** Beim Kanal „Stabil“ (= neuestes Release) meldete `/api/updates/status` „entspricht GitHub“, auch wenn auf dem Zweig längst mehr lag. Neu: `unreleased` (Version auf dem Zweig, eigener kleiner Zwischenspeicher über die `VERSION`-Datei, damit der Release-Cache nicht verdrängt wird).
- Update-Protokoll: Zeitstempel tragen jetzt ihre Zeitzone (`%Z`) — vorher Serverzeit ohne Angabe, scheinbar zwei Stunden neben der Anzeige im Browser.

## 2026.10.10.22.00.00

### Added
- **Wachhund** (`app/watchdog.py`): prüft jede Minute Datenbank, Platte, jüngsten Log (Funkstille), Flut je Gerät und verstummte Geräte. Zustand in `/api/health`.
- **Meldungen nach außen** (`app/alerts.py`, `routes/alerts.py`, `/api/alerts/*`): Mail (über System → Mail), Telegram (Bot-Token + Chat-ID, ohne Zusatzdienst) und Webhook (JSON-POST, z. B. n8n, optional `X-LogBot-Secret`). Sperrfrist je Meldung, Entwarnung bei Behebung. Einstellungen gespiegelt nach `data/alerts.json` (0600), damit Meldungen auch ohne Datenbank rausgehen; Rückfall per `ALERT_TELEGRAM_BOT_TOKEN`/`ALERT_TELEGRAM_CHAT_ID`/`ALERT_WEBHOOK_URL`.
- **`/api/health/ready`**: 200 nur, wenn LogBot wirklich sammelt, sonst 503 — für Uptime Kuma & Co.
- **Notreserve** (`DISK_RESERVE_MB`, Standard 1024, max. 5 % der Platte): wird ab `DISK_RESERVE_RELEASE` (97 %) gelöscht, damit Postgres weiterläuft; neu angelegt, sobald wieder unter 75 %.
- **Annahme-Stopp** ab `DISK_INGEST_STOP` (95 %): `/api/agents/ingest` antwortet 503 mit `Retry-After`.
- **Wiederholungen zusammenfassen** (`LOG_REPEAT_WINDOW`, 60 s): gleiche Meldung (Stufe, Quelle, Text) innerhalb einer Lieferung → eine Zeile mit `extra_data.repeat`/`repeat_last`. FRITZ!Box ausgenommen (Dedup über den Puffer). Logliste liefert `repeat` mit.
- **Kürzere Aufbewahrung für debug/info/notice** (Einstellung `log_retention_days_info`).
- Update-Seite: `command` und `install_dir` in `/api/updates/status` (genau die Zeile, die auf dem Host läuft — eine Quelle über `build_command`).

### Fixed
- **`/api/health` meldete immer „healthy“** — auch während die Datenbank 17 Tage stand. Jetzt `status`, `database`, `last_log_at`, `problems`. Der Statuscode bleibt 200 (das Update-Skript wartet darauf).
- **Aufbewahrung wurde nur unter Platzdruck durchgesetzt** (ab 80 %). Jetzt stündlich (`LOG_RETENTION_INTERVAL`).
- **Plattenwächter halbierte die Aufbewahrung bei 80–88 % jede Minute weiter** bis auf 24 h, weil ein gewöhnliches VACUUM keinen Platz ans System zurückgibt. Stufe 2 jetzt erst ab der hohen Stufe und pro Lauf nur ein Schritt (außer kritisch).
- **Mail-Benachrichtigungen hatten keinen Aufrufer** — `mail.notify` wurde nie benutzt. Die Haken werden in die neuen Meldungen übernommen.
- **Halb gebaute Indizes** nach abgebrochenem `CREATE INDEX CONCURRENTLY` blieben ungültig liegen; beim Dedup-Index scheiterte danach jede Lieferung mit Zeitstempel. Werden beim Start erkannt und neu gebaut.
- **Ingest:** NUL-Zeichen (500), zu lange Quellnamen (422 für die ganze Lieferung) und Lieferungen über ~2.700 Einträge (asyncpg-Parametergrenze, 500) brachten Sammler in Wiederholschleifen. Jetzt bereinigt, gekürzt bzw. in Stücken zu 1.000 gespeichert.
- **Ingest 403 wurde zu 500** (Nachladen nach Rollback in der async-Session).
- **JSON-Felder wurden an Ort und Stelle geändert** und deshalb nicht gespeichert: Mail-Einstellungen (`notify_on`), Archivierungs-Verlauf, „Gerät stilllegen“.
- Archivierung: fehlendes Rollback nach DB-Fehler.
- SSE: abgehängte Verbindung lief mit Herzschlägen weiter und bekam nie wieder ein Ereignis.
- Systemtagebuch: Aufräumen erst nach 24 h Laufzeit — bei häufigen Neustarts nie.
- `update.log` wurde nie geleert (wächst endlos, Live-Ansicht begann mit dem alten Lauf) — wird pro Lauf rotiert (`update.log.1`).
- `/api/health/detailed` rechnete die Plattenbelegung anders als der Plattenwächter.

## 2026.10.08.22.00.00

### Added
- **Linux-Systeme per SSH selbst einbinden** (`app/onboard.py`, `/api/discovery/devices/{id}/onboard`). Der Server verbindet sich mit einem *normalen* Benutzer (sudo, nicht root), prüft System und Rechte, überträgt den Installer direkt über die SSH-Verbindung (das Ziel braucht dafür weder Internet noch Zugriff auf den Server), führt ihn mit einer frisch erzeugten Einladung aus und meldet „eingebunden“, sobald die erste Logzeile ankommt. Der SSH-Hostschlüssel wird vorab zur Bestätigung angezeigt (`GET …/fingerprint`) und beim Verbinden gegengeprüft — gegen einen Mittelsmann. **Zugangsdaten leben nur für den einen Vorgang:** nie in der Datenbank, nie im Tagebuch, am Ende überschrieben.
- **Installer vom Server** (`GET /api/agents/installer/linux`). Liefert `agents/install-linux.sh` aus, damit der Einzeiler auch ohne GitHub-Zugang funktioniert: erst GitHub, sonst dieser Server.
- **Netzwerksuche** (`app/discovery.py`, `/api/discovery/*`). Findet die Systeme im Netz und beantwortet für jedes: liefert es schon Logs, und wenn nicht, wie bindet man es ein? Gesucht wird in den eigenen Subnetzen des Hosts (gelesen per `hostexec`, weil der Container nur sein Docker-Netz sieht), in eingetragenen Bereichen und **subnetzübergreifend über Router**: das Standard-Gateway und eingetragene Router werden per SNMP nach ARP-, Adress- und Routingtabelle gefragt. Abgetastet wird mit Ping (eigener ICMP-Socket, ohne externes Programm) und TCP-Verbindungsversuchen auf typische Ports; danach liefert die ARP-Tabelle des Hosts auch Geräte, die jede Anfrage verwerfen. Rückwärts-DNS, SSH-Kennung und SNMP-`sysDescr` ergänzen die Erkennung. Der Abgleich mit `agents` (MAC → IP → Hostname) passiert beim Lesen, nicht beim Suchen — ob ein Gerät liefert, ändert sich mit jeder Logzeile.
- **Kleiner SNMPv2c-Client** (`app/snmp.py`), nur GET und GETBULK, ohne neue Abhängigkeit. pysnmp wäre ein großer Brocken mit wechselnder API, nur um drei Tabellen zu lesen.
- **Automatische Suche**: einmal kurz nach dem ersten Start, danach alle 24 Stunden (abschaltbar). Jeder Lauf steht im Systemtagebuch, neue Kategorie `discovery`.
- Die Parallelität der Suche richtet sich nach dem Dateilimit des Prozesses: das weiche Limit wird angehoben, reicht es trotzdem nicht, wird langsamer gesucht statt mit „Too many open files“ abzubrechen.

## 2026.09.23.10.00.00

### Security
- **Agent-Schlüssel waren für jedes angemeldete Konto im Klartext lesbar.** `GET /api/agent-tokens` hing an `get_current_user` statt an `get_current_admin` und gab die Schlüssel unverändert heraus — ein Konto mit reinen Leserechten auf die Logs kam damit an den Generalschlüssel und konnte neue Schlüssel anlegen und vorhandene löschen. Alle Endpunkte unter `/api/agent-tokens` sind jetzt Administratoren vorbehalten, ebenso `DELETE /api/agents/{id}` und die Aufbewahrung je Gerät.
- **Ein Schlüssel für alle Geräte ist vorbei.** Neues Modul `app/tokens.py` mit drei Arten (`global`, `agent`, `enroll`). Jeder Agent bekommt beim Installieren über `POST /api/agents/enroll` einen eigenen Schlüssel, der nur für sein Gerät liefern und nur sich selbst abmelden darf. Wird ein Rechner übernommen, entwertet man genau diesen einen — die anderen laufen weiter.
- **Kein Schlüssel liegt mehr im Klartext in der Datenbank.** Gespeichert wird nur ein SHA-256-Abdruck (`token_hash`), angezeigt wird der Schlüssel genau einmal nach dem Erzeugen. Bestehende Schlüssel funktionieren unverändert weiter: beim Start wird ihr Abdruck nachgetragen, und die Oberfläche bietet das Überführen an. SHA-256 statt bcrypt, weil diese Werte 256 Bit Zufall sind und kein Passwort — es gibt nichts zu raten, aber jede gelieferte Logzeile muss den Schlüssel prüfen.
- **Der Generalschlüssel ist bewacht:** es gibt nur einen, er lässt sich nicht löschen (nur neu würfeln), optional auf Absenderbereiche (CIDR) eingrenzen und mit einem Ablaufdatum versehen. Jede Benutzung wird mit Zeit und IP vermerkt, jeder Fehlversuch landet im Systemtagebuch.
- **Ein Geräteschlüssel konnte jedes beliebige Gerät abmelden** — samt Logs. `POST /api/agents/decommission` prüft jetzt die Zuständigkeit; beim Abmelden wird der Schlüssel des Geräts entwertet.
- **Jeder angemeldete Benutzer konnte Webhooks anlegen.** Ein Webhook gibt Logdaten *ohne Anmeldung* heraus — der Token in der Adresse genügt. Ein Konto mit reinen Leserechten konnte sich damit einen dauerhaften, offenen Abgriff auf den Logbestand bauen, der auch nach dem Sperren des Kontos weiterlief. Anlegen, Ändern, Neu-Würfeln und Löschen sind jetzt Administratoren vorbehalten; lesen darf die Liste weiterhin jeder Angemeldete.
- **Das eigene Passwort ließ sich ohne Kenntnis des alten ändern.** Eine gültige Sitzung genügte. Wer sich einen Token beschafft hatte — unbeaufsichtigter Rechner, gestohlene Sitzung —, konnte damit das Passwort setzen und den rechtmäßigen Besitzer aussperren. `PUT /api/users/{id}` verlangt für das eigene Konto jetzt `current_password`. Administratoren, die ein *fremdes* Passwort zurücksetzen, brauchen es weiterhin nicht — sie kennen es ja nicht.
- **Nicht-Administratoren konnten `is_active` an ihrem Konto setzen.** Ohne Nutzen für irgendwen, aber ein Supportfall. Rolle und Aktiv-Zustand setzt jetzt nur noch ein Administrator.
- **CORS stand ab Werk auf `*`.** Ohne Eintrag in `CORS_ORIGINS` wird die Middleware gar nicht mehr eingehängt — Oberfläche und API liegen hinter demselben Caddy, also derselben Herkunft.
- `Strict-Transport-Security` wird gesetzt, sobald die Anfrage über HTTPS kam. Auf einer reinen HTTP-Installation bewusst nicht, sonst sperrt man sich selbst aus.
- Ingest, Anmeldung und Abmeldung von Agents sind rate-limitiert.

### Fixed
- **Der Plattenwächter versagte genau ab 95 % — also dann, wenn man ihn braucht.** Die alte Fassung wartete bis 90 % und rief `VACUUM FULL logs`. Das schreibt die Tabelle komplett neu und braucht dafür noch einmal so viel freien Platz, wie sie groß ist; bei 90 % Belegung ist der nicht da. Der Lauf brach ab oder trieb die Belegung sogar über 95 %, und ab dort half nur noch `TRUNCATE` — alle Logs weg. Dazu kam der erste Blick auf die Platte erst fünf Minuten nach dem Start, ein voller Server lief nach einem Neustart also erst einmal weiter voll.

  Neues Modul `app/diskguard.py`. Was sich ändert: Nachsehen im Minutentakt und sofort nach dem Start; Messung wie `df` (`statvfs` mit `f_bavail` statt `used/total` — die alte Rechnung zählte die für root reservierten Blöcke als frei und meldete deshalb zu wenig); Aufräumen ab 80 % statt ab 90 %; Löschen in Häppchen von 50.000 Zeilen mit eigenem Commit statt in einer Riesentransaktion, die das Transaktionslog aufbläht und die Platte während des Aufräumens noch voller macht; gewöhnliches `VACUUM` nach jedem Häppchen und `VACUUM FULL` nur dann, wenn der freie Platz dafür wirklich reicht. Ein automatisches „alles weg“ gibt es nicht mehr: die letzten 24 Stunden bleiben immer stehen (`DISK_MIN_KEEP_HOURS`), und `DISK_ALLOW_TRUNCATE` ist ab Werk aus.
- **Hintergrundaufgaben konnten stillschweigend verschwinden.** `asyncio.create_task()` ohne gehaltene Referenz darf vom Garbage Collector eingesammelt werden — ein Fehler, der sich erst Wochen später als „der Wächter läuft nicht mehr“ zeigt. Die Aufgaben werden jetzt festgehalten.
- `POST /api/settings/retention/execute` löscht ebenfalls häppchenweise statt in einer Transaktion über Millionen Zeilen, und `POST /api/settings/logs/compact` bricht nicht mehr blind los, wenn der Platz für `VACUUM FULL` fehlt.
- Der Systemcheck rechnete die Plattenbelegung mit derselben zu optimistischen Formel wie der alte Wächter und benutzt jetzt dieselbe Messung.

### Added
- **Systemtagebuch** (`app/journal.py`, `GET /api/journal`). Jeder Eingriff wird festgehalten: Update, Rückfall, Sicherung, Aufräumlauf, Container-Aktion, Terminal-Sitzung, Anmeldung und abgewiesene Anmeldung, geänderte Einstellung, erzeugter oder zurückgezogener Schlüssel. Bewusst eine eigene Tabelle `system_events` — ein Aufräumlauf, der `logs` kürzt, darf ausgerechnet den Eintrag „Aufräumlauf hat 4,2 Mio. Zeilen gelöscht“ nicht mitnehmen. `GET /api/journal/stream` liefert neue Einträge live.
- **Live-Ausgabe beim Update** (`GET /api/updates/log/stream`). Ein Fortschrittsbalken sagt „43 %“; er sagt nicht, woran es hängt, und bei einem Fehler sagt er gar nichts. Jetzt läuft mit, was das Wartungsskript auf dem Server tatsächlich ausgibt.
- **Container-Verwaltung** (`app/containers.py`, `/api/containers`). Sortiert jeden Container danach, *wie* er aktualisiert wird: aus dem Quellcode gebaut (`source`) oder fertiges Image (`image`). Die Update-Prüfung fragt die Registry direkt nach dem Abdruck des Tags und vergleicht ihn mit dem lokalen Image — ohne Herunterladen und ohne Zusatzdienst. Eingespielt wird nur auf Klick, mit Sicherungsfrage und Eintrag im Tagebuch.
- **Single Sign-on über Microsoft 365** (`app/sso.py`, `/api/auth/sso/*`). OpenID Connect mit Authorization Code Flow und PKCE, geprüfter ID-Token-Signatur gegen die JWKS des Mandanten, Prüfung von Aussteller, Empfänger, Laufzeit und `nonce`. Bewusst OIDC statt SAML: SAML für eine eigene, nicht gelistete Anwendung verlangt bei Microsoft einen kostenpflichtigen Entra-Plan, eine OIDC-App-Registrierung ist in jedem Microsoft-365-Tarif enthalten. Administratorrechte über Gruppen-Objekt-IDs oder eine App-Rolle, Domänen-Einschränkung möglich. Andere Anbieter (Keycloak, Authentik, Google Workspace, Okta) gehen über dieselbe Einstellung.
- **Open WebUI als KI-Anbieter** (`app/ai.py`). Spricht dieselbe Schnittstelle wie OpenAI, kann dahinter aber ein lokales Modell (Ollama) haben — dann verlässt bei der Auswertung keine Logzeile den Server. `GET /api/ai/models` holt die verfügbaren Modelle, damit der Name nicht geraten werden muss.
- **Der Anzeige-Parser wirkt jetzt auch in der Logliste.** `GET /api/logs?readable=true` liefert je Zeile Zusammenfassung, erkannte Felder und die wichtigsten Angaben als Abzeichen; die Rohzeile kommt unverändert mit. Bis 300 Zeilen pro Seite — darüber kostet das Zerlegen mehr, als es beim Überfliegen bringt.
- **Herkunft und FAQ** (`GET /api/about`): was LogBot ist, woher es kommt, unter welcher Lizenz es steht, wo der Quellcode liegt — und die Fragen, die immer wieder kommen.
- `GET /api/settings/disk/status` und `POST /api/settings/disk/cleanup` machen den Plattenwächter sichtbar und von Hand auslösbar.

### Changed
- **Die Konsole ist ab Werk an** (`LOGBOT_WEBSHELL`, Vorgabe `true`). Der Schalter davor klang vorsichtig, war es aber nicht: Wer die Oberfläche als Administrator erreicht, kann ohnehin Updates einspielen, Container neu bauen und den Host neu starten — also in jedem Fall Code auf diesem Server ausführen. Er hat keinen Angreifer aufgehalten, nur den Betreiber. Jede Sitzung steht mit Benutzer, IP, Uhrzeit und Dauer im Systemtagebuch. Abschalten: `LOGBOT_WEBSHELL=false`.
- Die Fußzeile sagt nicht mehr „All rights reserved“. Das war bei einer MIT-Lizenz schlicht falsch — die Rechte sind eingeräumt, nicht vorbehalten.

## 2026.09.15.20.00.00
### Fixed
- **HTTPS-Agents bekamen die IP von Caddy statt der Geräte-IP** (z. B. `172.18.0.3`). Das Backend hängt hinter Caddy und nahm `request.client.host`. Neue Funktion `client_ip()` in `app/limiter.py`: Sie liest den letzten Eintrag aus `X-Forwarded-For`, den Caddy setzt und den ein Client nicht fälschen kann. Vorrang hat weiterhin eine vom Agent gemeldete `ip_address`.
- **Altbestand wird übernommen, nicht verdoppelt.** Findet der Ingest kein Gerät mit Hostname + echter IP, aber eines mit Hostname + Caddy-IP, bekommt dieser Eintrag die richtige IP und Geräteart. So entsteht keine zweite Karte, und die Logs bleiben am Gerät.
- **Linux-Agents erschienen als „Windows-Agent“.** Ohne `device_type` im Payload und mit untypisiertem Token (z. B. `global-agent`) galt stur `windows_agent`. Jetzt gilt diese Reihenfolge: Payload → Token-Typ → User-Agent (`Python-urllib` = Linux-Agent, `PowerShell` = Windows-Agent) → `unknown`. Eine vom Agent gemeldete Geräteart überschreibt einen gespeicherten falschen Wert.

### Security
- **Rate-Limits griffen für alle Nutzer gemeinsam.** slowapi zählte pro Caddy-IP. Damit konnten zehn Fehlversuche irgendeines Nutzers den Login für alle sperren, und ein Angreifer sah das Limit nur als Summe aller Nutzer. Das Limit zählt jetzt pro echter Client-IP.
## 2026.09.10.20.00.00
### Fixed
- **Der Kanal „stabil" konnte ein Downgrade als Update anbieten.** `compare()` verglich
  installierten und entfernten Stand nur auf *Ungleichheit*, nicht auf *Reihenfolge*.
  Zeigt der Kanal auf ein Release, das älter ist als der installierte Stand — was
  passiert, sobald für den aktuellen Stand noch kein Release angelegt wurde —, meldete
  die Oberfläche „Update verfügbar", und das Einspielen fuhr den Server in Wahrheit
  zurück.
  `compare()` ordnet Versionen jetzt (`parse_version`) und gibt zusätzlich das
  Verhältnis zurück: `behind` (echtes Update), `same`, `ahead` (Server ist neuer als
  der Kanal) oder `unknown`. Bei gleicher Version entscheidet weiterhin der Commit —
  ein weitergelaufener Zweig ohne angehobene VERSION-Datei bleibt damit ein Update.
- `POST /api/updates/apply` weist einen Rückschritt mit HTTP 409 ab und nennt beide
  Auswege (Release anlegen oder Kanal wechseln). Wer den Rückschritt wirklich will,
  setzt `allow_downgrade` oder gibt ein `ref` von Hand an.
- Die Update-Seite zeigt bei diesem Zustand **„Server ist voraus"** statt eines
  Update-Knopfs, dazu eine Erklärung, was zu tun ist. Der Knopf bleibt aus, statt nur
  eine Fehlermeldung zu erzeugen.
- `import re` fehlte in `app/updater.py` — fiel erst beim Test der neuen
  Versionsordnung auf.

## 2026.09.09.22.00.00
### Added
- **Sicherung und Wiederherstellung** (`app/backup.py`, `routes/backup.py`): ZIP-Datei mit
  aussen liegendem, immer lesbarem `manifest.json` und innen liegender Nutzlast — wahlweise
  im Klartext (`payload.zip`) oder AES-256-GCM-verschlüsselt (`payload.bin`, PBKDF2-HMAC-SHA256
  mit 210 000 Runden). Sechs Bereiche einzeln wählbar, beim Sichern wie beim Zurückspielen.
  Tabellen liegen als `jsonl.gz` und werden in Blöcken von 5000 Zeilen gelesen und geschrieben,
  damit die `logs`-Tabelle den Speicher nicht sprengt.
  *Warum das Manifest aussen liegt:* Beim Zurückspielen muss **vor** der Passwortabfrage
  erkennbar sein, von welcher Version die Sicherung stammt — sonst fällt ein Versionskonflikt
  erst auf, wenn die Daten halb in der Datenbank stehen.
- **Versionsprüfung beim Zurückspielen**: gleich / älter / unlesbar / **neuer als der Server**.
  Der letzte Fall blockiert und lässt sich nur ausdrücklich übergehen.
- **Pflicht-Rückfrage vor Systemeingriffen** (`app/guard.py`): Update, Rückfall, Zurückspielen
  und das Schalten eines Zusatzdienstes verlangen im Rumpf ein `backup`-Objekt. Fehlt es,
  gibt es HTTP 400 — die Rückfrage lässt sich also nicht durch Weglassen der Oberfläche
  umgehen. Scheitert die Sicherung, startet der Eingriff nicht.
- **Ereignisverteiler** (`app/events.py`): Server-Sent Events an alle offenen Oberflächen,
  mit gemerktem letztem Stand je Ereignistyp und Lebenszeichen alle 20 s. Bewusst kein
  WebSocket — es fliesst nur in eine Richtung und läuft so durch jeden Reverse Proxy.
- **Update-Beobachter** (`updater.watch_task`): fragt GitHub im kurzen Takt ab und meldet
  einen neuen Stand genau einmal. Dazu **GitHub-Webhook** (`POST /api/updates/webhook`) mit
  HMAC-Prüfung; ohne gesetztes `LOGBOT_WEBHOOK_SECRET` wird nichts angenommen.
- **Release-Kanäle**: `stable`, `edge`, `pinned`. `remote_state()` prüft gegen den Kanal,
  `start_run()` gibt `--ref` an das Wartungsskript weiter.
- **Anzeige-Parser** (`app/logparse.py`): zerlegt Rohzeilen in Zusammenfassung, benannte
  Felder und Abzeichen. RFC 5424/3164, UniFi Netconsole und MAC/Modell, Cisco IOS,
  `key=value`, JSON, journald, Netfilter. Ändert nie Daten — reine Anzeige.
- **App-Schnittstelle** (`routes/mobile.py`) unter `/api/app`: Cursor-Blättern (`before_id`),
  Nachlaufen (`since_id`), kompakte Antworten, `bootstrap` mit `api_level`/`capabilities`.
- **KI-Auswertung** (`app/ai.py`, `routes/ai.py`): Anthropic, OpenAI, n8n extern, n8n lokal.
  Mit Vorschau des Sendeinhalts und Verbindungstest gegen eine erfundene Logzeile.
- **Zusatzdienste** (`app/stacks.py`, `routes/stacks.py`): Compose-Profile schalten, Zugangs-
  daten anzeigen und neu setzen, Protokolle lesen, Kapazität prüfen.
- **Mailversand** (`app/mail.py`, `routes/mail.py`): erzeugt die vollständige
  Postfix-Konfiguration und verschickt über SMTP — Container oder vorhandener Server.
- **Terminal** (`app/shell.py`, `routes/shell.py`): PTY auf dem Host über WebSocket. Nur
  Administratoren, nur mit `LOGBOT_WEBSHELL=true`, mit Sitzungsgrenze, Leerlauf-Abbruch und
  Protokollierung.
- `auth.admin_from_token()` für die beiden Wege ohne Kopfzeile (SSE, WebSocket).
- `GET /api/logs/{id}/parsed` und `agent_id` in der Antwort von `/api/agents/ingest`.

### Changed
- `POST /api/updates/apply` und `/rollback` verlangen zusätzlich zum Bestätigungswort die
  beantwortete Sicherungsfrage; `/apply` nimmt `ref` für ein bestimmtes Release entgegen.
- `POST /api/agents/decommission` ordnet vom Genauesten zum Ungenauesten zu (`agent_id`,
  MAC, Hostname+IP, Hostname) und meldet in `matched_by`, welcher Weg gegriffen hat.
  `all_for_hostname` räumt zusätzlich alle weiteren Einträge desselben Hostnamens ab.

### Fixed
- **Zurückspielen scheiterte an Zeitstempeln.** JSON kennt keinen Datumstyp, asyncpg verlangte
  beim Einfügen aber `datetime`-Objekte. Jeder Wert geht jetzt als Text und wird per
  `CAST(CAST(:x AS text) AS <typ>)` von PostgreSQL umgewandelt — der innere Cast legt den
  Parametertyp fest, der äussere den Zieltyp. ID-Zähler werden danach per `setval` nachgezogen.

## 2026.08.14.12.00.00
### Added
- **Systemcheck** (`app/diagnostics.py`, `routes/diagnostics.py`): prüft das System in einem
  Durchlauf und sagt bei jedem Fund, *was* nicht geht, *woran* das gemessen wurde und *was zu
  tun ist*. 24 Prüfungen in fünf Bereichen:
  - **Kern**: Backend, Datenbankverbindung samt Antwortzeit, vollständiges Schema (Tabellen
    *und* die per Startup-Migration nachgezogenen Spalten), Indizes, Platte, Speicher, CPU.
  - **Dienste**: Weboberfläche, Caddy-Admin-API, Syslog-Empfänger auf 514, Zustand aller
    `logbot-*`-Container (über den Host).
  - **Sicherheit**: `JWT_SECRET` noch auf `change-me`, Standardpasswörter auf Admin-Konten
    (bcrypt-Vergleich gegen `admin`/`password`/`logbot`), HTTPS aktiv, TLS zur externen
    Datenbank.
  - **Betrieb**: Logeingang (Alter des neuesten Eintrags), stille Geräte, gesetzte
    Aufbewahrung, Archivierung (letzter Lauf), LDAP-Erreichbarkeit — beides nur, wenn
    eingeschaltet.
  - **Netz & Updates**: DNS, GitHub, Aktualität, Host-Zugriff.
  Jede Prüfung läuft einzeln abgesichert und mit eigenem Zeitlimit (20 s): fällt eine aus,
  bleibt der Rest des Berichts brauchbar. `GET /api/diagnostics/last` liefert den letzten
  Bericht ohne neue Messung, `POST /api/diagnostics/run` misst neu (nur Admin).
- **Patchmanagement** (`app/updater.py`, `routes/updates.py`, `scripts/logbot-update.sh`):
  - `GET /api/updates/status` vergleicht den installierten Commit bzw. die `VERSION`-Datei mit
    dem Stand auf GitHub (`api.github.com`, Ergebnis 15 Minuten zwischengespeichert — anonym
    sind nur 60 Abfragen pro Stunde erlaubt; `GITHUB_TOKEN` hebt das an).
  - `POST /api/updates/apply` spielt den neuen Stand ein, `POST /api/updates/rollback` fällt auf
    eine Sicherung zurück. Beide verlangen ein Bestätigungswort im Rumpf, damit ein
    versehentlicher Klick nichts auslöst.
  - Das eigentliche Update läuft als Skript auf dem **Host**, nicht im Container — der
    Container kann sich nicht selbst ersetzen. Gestartet wird es über `systemd-run`, damit der
    Lauf den Neubau der Container überlebt (ein per `nsenter` gestarteter Kindprozess läge in
    der Control-Group des Containers und würde mitgetötet).
  - Ablauf: sichern (Dateien + optional `pg_dump`) → `git fetch`/`reset --hard` bzw. frischer
    Clone → `docker compose build && up -d` → warten, bis `/api/health` wieder antwortet.
    Schlägt ein Schritt fehl, spielt das Skript die Sicherung **selbsttätig** zurück.
  - Fortschritt und Ergebnis stehen in `<install>/data/update-state.json`, das ausführliche
    Protokoll in `<install>/data/update.log` (`GET /api/updates/log`). Bewusst Dateien auf dem
    Host und keine Tabelle: während des Updates ist die Datenbank zeitweise weg.
- `app/hostexec.py`: gemeinsamer Helfer für Befehle auf dem Host (`nsenter`), inklusive
  Dateien lesen/schreiben und dem Start langlaufender Vorgänge.
- `VERSION` im Projektwurzelverzeichnis als eindeutiger Versionsstand für den Abgleich.

### Changed
- Neue Umgebungsvariablen (alle mit sinnvollen Vorgaben): `LOGBOT_INSTALL_DIR` (`/opt/logbot`),
  `LOGBOT_REPO_SLUG`, `LOGBOT_BRANCH`, `GITHUB_TOKEN`.

## 2026.08.02.20.00.00
### Fixed
- **Backend startete nicht mehr, jede Anfrage endete mit HTTP 502.** `app/branding.py` lag als
  Windows-1252 statt UTF-8 auf der Platte: das „ü" in „Backend für Whitelabel-System" war ein
  einzelnes Byte `0xFC`. Python liest Quelldateien immer als UTF-8 und brach beim Import ab
  (`SyntaxError: (unicode error) 'utf-8' codec can't decode byte 0xfc`). Der Container lief
  damit in eine Neustartschleife, und Caddy meldete für Oberfläche, Login **und** den
  Agenten-Ingest 502.
  Ursache war ein Reparaturlauf gegen doppelt kodierte Umlaute, der bei dieser Datei aus
  korrektem UTF-8 wieder Windows-1252 gemacht hat. Alle übrigen Quell- und
  Konfigurationsdateien wurden byteweise geprüft und sind gültiges UTF-8.

## 2026.08.02.18.00.00
### Added
- **Passkeys / WebAuthn** (`routes/passkey.py`): Anmeldung mit Windows Hello, Face ID,
  Fingerabdruck oder Sicherheitsschlüssel statt Passwort und Einmalcode.
  - Registrieren (angemeldet): `POST /register/options` → `POST /register/verify`;
    verwalten über `GET/PUT/DELETE /credentials`.
  - Anmelden (offen): `POST /login/options` → `POST /login/verify` liefert das Zugangstoken.
    Ohne Benutzernamen sucht der Browser selbst einen passenden Passkey; ein unbekannter
    Benutzername liefert trotzdem gültige Optionen, damit sich darüber keine Konten ausspähen
    lassen.
  - Herkunft und Domäne für die Signaturprüfung kommen aus `SITE_URL`, ersatzweise aus dem
    `Origin`-Header. Jede Challenge gilt genau einmal und läuft nach 5 Minuten ab.
  - Neue Tabelle `webauthn_credentials` (Startup-Migration und `db/init.sql`), `sign_count`
    wird fortgeschrieben — er entlarvt geklonte Schlüssel.
  - Bewusst **kein** zusätzlicher MFA-Schritt nach dem Passkey: er ist bereits Gerätebesitz
    plus Entsperrung durch PIN oder Biometrie.
- Neue Abhängigkeit: `webauthn`.

### Security
- **Webhook-Abruf ohne Bremse**: `GET /api/webhook/{id}/call` ist ohne Anmeldung erreichbar
  (Token als Parameter) und liefert Logdaten — jetzt auf 60 Aufrufe pro Minute begrenzt.

### Performance
- `GET /api/database/status` schätzt die Zeilenzahl über `pg_class.reltuples`, statt
  `count(*)` über die gesamte `logs`-Tabelle laufen zu lassen (bei Millionen Zeilen ein
  Sekunden-Scan bei jedem Aufruf der Systemzustand-Seite).

## 2026.08.02.16.00.00
### Added
- **Externe Datenbank** (`app/config.py`): neue Umgebungsvariablen `DATABASE_URL` (komplette
  Verbindung, hat Vorrang) und `DB_SSLMODE` (`require`/`verify-ca`/`verify-full`). `postgresql://`
  wird automatisch auf den async-Treiber gehoben, Benutzer/Passwort werden URL-sicher kodiert.
  Der Syslog-Dienst versteht dieselben Variablen (`syslog/syslog_server.py`).
- **`GET /api/database/status`** (Admin): zeigt Verbindungsziel ohne Zugangsdaten, ob die
  Datenbank extern liegt, ob TLS aktiv ist, PostgreSQL-Version, Größe, Verbindungen und
  Antwortzeit. Jede Kennzahl einzeln abgesichert, damit fehlende Rechte auf einer verwalteten
  Datenbank nicht die ganze Auskunft kippen.
- **Anmeldung gegen LDAP / Active Directory** (`app/ldap_auth.py`, `routes/ldap.py`), optional
  und über die Oberfläche einzurichten:
  - Schlägt die lokale Anmeldung fehl, wird zusätzlich das Verzeichnis gefragt. Lokale Konten
    funktionieren unverändert weiter.
  - Passwortprüfung immer über einen **zweiten Bind mit dem DN des Benutzers** — die reine
    Suche beweist gar nichts. Leere Passwörter werden abgelehnt, weil LDAP-Server sie als
    anonyme Anmeldung akzeptieren und Erfolg melden würden.
  - Eingaben werden nach RFC 4515 escaped (sonst ließe sich der Suchfilter umschreiben).
  - Gruppen → Rollen (`admin_group`), optionale Pflichtgruppe, Benutzer werden auf Wunsch beim
    ersten Anmelden angelegt. Neue Spalte `users.auth_source` (`local`/`ldap`) samt
    Startup-Migration; ein bereits vorhandenes **lokales** Konto gleichen Namens wird nie
    übernommen, sonst könnte ein gleichnamiges Verzeichniskonto den lokalen Admin kapern.
  - `POST /api/ldap/test` spielt eine echte Anmeldung durch und zeigt DN, Gruppen und die
    daraus abgeleitete Rolle — auch bevor LDAP scharf geschaltet ist.
- **Archivierung alter Logs** (`app/archiving.py`, `routes/archiving.py`): schreibt Logs älter
  als N Tage als `.ndjson.gz` und überträgt sie per **SFTP, FTPS, FTP, SMB** oder in einen
  eingebundenen Ordner. Zeitplan (täglich zur eingestellten Stunde, Hintergrund-Task ohne
  zusätzlichen Dienst), Verbindungstest mit echter Testdatei, Historie der letzten 20 Läufe.
  Das Löschen nach der Übertragung läuft über die **gemerkten Zeilen-IDs**, nicht über den
  Zeitstempel — sonst könnten zwischenzeitlich eingetroffene Einträge mit altem Datum
  ungesichert verschwinden.

### Changed
- Neue Abhängigkeiten: `ldap3` (LDAP), `paramiko` (SFTP), `smbprotocol` (SMB).

## 2026.08.02.14.00.00
### Security
- **Branding-Endpunkte waren ohne Anmeldung beschreibbar** (`app/branding.py`). `PUT /config`,
  `POST /upload/logo`, `POST /upload/favicon` und `POST /reset` hingen an keiner Prüfung —
  wer den Server erreichte, konnte Farben, Firmenname und vor allem **`custom_css`** setzen,
  das die Oberfläche bei jedem Besucher ungefiltert in ein `<style>`-Element schreibt, und
  beliebige Dateien ablegen. Alle vier verlangen jetzt einen Admin (`get_current_admin`).
- **Pfad-Ausbruch beim Asset-Abruf**: `GET /assets/{filename}` reichte den Namen direkt an
  `os.path.join` weiter — `../../etc/passwd` hätte damit jede lesbare Datei des Containers
  ausgeliefert. Der Name muss jetzt exakt dem Muster der eigenen Upload-Routine entsprechen,
  zusätzlich wird der aufgelöste Pfad gegen den Asset-Ordner geprüft.
- **Uploads ohne Größenbegrenzung** konnten die Platte füllen: jetzt stückweises Schreiben
  mit Abbruch bei 5 MB (`MAX_UPLOAD_BYTES`) und Aufräumen der angefangenen Datei.

### Changed
- Neue Standard-Farbschemata passend zum überarbeiteten Design. Damit bestehende
  Installationen nicht beim alten Aussehen hängen bleiben, hebt `_migrate_legacy_schemes`
  die gespeicherte Konfiguration **nur dann** auf die neuen Werte, wenn dort exakt die alten
  Standardfarben stehen — selbst gewählte Farben bleiben unangetastet.

## 2026.08.02.13.30.00
### Fixed
- **Ingest lehnte große Lieferungen ab.** Die FRITZ!Box liefert ihren kompletten Puffer auf
  einmal — beobachtet wurden 800 Einträge, das Limit lag bei 500 („List should have at most
  500 items"). Obergrenze jetzt **5000**. Zusätzlich stückelt der n8n-Workflow die Einträge
  in Pakete zu 400, damit die Größe des Puffers keine Rolle mehr spielt.

## 2026.07.31.23.30.00
### Added
- **Ingest für Sammler erweitert** (`POST /api/agents/ingest`). Bisher konnte ein Agent nur
  eigene Logs melden — Hostname kam aus dem Payload, die IP war die des Absenders, ein
  Zeitstempel war nicht vorgesehen. Neu sind optional:
  - `ip_address` / `device_type` im Request: ein Sammler (z. B. n8n) meldet die Logs eines
    *anderen* Geräts, das dann mit **eigener IP, eigenem Hostnamen und eigener Geräteart**
    in der Geräteliste steht statt unter der IP des Sammlers.
  - pro Eintrag `timestamp`, `event_id`, `group`, `facility` sowie optionales `level`/`source`
    (bisher Pflicht mit Default). Ohne `timestamp` gilt weiterhin die Empfangszeit.
  - Paketgröße von 50 auf 1000 Einträge angehoben (die FRITZ!Box liefert ~500 auf einmal).
- **Duplikate werden verworfen statt gespeichert.** Einträge mit eigenem `timestamp` bekommen
  einen `dedup_key` (SHA256 aus Hostname, Zeit, Ereignis-ID und Text); der Insert läuft mit
  `ON CONFLICT DO NOTHING` gegen einen partiellen Unique-Index. Damit darf dieselbe Quelle
  beliebig oft ihren kompletten Puffer schicken — gespeichert wird nur, was neu ist. Die
  Antwort enthält jetzt `accepted` **und** `duplicates`. Doppelte innerhalb einer Lieferung
  werden schon vor dem Insert zusammengefasst.
- **Einstufung von FRITZ!Box-Ereignissen** (`app/fritzbox.py`): die Box liefert keinen
  Schweregrad, nur eine Ereignis-ID. Bekannte IDs werden über eine Tabelle eingestuft
  (z. B. 503 „Anmeldung gescheitert" → `warning`, 122 „VPN-Fehler" → `error`, 121
  „VPN getrennt" → `warning`), unbekannte über Stichwörter im Meldungstext. Zusätzlich
  entsteht ein Dienstname aus der Gruppe (`fritzbox-net`, `fritzbox-wlan`, `fritzbox-sys`,
  `fritzbox-auth`, `fritzbox-audit`). Greift nur bei `device_type: "fritzbox"`.
- **Log-Kategorien erweitert** (`routes/logs.py`): die FRITZ!Box-Dienstnamen zählen zu
  „Anmeldung & Rechte", „Netzwerk", „System & Dienste" und „Audit" — die vorhandenen
  Logtyp-Filter greifen also ohne Zusatzarbeit.

## 2026.07.31.23.00.00
### Fixed
- **Reverse Proxy sperrt sich nicht mehr aus.** Die erzeugte Caddyfile leitete Port 80
  bisher komplett auf `https://<FQDN>` um — bei fehlerhaftem Zertifikat oder DNS war die
  Oberfläche danach über *keinen* Weg mehr erreichbar. Jetzt bleibt `:80` in jeder Vorlage
  ein vollwertiger Zugang, IP- und FQDN-Zugriff funktionieren parallel auf 80 und 443.
- **Manuelle Änderungen im Caddyfile-Editor werden nicht mehr verworfen.** `POST /api/caddy/apply`
  baute die Datei aus `mode` neu, sobald ein Modus mitkam — der Editor-Inhalt wurde ignoriert
  („Einstellungen werden falsch übernommen"). Das Frontend schickt jetzt nur noch den Editor-Inhalt.
- **DNS-Prüfung bricht nicht mehr ab.** Ein im Container nicht auflösbarer FQDN führte zu
  HTTP 400; jetzt ist es eine Warnung im Ergebnis (der Container-Resolver ist oft ein anderer
  als der im restlichen Netz).

### Added
- **Vorab-Validierung**: jede Konfiguration geht zuerst durch Caddys `/adapt` — Syntaxfehler
  werden gemeldet, ohne den laufenden Zustand anzufassen.
- **Automatischer Rollback**: schlägt `/load` fehl oder antwortet Caddy danach nicht mehr,
  wird die zuvor laufende Konfiguration wiederhergestellt.
- **Modus `internal`**: Zertifikat aus Caddys eigener CA — HTTPS ohne Internet und ohne
  Let's Encrypt (Browser warnt einmalig).
- **`extra_hosts`**: zusätzliche Adressen (IPs oder interne Namen), die per HTTPS erreichbar
  sein sollen; erhalten ein internes Zertifikat, da es für IPs keine öffentlichen gibt.
- **`POST /api/caddy/reset`**: Notausstieg zurück auf reines HTTP.
- Startet die App mit einer gespeicherten Konfiguration, die nicht lädt, wird jetzt die
  HTTP-Grundkonfiguration geladen statt gar nichts.
- Zertifikat-Upload prüft auf PEM-Format und setzt `chmod 600` auf den Key.
- **`CADDY_FORCE_HTTP=true`** (Umgebungsvariable): Notausstieg ohne Web-UI — beim Start wird
  auf reines HTTP zurückgesetzt und die gespeicherte Konfiguration verworfen. Für den Fall,
  dass TLS klemmt und man deshalb gar nicht mehr an die Oberfläche kommt.

## 2026.07.31.22.10.00
### Added
- **Filter nach Logtypen** für `GET /api/logs` und den Export (`routes/logs.py`):
  - `min_severity` — Schweregrad-Gruppe, z. B. `warning` = Warnungen *und alles Dringendere*.
    Abbildung über `SEVERITY_ORDER` inkl. Kurzformen (`err`, `warn`, `crit`, `emerg` …).
  - `category` — fachliche Kategorien (`auth`, `kernel`, `network`, `firewall`, `container`,
    `cron`, `mail`, `system`, `audit`); Treffer über Syslog-Facility **oder** Dienstnamen.
  - `facility` — Syslog-Facility 0–23.
  - `device_type` — Geräteart des sendenden Systems (Subquery über `agents`, nutzt `idx_logs_agent_id`).
- `GET /api/logs/filter-options` liefert zusätzlich `severities`, `categories`, `facilities`
  und `device_types`. Die Logtyp-Listen sind fest hinterlegt — ein `DISTINCT` über die
  Millionen Zeilen der `logs`-Tabelle wäre dafür zu teuer.
- Startup-Migration `ensure_log_indexes` legt `idx_logs_facility` an (`CREATE INDEX
  CONCURRENTLY`, blockiert also keine Schreibzugriffe); für Neuinstallationen auch in `db/init.sql`.

### Changed
- `hostname_exact` vergleicht per `ilike` ohne Wildcards statt `lower(hostname) = …` —
  gleiches Ergebnis, nutzt aber den vorhandenen Trigramm-Index.

## 2026.07.31.21.10.00
### Added
- **`GET /api/logs/export`** (`routes/logs.py`): Export der aktuell gefilterten Logs als
  **CSV** oder **JSON**, gestreamt (kein Vollpuffer im RAM), `limit` bis 500.000 Zeilen.
  Der Endpoint fehlte bisher komplett — die Export-Buttons im Frontend liefen ins Leere.
  Er steht bewusst **vor** `GET /api/logs/{log_id}`, sonst schluckt die ID-Route den Pfad.
- **`hostname_exact`**-Parameter für `GET /api/logs` und den Export: vergleicht den Hostnamen
  exakt (case-insensitiv) statt als Teilstring — die Geräte-Ansicht braucht das, damit
  z. B. `srv1` nicht auch die Logs von `srv10` mitzieht.

### Security
- CSV-Export entschärft Werte, die mit `=`, `+`, `-` oder `@` beginnen (Formel-Injection in
  Excel/LibreOffice), und schreibt ein UTF-8-BOM für korrekte Umlaute in Excel.

### Hinweis
- Der Streaming-Export öffnet bewusst eine **eigene** DB-Session: ab FastAPI 0.106 ist die per
  `Depends` injizierte Session beendet, bevor der Response-Body gesendet wird.

## 2026.07.18.16.00.00
### Changed
- **Ingest `device_type` dynamisch aus dem Agent-Token** (`/api/agents/ingest`): `linux` → `linux_agent`, `windows` → `windows_agent`, sonst Bestandsverhalten `windows_agent`. Vorher hart `windows_agent` – Linux-HTTPS-Agenten erschienen dadurch fälschlich als Windows. Für korrekte Anzeige ein Token mit Typ `linux` verwenden.

## 2026.07.11.13.03.42
### Added
- **Netzwerk-DNS-Verwaltung** (`routes/network.py`): `GET/PUT /api/network/dns` und `POST /api/network/dns/test`. Standard = per DHCP vergebener System-DNS des Hosts (gelesen aus `/proc/1/root/.../resolv.conf`), optional eigene Server + Such-Domains; wird auf `/etc/resolv.conf` des Backend-Containers angewendet und beim Start erneut gesetzt.

### Changed
- Behebt „FQDN nicht auflösbar" im Reverse-Proxy-Check auf jedem Netz, ohne hartes `extra_hosts` in `docker-compose.yml` (entfernt).

## 2026.05.30.18.02.35
### Added
- Ausgangsbasis dieses Changelogs. Aktueller Funktionsstand u.a.: HTTPS-Log-Ingest (`/api/agents/ingest`), Agent- & Token-Verwaltung, MFA/TOTP, Caddy-Management (Reverse Proxy & TLS), Disk-Monitoring und Retention-Housekeeping.

_Änderungen vor Einführung des Changelogs wurden nicht einzeln erfasst._
