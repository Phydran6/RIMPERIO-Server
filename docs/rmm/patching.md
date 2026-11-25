# Patchmanagement

Windows, Linux und Docker patchen: täglich suchen, im Wartungsfenster installieren (was sofort drauf
muss, auch außerhalb), vorher per Proxmox sichern, danach prüfen. Freigeben muss man nur, was wirklich eine
Freigabe braucht – Kritisches immer, Wichtiges nur bei einem Warnsignal, Normales nie. In der Oberfläche unter **Patchmanagement → Patch-Center**, in der App unter
*Freigaben*.

← [Fernwartung](README.md) · [SNMP](snmp.md) · [Dokumentation](../README.md)

## Was ohne jede Einstellung passiert

Die Standardrichtlinie gilt für alle Rechner mit RIMPERIO-Agent:

| Schritt | Standard |
|---|---|
| Suche | täglich 12:00 (Windows Update, apt/dnf/yum, Docker-Images) |
| Einstufung | Stufe und Dringlichkeit nach Regeln, siehe unten |
| KI-Prüfung | an, entscheidet mit (bei „Wichtig“ und „Normal“; kann die Dringlichkeit anheben) |
| Meldung | Freigabe nötig → App, Glocke, Mail/Telegram/Webhook (falls eingerichtet); „sofort“ als kritische Meldung |
| Installation | im Wartungsfenster, täglich 02:00–05:00 (Europe/Berlin); „sofort“ auch außerhalb |
| Sicherung | Proxmox-Snapshot vor dem Lauf, sofern die VM zugeordnet ist |
| Neustart | wenn nötig |
| Prüfung | 5 Minuten nach dem Update (bzw. nach dem Neustart): Dienste, Container, Checks |
| Aufräumen | Snapshot nach 3 Tagen löschen, wenn die Prüfung bestanden ist |

Alles davon lässt sich unter **Patch-Richtlinien** ändern. Eigene Richtlinien gelten je
Standort oder je Rechner und gehen der Standardrichtlinie vor.

## Stufe und Dringlichkeit

Jedes Update bekommt zwei Eigenschaften – sie gelten für Betriebssystem-Updates und Docker-Container gleich.

**Stufe – wie viel Mensch braucht es?**

| Stufe | Verhalten | Typisch |
|---|---|---|
| **Normal** | läuft immer von selbst, ohne Rückfrage | gewöhnliche Pakete, Container-Neubauten, Defender-Definitionen |
| **Wichtig** | läuft von selbst – Freigabe **nur**, wenn es Fehler auslösen könnte | Sicherheitsupdates, Kernel, systemd/libc/OpenSSL, Docker-Engine, Windows-Sicherheitsupdates, Treiber |
| **Kritisch** | **nie** ohne Freigabe | Datenbanken, Webserver/Reverse-Proxys, Anmeldung und Zugang (SSH, PAM, LDAP, SSO, VPN), Proxmox, Windows-Funktionsupdates, Sprung der Hauptversion |

„Könnte Fehler auslösen“ heißt bei **Wichtig** (einzeln abschaltbar): die KI hält es für riskant, die KI kennt
Probleme, Sprung der Hauptversion, oder dasselbe Update ist auf einem anderen Rechner fehlgeschlagen.
Ein **normales** Update, bei dem die KI ein hohes Risiko sieht oder das anderswo fehlschlug, wird auf „Wichtig“
hochgestuft (↑, abschaltbar).

**Dringlichkeit – wie schnell muss es drauf?**

| Dringlichkeit | Wann | Typisch |
|---|---|---|
| **sofort** | direkt nach Erscheinen, auch außerhalb des Wartungsfensters (mit Sicherung und Prüfung wie sonst) | aktiv ausgenutzte Lücke (CISA KEV), oder per Regel / KI |
| **zeitnah** | im nächsten Wartungsfenster | Sicherheitsupdates, Wichtiges und Kritisches |
| **irgendwann** | nach einer Rückstellung (Standard 3 Tage) im Wartungsfenster | alles andere |

Beides zusammen ergibt, was passiert: *Normal + irgendwann* läuft nach drei Tagen nachts von selbst;
*Wichtig + zeitnah* läuft in der nächsten Nacht, außer es gibt ein Warnsignal; *Kritisch + sofort* (z. B. eine
ausgenutzte Lücke in OpenSSH) kommt als kritische Meldung aufs Handy und wird **direkt nach der Freigabe**
installiert, ohne aufs Fenster zu warten.

Ein Sofort-Lauf startet je Update und Version höchstens einmal in 24 Stunden; taucht das Update danach wieder auf,
wartet es aufs Fenster. Ist das automatische Installieren in der Richtlinie aus („nur von Hand“), gibt es auch
keine Sofort-Läufe.

**Schlägt ein Lauf fehl**, laufen dieselben Updates auf den anderen Rechnern nicht mehr automatisch, sondern warten
auf eine Freigabe – damit ein kaputtes Update nicht nachts durch alle Rechner geht.

## Regeln

Die erste passende Regel entscheidet über Stufe und Dringlichkeit; passt keine, gilt der Standard je Plattform
(„Normal“). Dringlichkeit leer = automatisch (Sicherheitsupdates, Wichtiges und Kritisches zeitnah, sonst
irgendwann). Eine aktiv ausgenutzte Lücke ist immer „sofort“; die KI darf die Dringlichkeit anheben, nie senken.

| Regel | Stufe | Dringlichkeit |
|---|---|---|
| Windows: Definitionsupdates (Defender) | Normal | zeitnah |
| Windows: Funktionsupdates, Upgrades | Kritisch | |
| Linux: Datenbankserver (PostgreSQL, MariaDB, MySQL, MongoDB, Redis, Valkey) | Kritisch | |
| Docker: Datenbank-Container | Kritisch | |
| Linux: Webserver / Reverse-Proxy (nginx, Apache, Caddy, HAProxy, Traefik …) | Kritisch | |
| Docker: Webserver / Reverse-Proxy (nginx, Traefik, Caddy, Nginx Proxy Manager, cloudflared …) | Kritisch | |
| Linux: Anmeldung / Zugang (openssh-server, PAM, SSSD, LDAP, Kerberos, Samba, OpenVPN, WireGuard …) | Kritisch | |
| Docker: Anmeldung / Zugang (Keycloak, Authentik, Authelia, Vaultwarden, oauth2-proxy, VPN …) | Kritisch | |
| Linux: Proxmox-Pakete | Kritisch | |
| alle: Sprung der Hauptversion (1.x → 2.x) | Kritisch | |
| Windows: Sicherheits- und kritische Updates | Wichtig | zeitnah |
| Windows: Treiber, Vorschau-Updates | Wichtig | irgendwann |
| Linux: Docker-Engine, containerd, runc | Wichtig | |
| Linux: Kernel | Wichtig | |
| Linux: systemd, libc, OpenSSL, Bootloader, sudo | Wichtig | |
| alle: Sicherheitsupdates | Wichtig | zeitnah |

Regeln prüfen ein Feld (`category`, `title`, `package`, `image`, `container`, `kind`,
`severity`, `kb`, `security`, `major`, `reboot`) mit `equals`, `contains`, `in`, `glob`,
`regex`, `is_true` oder `is_false`. Stufen: `normal`, `important`, `critical`, `ignore`;
Dringlichkeit: `immediate`, `soon`, `low` oder leer.

Ändert man eine Richtlinie, werden alle offenen Updates neu eingestuft, über die noch kein
Mensch entschieden hat.

**Umstieg:** Richtlinien mit den unveränderten Regeln von früher bekommen beim Start die neuen. Eigene Regeln
bleiben; dort wird `automatisch` zu *Normal* und `Freigabe` zu *Kritisch*, die frühere Rückstellung gilt für
„zeitnah“ und „irgendwann“. Webserver- und Anmelde-Regeln fehlen dann – „Mitgelieferte Regeln wiederherstellen“
holt sie.

## Datenbanken: Vorbereitung vor der Freigabe

Für kritische Datenbank-Container (Standard) laufen im Wartungsfenster schon **vor** der Freigabe Analyse,
Sicherung und Probelauf ([DB-Updates](../updates/db-updates.md)) – die echte Datenbank merkt davon nichts. Die
Freigabe kommt dann mit Ergebnis. Gibt man das Update frei, wird im nächsten Fenster (bei „sofort“ direkt)
ausgeführt; ein Sprung der Hauptversion bleibt bei der Bestätigung `MIGRATE` unter *DB-Updates*, ein unsauberer
Probelauf ebenso. Wer direkt unter *DB-Updates* freigibt, startet die Ausführung sofort. Stuft man Datenbanken auf *Wichtig* herunter, läuft ein Update derselben Hauptversion nach
sauberem Probelauf von selbst.

## KI-Prüfung

Für jedes neue Update fragt RIMPERIO die unter *KI-Auswertung* eingestellte KI: Risiko
(gering/mittel/hoch), Empfehlung (installieren/Freigabe/zurückhalten), Begründung, bekannte
Probleme, Quellen. Mit dabei: alle passenden Meldungen aus den Update-Quellen und — wenn
eingeschaltet — eine Websuche.

| Modus | Wirkung |
|---|---|
| `gate` (Standard) | Bei „Wichtig“: Risiko oder bekannte Probleme → Freigabe. Bei „Normal“: hohes Risiko → hochgestuft. Hebt die Dringlichkeit an, wenn die KI „sofort“ sagt |
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

1. *Proxmox & Snapshots → Proxmox-Host*: Adresse (IP oder Name, ohne Port gilt 8006),
   Benutzer `root@pam` und Passwort → **Verbinden**. RIMPERIO meldet sich einmal an, legt sich
   in Proxmox selbst den API-Token `root@pam!rimperio` an und speichert nur diesen — das
   Passwort nicht. Erneutes Verbinden ersetzt den Token.
   Mit Zwei-Faktor geht das nicht; dann *API-Token von Hand*: anlegen unter
   *Rechenzentrum → Berechtigungen → API-Token*; Rechte am Pfad `/vms`:
   `VM.Snapshot`, `VM.Snapshot.Rollback`, `VM.Audit`, `VM.PowerMgmt`. Für vzdump zusätzlich
   `VM.Backup` und `Datastore.AllocateSpace` am Speicher.
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

**Rechte unter Linux.** Der Agent läuft als eigenes Dienstkonto, nicht als root
([Einzelheiten](../../agent/README.md#linux-dienstkonto-statt-root)). Pakete spielt er über
ein festes Hilfsskript per `sudo` ein (`--sudo limited`, Standard); ohne sudo-Regel
(`--sudo none`) sucht er nur. Für Docker bekommt der Dienst die Gruppe `docker`. Kann er
ein Compose-Projekt nicht lesen (Dateien oder `.env` gehören root allein), erneuert er den
Container über die Docker-API. Zugangsdaten für private Registries liest er aus
`/opt/RMM/rimperio/var/.docker/config.json` — einmal hinüberkopieren:
`sudo install -D -o rimperio -g rimperio -m 0600 /root/.docker/config.json /opt/RMM/rimperio/var/.docker/config.json`.

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
| `GET /api/app/patching/approvals` | was wartet, mit KI-Einschätzung, Quellen, `tier`/`urgency` – sofort und kritisch zuerst |
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
