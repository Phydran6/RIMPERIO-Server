# Patchmanagement

Windows, Linux und Docker patchen: täglich suchen, nur im Wartungsfenster installieren,
vorher per Proxmox sichern, danach prüfen. Freigeben muss man nur, was wirklich eine
Freigabe braucht. In der Oberfläche unter **Patchmanagement → Patch-Center**, in der App unter
*Freigaben*.

← [Fernwartung](README.md) · [SNMP](snmp.md) · [Dokumentation](../README.md)

## Was ohne jede Einstellung passiert

Die Standardrichtlinie gilt für alle Rechner mit RIMPERIO-Agent:

| Schritt | Standard |
|---|---|
| Suche | täglich 12:00 (Windows Update, apt/dnf/yum, Docker-Images) |
| Einstufung | Regeln, siehe unten |
| KI-Prüfung | an, entscheidet mit (riskante „automatische“ Updates brauchen dann doch eine Freigabe) |
| Meldung | Freigabe nötig → App, Glocke, Mail/Telegram/Webhook (falls eingerichtet) |
| Installation | nur im Wartungsfenster, täglich 02:00–05:00 (Europe/Berlin) |
| Sicherung | Proxmox-Snapshot vor dem Lauf, sofern die VM zugeordnet ist |
| Neustart | wenn nötig |
| Prüfung | 5 Minuten nach dem Update (bzw. nach dem Neustart): Dienste, Container, Checks |
| Aufräumen | Snapshot nach 3 Tagen löschen, wenn die Prüfung bestanden ist |

Alles davon lässt sich unter **Patch-Richtlinien** ändern. Eigene Richtlinien gelten je
Standort oder je Rechner und gehen der Standardrichtlinie vor.

## Was automatisch läuft, was eine Freigabe braucht

Die erste passende Regel entscheidet; passt keine, gilt der Standard je Plattform
(„automatisch“).

| Regel | Aktion |
|---|---|
| Windows: Definitionsupdates (Defender) | automatisch |
| Windows: Funktionsupdates, Upgrades | Freigabe |
| Windows: Treiber | Freigabe |
| Windows: Vorschau-Updates | Freigabe |
| Windows: Sicherheits- und kritische Updates | automatisch |
| Linux: Docker-Engine, containerd, runc | Freigabe |
| Linux: Datenbankserver (PostgreSQL, MariaDB, MySQL, MongoDB, Redis) | Freigabe |
| Linux: Proxmox-Pakete | Freigabe |
| Docker: Datenbank-Container | Freigabe |
| alle: Sprung der Hauptversion (1.x → 2.x) | Freigabe |

Regeln prüfen ein Feld (`category`, `title`, `package`, `image`, `container`, `kind`,
`severity`, `kb`, `security`, `major`, `reboot`) mit `equals`, `contains`, `in`, `glob`,
`regex`, `is_true` oder `is_false`. Aktionen: `auto`, `approve`, `ignore`.

Ändert man eine Richtlinie, werden alle offenen Updates neu eingestuft, über die noch kein
Mensch entschieden hat.

**Rückstellung:** „frühestens N Tage nach dem ersten Auftauchen“ gilt für automatische
Updates. Updates, die eine aktiv ausgenutzte Lücke schließen (CISA KEV), überspringen sie.

## KI-Prüfung

Für jedes neue Update fragt RIMPERIO die unter *KI-Auswertung* eingestellte KI: Risiko
(gering/mittel/hoch), Empfehlung (installieren/Freigabe/zurückhalten), Begründung, bekannte
Probleme, Quellen. Mit dabei: alle passenden Meldungen aus den Update-Quellen und — wenn
eingeschaltet — eine Websuche.

| Modus | Wirkung |
|---|---|
| `gate` (Standard) | Ist ein eigentlich automatisches Update riskant, braucht es eine Freigabe |
| `advise` | Einschätzung wird nur angezeigt |
| `off` | keine KI |

Die Einschätzung gilt je Update, nicht je Rechner: dasselbe KB auf 50 Rechnern wird einmal
bewertet (7 Tage zwischengespeichert). Ist keine KI eingerichtet, gilt die Regel allein —
außer „ohne KI-Einschätzung nichts automatisch“ ist gesetzt.

**Websuche:** mit Claude (Anbieter Anthropic) über die eingebaute Websuche des Modells;
sonst über eine eigene SearXNG-Instanz oder die Brave Search API (*Update-Quellen*).

## Update-Quellen

Mitgeliefert und abschaltbar: Microsoft Security Update Guide, Borns Tech and Windows World,
BleepingComputer, Ubuntu Security Notices, Debian Security Advisories, Proxmox-Ankündigungen,
Docker-Engine-Releases, CISA Known Exploited Vulnerabilities. Für Container-Images mit Quelle
auf GitHub (Label `org.opencontainers.image.source` oder `ghcr.io/…`) legt RIMPERIO die
Release-Notizen selbst als Quelle an.

Eigene Quellen: beliebige RSS-, Atom- oder RDF-Feeds, GitHub-Releases
(`https://github.com/<owner>/<repo>/releases.atom`) oder JSON im CISA-KEV-Format.

Jede Meldung wird nach KB-Nummern, CVEs und Paketnamen durchsucht und so den Updates
zugeordnet; die Treffer stehen an der Freigabe.

## Proxmox: Sicherung vorher, Aufräumen danach

1. *Proxmox & Snapshots → Proxmox-Host*: Adresse (`https://pve:8006`) und API-Token.
   Token anlegen unter *Rechenzentrum → Berechtigungen → API-Token*; Rechte am Pfad `/vms`:
   `VM.Snapshot`, `VM.Snapshot.Rollback`, `VM.Audit`, `VM.PowerMgmt`. Für vzdump zusätzlich
   `VM.Backup` und `Datastore.AllocateSpace` am Speicher. Benutzer + Passwort geht auch, aber
   nicht mit Zwei-Faktor.
2. RIMPERIO liest die VMs und ordnet sie den Rechnern zu — über die MAC-Adressen aus der
   Agent-Inventur, sonst über den Namen. Von Hand geht immer.

Je Richtlinie: Snapshot, vzdump-Sicherung oder „Snapshot, sonst vzdump“ (für Speicher ohne
Snapshots); Arbeitsspeicher mitsichern; „ohne Sicherung nicht installieren“; Löschfrist;
„bei Fehlschlag automatisch zurückrollen“.

Snapshots heißen `rimperio_JJJJMMTT_hhmmss`. Gelöscht werden nur die, die RIMPERIO selbst
angelegt hat, und nur nach bestandener Prüfung. „Behalten“ hält eine Sicherung fest.

## Prüfung nach dem Update

Vor der Installation nimmt der Agent ein Gesundheitsbild auf, danach noch einmal:

- **Windows:** Dienste mit Starttyp *Automatisch*, die vorher liefen und nachher nicht
- **Linux:** laufende Dienste, die fehlen, und neu fehlgeschlagene systemd-Units
- **Docker:** Container, die nicht mehr laufen oder ungesund sind
- **Checks:** fehlschlagende RMM-Checks dieses Rechners
- Rechner kommt nach dem Neustart nicht zurück (Standard 45 Minuten)

Dienste, die rund um Updates kommen und gehen (Windows-Update-Dienste, apt-daily, …), werden
ignoriert; eigene lassen sich ergänzen. Während eines Laufs gibt es keinen Offline-Alarm.

Schlägt die Prüfung fehl: der Lauf steht auf „fehlgeschlagen“, die Sicherung bleibt, eine
Meldung mit „Zurückrollen“ geht an App und Glocke. Nach einem Zurückrollen werden die
betroffenen Updates als abgelehnt markiert.

## Docker — wie Watchtower und Tugtainer

Auf jedem Linux-Rechner mit Agent und Docker:

- **Suchen:** Für jeden Container fragt der Agent die Registry nach dem Abdruck des Tags —
  ohne etwas herunterzuladen — und liest bei einem Update die Versionsangabe des neuen Images
  (`org.opencontainers.image.version`). Zugangsdaten für private Registries kommen aus der
  Docker-Konfiguration des Hosts (`docker login`).
- **Erneuern:** Compose-Container über `docker compose up -d --no-deps <dienst>`, alle anderen
  über die Docker-API mit derselben Konfiguration (ohne die Vorgaben des alten Images
  festzuschreiben, anonyme Volumes bleiben erhalten, alle Netze werden wieder verbunden).
- **Prüfen:** Healthcheck abwarten (bzw. 15 s stabil laufen); sonst alter Stand zurück.
- **Aufräumen:** altes Image entfernen.

Labels am Container:

| Label | Wirkung |
|---|---|
| `com.centurylinklabs.watchtower.enable=false` | wird nie angefasst |
| `de.rimperio.update=off` | wird nie angefasst |
| `com.centurylinklabs.watchtower.monitor-only=true` | Update wird angezeigt, nicht installiert |
| `de.rimperio.update=monitor` | dito |

Dazu je Richtlinie Ein- und Ausschlusslisten (Muster wie `*-db`). Auf dem RIMPERIO-Server
selbst: Agent installieren — die RIMPERIO-eigenen Container werden dabei automatisch
übersprungen, sie aktualisiert *RIMPERIO-Server → Updates*.

## Freigeben in der App

App-Schnittstelle ab API-Level 3 (`/api/app/bootstrap` meldet `api_level` und die
Fähigkeiten):

| Pfad | Zweck |
|---|---|
| `GET /api/app/notifications?since_id=` | neue Meldungen |
| `POST /api/app/notifications/read` | gelesen markieren |
| `GET /api/app/patching/summary` | Zahlen für den Startbildschirm |
| `GET /api/app/patching/approvals` | was wartet, mit KI-Einschätzung und Quellen |
| `POST /api/app/patching/decide` | `approve`, `reject`, `defer` |
| `POST /api/app/patching/install` | sofort installieren |
| `GET /api/app/patching/runs` | Läufe |
| `POST /api/app/patching/runs/{id}/rollback` | zurückrollen |
| `GET /api/app/snmp` | SNMP-Geräte mit Problemen |

Entscheiden dürfen nur Administratoren. Jede Entscheidung steht mit „(App)“ im
Systemtagebuch.

## API (Oberfläche)

`/api/patching/…` — Übersicht, Updates, Entscheidungen, Läufe, Richtlinien, Proxmox, Quellen;
`/api/notifications` — Eingang. Vollständig unter `/api/docs`.
