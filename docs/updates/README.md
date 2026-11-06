# Updates

Alles zum Aktualisieren: Einzeiler, Kanäle, Sofortmeldung, Ablauf, Rückfall,
Fehlersuche. Wer nur schnell den neuesten Stand drüberbügeln will, braucht die
[erste Tabelle](#einzeiler-auf-einen-blick) — der Rest erklärt, was dabei
passiert.

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

## Einzeiler auf einen Blick

Alle Befehle laufen **auf dem Server**, als root. `RAW` steht dabei für
`https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main`.

| Was | Einzeiler |
|-----|-----------|
| **Aktuellsten Stand drüberbügeln** — mit Sicherung, Gesundheitsprüfung und selbsttätigem Rückfall *(empfohlen)* | `curl -sSL RAW/server/backend/scripts/rimperio-update.sh \| sudo bash -s -- apply --dir /opt/rimperio` |
| Dasselbe aus der Installation heraus | `sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh apply` |
| **Über den Installer** — aktualisiert und ergänzt dabei fehlende `.env`-Schlüssel | `curl -sSL RAW/install.sh \| sudo bash -s -- update -y` |
| Auf ein **bestimmtes Release** | `sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh apply --ref v2026.09.10` |
| Bestimmtes Release über den Installer | `curl -sSL RAW/install.sh \| sudo bash -s -- update -y --ref v2026.09.10` |
| Ohne Datenbank-Abzug *(schneller, riskanter)* | `sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh apply --no-db-backup` |
| **Zurückfallen** auf die letzte Sicherung | `sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh rollback` |
| Auf eine bestimmte Sicherung | `sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh rollback --backup 20260910-120000` |
| Stand des letzten Laufs | `sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh status` |
| Protokoll mitlesen | `sudo tail -f /opt/rimperio/data/update.log` |
| **Rohe Gewalt** — ohne Sicherung, ohne Netz | `cd /opt/rimperio && sudo git fetch --all --tags --prune && sudo git reset --hard origin/main && sudo docker compose build --pull && sudo docker compose up -d --remove-orphans` |
| Nur neu bauen und starten *(nach Änderungen an der `.env`)* | `cd /opt/rimperio && sudo docker compose up -d --build --remove-orphans` |
| Vorher prüfen, ob die Maschine reicht | `curl -sSL RAW/server/install/preflight.sh \| sudo bash` |
| PostgreSQL-Major-Upgrade **mit** Datenerhalt | `curl -sSL RAW/db/migrate.sh \| sudo bash` |
| Linux-Agent aktualisieren *(Installer drüber — FQDN und Token wieder mitgeben)* | `curl -sSL RAW/agent/logship/install-linux.sh \| sudo bash -s -- --fqdn rimperio.example.com --token DEIN-TOKEN --yes` |

> **Den ersten Einzeiler holt sich das Wartungsskript frisch von GitHub.** Das
> ist der Weg für Installationen, die so alt sind, dass sie das Skript noch
> nicht mitbringen. Liegt es schon da, ist der lokale Aufruf identisch — nur
> ohne Netzabhängigkeit.

---

## Welchen Weg nehmen?

| | Oberfläche | Wartungsskript | Installer | Rohe Gewalt |
|---|---|---|---|---|
| Aufruf | *System → Updates* | `rimperio-update.sh apply` | `install.sh update` | `git reset --hard` + `compose up` |
| Sicherung vorher | ja, samt Rückfrage | ja (Dateien + `pg_dump`) | **nein** | **nein** |
| Rückfall bei Fehlschlag | selbsttätig | selbsttätig | nein | nein |
| Gesundheitsprüfung danach | ja | ja | nur „Container laufen“ | nein |
| Bestimmtes Release | ja (Kanal *Festgelegt*) | `--ref` | `--ref` | selbst auschecken |
| Ergänzt fehlende `.env`-Schlüssel | nein | nein | **ja** | nein |
| Zusatzdienste zuschalten | *System → Zusatzdienste* | nein | `--with …` | nein |
| Braucht Host-Zugriff des Backends | **ja** | nein | nein | nein |

**Kurz:** Im Regelbetrieb die Oberfläche oder das Wartungsskript. Der Installer
ist der Weg nach einem größeren Sprung — er zieht die `.env` nach, wenn neue
Schlüssel dazugekommen sind. Rohe Gewalt nur, wenn beides nicht mehr will und
eine eigene Sicherung existiert.

---

## Die Einzeiler im Detail

### Aktuellsten Stand drüberbügeln

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/server/backend/scripts/rimperio-update.sh \
  | sudo bash -s -- apply --dir /opt/rimperio
```

Ohne `--ref` ist das Ziel der Kopf von `main` — also wirklich der letzte Stand,
unabhängig davon, welcher [Kanal](#welchen-stand-soll-der-server-fahren) in der
Oberfläche eingestellt ist. Der Kanal gilt für die Oberfläche, nicht für die
Kommandozeile.

Was der Lauf tut, steht unter [Ablauf eines Updates](#ablauf-eines-updates):
sichern, holen, bauen, prüfen — und bei jedem Fehlschlag zurückfallen.

### Über den Installer

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/install.sh \
  | sudo bash -s -- update -y
```

Der Installer holt den neuen Stand per `git pull --ff-only`; geht das nicht
(lokale Änderungen, abgekoppelter `HEAD` nach einem `--ref`-Lauf, kein
Git-Verzeichnis), klont er frisch und kopiert die Dateien darüber. `.env` und
`data/` bleiben in beiden Fällen unangetastet, fehlende `.env`-Schlüssel kommen
dazu. Danach `docker compose build` und `up -d --remove-orphans`.

Er sichert **nicht** und fällt **nicht** zurück. Wer das will, nimmt das
Wartungsskript oder sichert vorher selbst unter *System → Sicherung*.

Zusatzdienste bleiben beim Update unberührt, solange man sie nicht ausdrücklich
nennt (`--with portainer,n8n`) — sonst schaltete ein Update stillschweigend
Dienste zu oder ab.

### Ein bestimmtes Release

Über das Wartungsskript:

```bash
sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh apply --ref v2026.09.10
```

Über den Installer:

```bash
curl -sSL RAW/install.sh | sudo bash -s -- update -y --ref v2026.09.10
```

`--ref` nimmt ein Release, ein Tag, einen Zweig oder einen Commit. Aufgelöst
wird in dieser Reihenfolge: **Tag**, dann **Zweig auf dem Server**, dann roher
**Commit** — damit ein Tag, der wie ein Zweig heißt, nicht zufällig gewinnt.

Danach steht die Installation auf einem abgekoppelten `HEAD`. Das ist gewollt
(genau dieser Punkt soll laufen), hat aber eine Folge: Der nächste Lauf **ohne**
`--ref` zieht den Server wieder auf den Zweigkopf. Wer dauerhaft auf einem
Release bleiben will, stellt in der Oberfläche den Kanal *Festgelegt* ein — die
Einstellung liegt in der Datenbank und übersteht ein Update.

### Rohe Gewalt

```bash
cd /opt/rimperio \
  && sudo git fetch --all --tags --prune \
  && sudo git reset --hard origin/main \
  && sudo docker compose build --pull \
  && sudo docker compose up -d --remove-orphans
```

Kein Backup, kein Rückfall, keine Gesundheitsprüfung: Was hier schiefgeht,
bleibt schief. `reset --hard` verwirft außerdem **jede lokale Änderung** an
Dateien im Repository (`.env` und `data/` liegen nicht darin und bleiben).
Gedacht für den Fall, dass die anderen Wege klemmen — und für Testmaschinen.

### Stand und Protokoll ansehen

Zustandsdatei als JSON:

```bash
sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh status
```

Protokoll des Laufs:

```bash
sudo tail -f /opt/rimperio/data/update.log
```

Laufen alle Container?

```bash
cd /opt/rimperio && sudo docker compose ps
```

Welcher Stand liegt hier?

```bash
cat /opt/rimperio/VERSION
```

Antwortet die Anwendung?

```bash
curl -s http://127.0.0.1/api/health
```

---

## Was „aktuell“ überhaupt heißt

Verglichen wird zweierlei, in dieser Reihenfolge:

1. **Die Datei `VERSION`** im Wurzelverzeichnis — hier und auf GitHub. Format
   `YYYY.MM.DD.HH.MM.SS`; verglichen werden die ersten sechs Zahlen, ein Zusatz
   wie `-rc1` kippt den Vergleich also nicht.
2. **Der Commit**, falls die Versionen gleich oder unlesbar sind. So fällt auch
   auf, wenn der Zweig weitergelaufen ist, ohne dass `VERSION` angehoben wurde.

Daraus wird ein Verhältnis:

| Verhältnis | Bedeutung | Was die Oberfläche zeigt |
|---|---|---|
| `behind` | Auf GitHub liegt Neueres | „Update verfügbar“ samt Knopf |
| `same` | Gleichstand | nichts zu tun |
| `ahead` | Der installierte Stand ist **neuer** als das Ziel des Kanals | Hinweis statt Knopf — Einspielen wäre ein Rückschritt |
| `unknown` | Kein Vergleich möglich (GitHub nicht erreichbar, `VERSION` unlesbar) | die Begründung |

`ahead` ist kein Fehler, sondern der häufige Fall „Release noch nicht angelegt“:
Der Kanal *Stabil* zeigt auf das letzte Release, das kann älter sein als der
gerade laufende Stand. Ein Einspielen wäre dann ein als Update getarntes
Downgrade. Wer es trotzdem will, setzt im API-Aufruf `allow_downgrade` oder gibt
ein `ref` an.

**Abfragekontingent:** GitHub erlaubt anonym 60 Anfragen pro Stunde, deshalb
wird die Antwort 900 Sekunden zwischengespeichert (`UPDATE_CHECK_CACHE_SECONDS`).
Fehlschläge nur 60 Sekunden — damit „Erneut prüfen“ etwas bewirkt. Wem das
Kontingent nicht reicht, setzt `GITHUB_TOKEN` in der `.env`.

---

## Welchen Stand soll der Server fahren?

Nicht jeder will immer den letzten Commit. Drei Möglichkeiten, einzustellen
unter *System → Updates*:

| Kanal | Bedeutung | Für wen | Ziel auf der Kommandozeile |
|-------|-----------|---------|----------------------------|
| **Stabil** | das zuletzt veröffentlichte Release (kein Prerelease) | Regelbetrieb — Voreinstellung | `--ref <Release-Tag>` |
| **Aktuell** | der Kopf des Zweiges, jeder Push | Test- und Entwicklungsserver | ohne `--ref` |
| **Festgelegt** | genau ein Release, Tag oder Commit | wenn nichts mehr wackeln darf | `--ref <Punkt>` |

Die Wahl steht in der Datenbank und übersteht ein Update. Die Prüfung richtet
sich danach: Wer auf **Stabil** steht, bekommt keine Meldung, weil jemand einen
Zwischenstand gepusht hat.

Zur Auswahl stehen die Releases des Repositories; gibt es keine, werden die Tags
gelistet. Gibt es auch die nicht, bleibt der Zweig die einzige Wahl.

---

## Sofortmeldung

Der Wunsch dahinter: *Sobald auf GitHub etwas gepusht wird, sollen alle
laufenden Server das mitbekommen — nicht erst, wenn jemand zufällig die
Update-Seite öffnet.*

Zwei Wege führen dahin. Beide enden im selben Verteiler, von dort geht der
Hinweis per Server-Sent Events in jedes offene Browser-Fenster:

### 1. Eigener Beobachter *(läuft von selbst)*

Der Server sieht alle 120 Sekunden bei GitHub nach. Erst wenn wirklich etwas
Neues da ist, gibt es eine Meldung — nicht bei jeder Abfrage. Ist der Server
wieder gleichauf (etwa nach dem Update), wird der Hinweis von selbst
zurückgezogen.

In die `.env` eintragen:

```
RIMPERIO_UPDATE_WATCH=true
RIMPERIO_UPDATE_WATCH_INTERVAL=120
```

Das ist der Weg für jeden Server hinter einer Firewall — also für die meisten.

### 2. GitHub-Webhook *(wirklich sofort)*

Ist der Server aus dem Internet erreichbar, meldet GitHub den Push direkt.

1. In der `.env` ein Secret setzen *(der Installer würfelt eines aus)*:
   ```
   RIMPERIO_WEBHOOK_SECRET=<langer Zufallswert>
   ```
2. Im Repository unter **Settings → Webhooks → Add webhook**:
   - **Payload URL:** `https://dein-rimperio.example.com/api/updates/webhook`
   - **Content type:** `application/json`
   - **Secret:** derselbe Wert
   - **Events:** *Just the push event*

Ohne gesetztes Secret nimmt der Server **gar keine** Webhook-Meldung an — ein
offener Endpunkt wäre eine Einladung, den Server im Takt Anfragen an GitHub
schicken zu lassen. Die Signatur (`X-Hub-Signature-256`) wird bei jeder Meldung
geprüft; ausgewertet werden `push`, `release` und `create`, auf `ping` antwortet
der Server mit einem Pong.

Angezeigt wird der Hinweis als Pille oben rechts in der Kopfleiste, in jedem
offenen Fenster, ohne Neuladen.

---

## Update über die Oberfläche

*System → Updates → **Update einspielen***

Davor stehen zwei Sperren:

1. **Ein Hinweis**, was passiert und was es kosten kann — mit Häkchen zum
   Bestätigen. Gegen den versehentlichen Klick.
2. **Die Sicherungsfrage.** Vorher sichern — ja oder nein? Ohne beantwortete
   Frage weist der Server den Aufruf ab. Auch das „Nein“ muss man wählen.
   Siehe [Sicherung](../backup/README.md).

> Der Lauf startet über `systemd-run` als eigene Unit — nicht als Kindprozess
> des Containers. Nötig, weil er die Container neu baut, inklusive des Backends,
> das ihn angestoßen hat: ein Kindprozess stürbe dabei mitten im Update. Ohne
> systemd weicht das Backend auf `setsid`+`nohup` aus und sagt das in der
> Rückmeldung.
>
> Das Wartungsskript wird vor **jedem** Lauf frisch aus dem Backend-Image nach
> `/opt/rimperio/scripts/rimperio-update.sh` geschrieben. So passen Skript und
> Backend immer zusammen, und auch eine Installation, die das Skript noch nicht
> kennt, lässt sich über die Oberfläche aktualisieren.

---

## Ablauf eines Updates

Genau diese Schritte laufen — über die Oberfläche wie auf der Kommandozeile,
denn beides startet dasselbe Skript:

| Fortschritt | Schritt | Was passiert |
|---:|---|---|
| 5 % | Vorbereitung | root? Verzeichnis da? `docker`, Compose-Plugin, `git` vorhanden? |
| 15 % | Sicherung | Dateien nach `/opt/rimperio-backups/<Zeitstempel>/files/`, dazu `backup.json`; auf Wunsch ein `pg_dump --clean --if-exists`, gzip-gepackt |
| 40 % | Neuer Stand | `git fetch --tags` + `reset --hard` auf Tag, Zweig oder Commit; klemmt das, ein frischer Clone, dessen Dateien darüberkopiert werden |
| 60 % | Container | `docker compose build --pull` *(ohne `--pull`, falls das scheitert)*, dann `up -d --remove-orphans` |
| 85 % | Kontrolle | wartet bis zu 420 s darauf, dass `http://127.0.0.1/api/health` antwortet |
| 100 % | Fertig | alte Sicherungen über der Aufbewahrungsgrenze werden entfernt |

**Schlägt Schritt 3, 4 oder 5 fehl, spielt das Skript die Sicherung selbsttätig
zurück** — Dateien, Neubau, Start und, falls vorhanden, der Datenbank-Abzug.
Das Ergebnis steht in beiden Fällen in der Zustandsdatei.

Über die Oberfläche startet kein zweiter Lauf, solange einer läuft (`status:
running` in der Zustandsdatei) — auf der Kommandozeile gibt es diese Sperre
nicht. Zwei gleichzeitige Läufe wären fatal: erst in die Zustandsdatei sehen.

---

## Was auf der Platte passiert

| Pfad | Inhalt |
|------|--------|
| `/opt/rimperio` | die Installation (`RIMPERIO_INSTALL_DIR`) |
| `/opt/rimperio/.env` | Konfiguration — wird **nie** überschrieben |
| `/opt/rimperio/data/` | Laufzeitdaten, liegt nicht im Repository und wird nicht mitgesichert |
| `/opt/rimperio/data/update-state.json` | Fortschritt und Ergebnis des letzten Laufs; die Oberfläche liest das |
| `/opt/rimperio/data/update.log` | ausführliches Protokoll aller Läufe |
| `/opt/rimperio/scripts/rimperio-update.sh` | das Skript, wie es das Backend auf den Host geschrieben hat |
| `/opt/rimperio/server/backend/scripts/rimperio-update.sh` | dasselbe Skript als Teil des Repositories |
| `/opt/rimperio-backups/<Zeitstempel>/files/` | die gesicherten Dateien |
| `/opt/rimperio-backups/<Zeitstempel>/database.sql.gz` | der Datenbank-Abzug, falls angelegt |
| `/opt/rimperio-backups/<Zeitstempel>/backup.json` | Version, Commit, Zweig, Ziel, ob ein Abzug dabei ist |

Die Logs selbst liegen in einem Docker-Volume, nicht in diesen Verzeichnissen —
sie überstehen einen Neubau der Container.

---

## Schalter des Wartungsskripts

```bash
sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh {apply|rollback|status} [optionen]
```

| Option | Bedeutung | Vorgabe |
|--------|-----------|---------|
| `--dir <pfad>` | Installationsverzeichnis | `/opt/rimperio` |
| `--repo <url>` | Repository (für Forks) | `https://github.com/Phydran6/RIMPERIO-Server.git` |
| `--branch <name>` | Zweig | `main` |
| `--ref <punkt>` | Release, Tag, Zweig oder Commit statt des Zweigkopfes | leer |
| `--backup <name>` | beim Rückfall: welche Sicherung | die neueste |
| `--db-backup` / `--no-db-backup` | Datenbank-Abzug vor dem Update | an |

Dazu drei Umgebungsvariablen, die nur für den Lauf gelten:

| Variable | Bedeutung | Vorgabe |
|----------|-----------|---------|
| `RIMPERIO_HEALTH_TIMEOUT` | Sekunden, die die Anwendung zum Wiederkommen hat | 420 |
| `RIMPERIO_DUMP_TIMEOUT` | Sekunden für den `pg_dump` | 3600 |
| `RIMPERIO_KEEP_BACKUPS` | wie viele Sicherungen in `<install>-backups` bleiben | 5 |

```bash
sudo RIMPERIO_HEALTH_TIMEOUT=900 bash /opt/rimperio/server/backend/scripts/rimperio-update.sh apply
```

> Diese drei kommen aus der **Umgebung des Laufs**, nicht aus der `.env`. Beim
> Update über die Oberfläche gelten daher die Vorgaben. Und Achtung:
> `RIMPERIO_KEEP_BACKUPS` in der `.env` steuert etwas anderes — die Anzahl der
> [ZIP-Sicherungen](../backup/README.md) unter *System → Sicherung*.

---

## Einstellungen in der `.env`

| Variable | Bedeutung | Vorgabe |
|----------|-----------|---------|
| `RIMPERIO_INSTALL_DIR` | wo die Installation auf dem **Host** liegt. Stimmt das nicht, findet das Update die `docker-compose.yml` nicht | `/opt/rimperio` |
| `RIMPERIO_REPO_SLUG` | Repository, gegen das geprüft wird (Forks) | `Phydran6/RIMPERIO-Server` |
| `RIMPERIO_BRANCH` | Zweig | `main` |
| `GITHUB_TOKEN` | hebt das Abfragekontingent von 60/h an | leer |
| `RIMPERIO_UPDATE_WATCH` | eigener Beobachter an/aus | `true` |
| `RIMPERIO_UPDATE_WATCH_INTERVAL` | Takt in Sekunden (Untergrenze 30) | 120 |
| `RIMPERIO_WEBHOOK_SECRET` | Secret für den GitHub-Webhook. Ohne ihn nimmt der Server keine Meldung an | leer |

Nach Änderungen an der `.env` das Backend neu starten:
`cd /opt/rimperio && sudo docker compose up -d`.

Zwei Werte kennt das Backend zwar, aber die `docker-compose.yml` reicht sie
nicht durch — wer sie braucht, trägt sie dort beim Backend unter `environment`
ein: `UPDATE_CHECK_CACHE_SECONDS` (Haltbarkeit der GitHub-Antwort, Vorgabe 900 s)
und `RIMPERIO_REPO` (Klon-URL; sie wird sonst aus `RIMPERIO_REPO_SLUG` gebildet).

---

## Über die API

Alles unter `/api/updates` ist Administratoren vorbehalten — mit einer Ausnahme:
der Webhook weist sich per HMAC-Signatur aus.

| Endpunkt | Zweck |
|----------|-------|
| `GET /api/updates/status` | installierter Stand, Stand auf GitHub, laufender Vorgang, Sicherungen, passender Einzeiler |
| `POST /api/updates/check` | GitHub sofort erneut abfragen (umgeht den Zwischenspeicher) |
| `GET /api/updates/log?lines=200` | die letzten Zeilen des Wartungsprotokolls |
| `GET /api/updates/releases` | Releases bzw. Tags samt Kanal-Auswahl |
| `PUT /api/updates/channel` | Kanal setzen (`stable` \| `edge` \| `pinned`) |
| `GET /api/updates/stream?token=…` | Server-Sent Events für die Sofortmeldung |
| `POST /api/updates/webhook` | Push-Meldung von GitHub (HMAC-signiert) |
| `POST /api/updates/apply` | Update starten — `confirm: "UPDATE"` und beantwortete Sicherungsfrage |
| `POST /api/updates/rollback` | Rückfall starten — `confirm: "ROLLBACK"` und beantwortete Sicherungsfrage |

Beispiel, Stand abfragen:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://rimperio.example.com/api/updates/status
```

Einzelheiten: [API](../api/README.md) und `/api/docs` am laufenden Server.

---

## Zurückfallen

*System → Updates → **Auf vorherigen Stand zurück***

Zeigt die Sicherungen des Wartungsskripts (Dateien, optional Datenbankabzug).
Auch hier läuft vorher die Sicherungsfrage. Auf der Kommandozeile:

Auf die letzte Sicherung zurück:

```bash
sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh rollback
```

Auf eine bestimmte Sicherung zurück:

```bash
sudo bash /opt/rimperio/server/backend/scripts/rimperio-update.sh rollback --backup 20260910-120000
```

Welche Sicherungen gibt es?

```bash
ls -1 /opt/rimperio-backups
```

> **Achtung:** Enthält die gewählte Sicherung einen Datenbankabzug, wird er
> eingespielt — **alle Logs seit dieser Sicherung sind dann weg.** Die
> Oberfläche sagt das für jede Sicherung einzeln an.

Nicht zu verwechseln mit den [ZIP-Sicherungen](../backup/README.md): Die
gehören zum Datenbestand und lassen sich granular zurückspielen. Der Rückfall
hier setzt den ganzen Stand auf einen früheren Zeitpunkt.

---

## Zum Datenbestand

Die Logs liegen in einem eigenen Docker-Volume und überstehen ein Update
normalerweise unbeschadet. Verlassen sollte man sich darauf nicht: Ändert sich
das Datenbankschema oder bricht der Lauf ab, können Logs verloren gehen.

Deshalb die Sicherungsfrage vor jedem Eingriff — und deshalb lässt sie sich
nicht wegklicken, sondern nur beantworten.

---

## Wenn das Update über die Oberfläche fehlt

Läuft RIMPERIO mit `server/deploy/hardened.yml`, hat das Backend keinen Zugriff auf den
Host (`privileged: false`, kein `pid: host`) — dann geht das Update nur über die
Kommandozeile. Der Systemcheck sagt das ausdrücklich an, und die Update-Seite
zeigt statt des Knopfes den Einzeiler zum Kopieren.

Dasselbe gilt, wenn `nsenter` fehlt oder `RIMPERIO_INSTALL_DIR` auf ein
Verzeichnis zeigt, in dem keine `docker-compose.yml` liegt.

---

## PostgreSQL-Major-Upgrade

Ein Image-Tausch reicht **nicht** — Datenverzeichnisse sind zwischen
Major-Versionen nicht kompatibel. Der Weg mit Datenerhalt:

Ziel = POSTGRES_VERSION aus der .env:

```bash
sudo bash /opt/rimperio/db/migrate.sh
```

Ziel-Major und ohne Rückfrage:

```bash
sudo bash /opt/rimperio/db/migrate.sh 18 -y
```

Einzelheiten: [Datenbank](../../server/db/README.md).

Notbremse, falls schon aktualisiert und die Datenbank nicht mehr startet:

In der `.env` auf PostgreSQL 16 zurückstellen:

```bash
echo "POSTGRES_VERSION=16" >> /opt/rimperio/.env
```

Neu starten:

```bash
cd /opt/rimperio && sudo docker compose up -d
```

---

## Agents und Zusatzdienste

**Agents** aktualisieren sich nicht selbst. Der Installer-Einzeiler drüber
genügt — er konfiguriert die vorhandene Installation neu, **FQDN und Token
gehören also wieder dazu** (unbeaufsichtigt übernimmt er keine alten Werte):

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh \
  | sudo bash -s -- --fqdn rimperio.example.com --token DEIN-AGENT-TOKEN --yes
```

Ohne `--yes` fragt der Installer nach und bietet an, die vorhandene Installation
neu zu konfigurieren. Der bestehende Token steht in
`/opt/rimperio-agent/config.json`. Windows und alle Optionen:
[Agents](../../agent/logship/README.md).

### Was diese Seite *nicht* aktualisiert

Nur die Container, die aus diesem Quellcode gebaut werden: `rimperio-app-backend`,
`-frontend` und `-syslog`. Die fremden Images — PostgreSQL, Caddy, n8n,
Portainer, Open WebUI, Postfix — brauchen ihre eigenen Updates. Die laufen
unter *System → Container*; dort steht auch, für welches gerade etwas
bereitliegt.

Das ist bewusst getrennt: Ein Update von RIMPERIO holt neuen Quellcode und baut
Container neu. Ein Image-Update tauscht ein fertiges Image aus. Zwei
verschiedene Vorgänge mit zwei verschiedenen Risiken — sie in einen Knopf zu
legen, würde nur verwischen, was gerade passiert.

---

## Zusehen, was passiert

Während ein Lauf arbeitet, zeigt die Seite nicht nur einen Fortschrittsbalken,
sondern **die Ausgabe des Wartungsskripts, Zeile für Zeile**, so wie sie auf dem
Server entsteht.

Der Grund ist einfach: Ein Balken sagt „43 %“. Er sagt nicht, woran es gerade
hängt, und wenn etwas schiefgeht, sagt er gar nichts. Wer `docker compose build`
durchlaufen sieht, weiß im Fehlerfall sofort, wo es klemmte, statt hinterher zu
raten.

Bricht die Verbindung ab, ist das normal — der Container, der die Seite
ausliefert, wird ja gerade neu gebaut. Der Browser verbindet sich von selbst
wieder, und die Ausgabe läuft weiter.

Danach steht der ganze Vorgang dauerhaft im
[Systemtagebuch](../operate/README.md#systemtagebuch): wer das Update
angestoßen hat, von welcher Version auf welche, ob vorher gesichert wurde und
wie es ausging.

---

## Fehlersuche

| Meldung / Symptom | Ursache | Abhilfe |
|---|---|---|
| „Kein Zugriff auf den Host“ | Backend ohne `privileged`/`pid: host` (z. B. `hardened.yml`), oder `nsenter` fehlt | Update per Einzeiler auf dem Host |
| „GitHub hat die Anfrage abgelehnt (Limit …)“ | 60 anonyme Abfragen pro Stunde erschöpft | `GITHUB_TOKEN` in die `.env`, Backend neu starten |
| „Der installierte Stand ist NEUER als …“ | Kanal *Stabil*, aber für den laufenden Stand gibt es kein Release | Release anlegen, Kanal auf *Aktuell* stellen, oder `--ref` angeben |
| „Es läuft bereits ein Wartungsvorgang“ | `status: running` in der Zustandsdatei | `update.log` ansehen; nach einem abgestürzten Lauf `data/update-state.json` entfernen |
| `git pull fehlgeschlagen` | lokale Änderungen oder abgekoppelter `HEAD` nach `--ref` | Installer kopiert dann selbst; sauber: `git reset --hard origin/main` |
| „Nach dem Update hat die Oberfläche nicht geantwortet“ | Build in Ordnung, Start nicht — oder der Server ist zu langsam | `update.log` und `docker compose logs`; mit `RIMPERIO_HEALTH_TIMEOUT=900` erneut |
| `bad interpreter: /bin/bash^M` | Checkout mit CRLF-Zeilenenden | `.gitattributes` beachten, neu klonen (`*.sh text eol=lf`) |
| `detected dubious ownership` | root fasst ein Repository mit fremdem Besitzer an | `git config --global --add safe.directory /opt/rimperio` |
| „no space left on device“ mitten im Build | Sicherungen und alte Images | `docker image prune -f`, alte Verzeichnisse unter `/opt/rimperio-backups` entfernen |
| Update lief, Oberfläche zeigt den alten Stand | Browser-Cache | hart neu laden (Strg+Umschalt+R) |

---

## Nach dem Update prüfen

Welcher Stand ist installiert?

```bash
cat /opt/rimperio/VERSION
```

Laufen alle Container?

```bash
cd /opt/rimperio && sudo docker compose ps
```

Antwortet die Anwendung?

```bash
curl -s http://127.0.0.1/api/health
```

In der Oberfläche: *Systemzustand* für den Gesamtblick, *Updates* für den
Versionsstand. Was sich geändert hat, steht im
[Changelog](../changelog/README.md).

---

## Weiter

- [Sicherung](../backup/README.md)
- [Installation](../install/README.md)
- [Betrieb](../operate/README.md)
- [Changelog](../changelog/README.md)
