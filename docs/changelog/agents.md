# Changelog — Agents

Installer & Log-Forwarder für Linux/Windows (`agents/`). Versionsformat: `YYYY.MM.DD.HH.MM.SS`.

## 2026.10.11.20.00.00

### Fixed
- **Windows-Agent (HTTPS): Ereignisse gingen verloren.** `Get-WinEvent -MaxEvents 100` holte die *neuesten* 100 Ereignisse und filterte dann nach Zeit — mehr als 100 zwischen zwei Durchläufen, und die älteren waren weg. Jetzt: Position je Kanal über die fortlaufende `EventRecordID` (`-FilterXPath "*[System[(EventRecordID > N)]]" -Oldest`), gespeichert in `positions.json` (übersteht Neustarts), höchstens 500 je Kanal und 5.000 je Durchlauf.
- **Windows-Agent: gescheitertes Senden verwarf das Paket.** Die Position rückt jetzt erst nach dem erfolgreichen Senden vor; wiederholt wird nur ab dem gescheiterten Paket, mit wachsender Pause (5 s … 5 min) und `Retry-After`. 400/413/422 werden protokolliert und übersprungen.
- **Windows-Agent: Zeitstempel und Event-ID** werden mitgeschickt — der Server erkennt doppelte Lieferungen, nachgeholte Ereignisse landen zur richtigen Zeit.
- Windows-Agent: geleerte Protokolle (RecordId beginnt neu) werden erkannt.
- Windows-Agent (Syslog-Modus): dieselbe lückenlose Leseposition.
- Aufgabenplanung: kein Zeitlimit mehr (vorher 365 Tage, danach still beendet), 999 statt 3 Neustarts nach Absturz.
- Test unter Windows PowerShell 5.1 mit echtem Systemprotokoll und simuliertem Serverausfall beim 2. Paket: 450 von 450 Ereignissen angekommen, keine doppelt gesendet.

## 2026.10.10.22.00.00

### Fixed
- **Linux-Agent: Endlosschleife bei Teilfehlern** — die Ursache des Ausfalls vom 2026-10-10. Der Agent las den gesamten Rückstand ab der gemerkten Position und schickte ihn in Paketen. Scheiterte *ein* Paket (Server weg, 429, 422 wegen eines zu langen Quellnamens), rückte die Position nicht vor und beim nächsten Durchlauf ging **alles** erneut raus — alle 5 s, für immer. Auf einem Proxmox-Host wurden daraus 14 Mio. Zeilen am Tag, die Platte lief voll. Jetzt: Position rückt nach **jedem** angekommenen Paket vor, höchstens 5.000 Journal-Zeilen pro Durchlauf (gedrosselt, 2 s Pause bei Rückstand), Pakete zu 500 statt 50 Einträgen.
- **Linux-Agent: wachsende Wartezeit nach Fehlern** (5 s, 10 s, 20 s … bis 5 min) statt starr alle 5 s; `Retry-After` vom Server wird beachtet.
- **Linux-Agent: dauerhaft abgelehnte Pakete** (400/413/422) werden protokolliert und übersprungen, statt alles dahinter für immer zu blockieren.
- **Linux-Agent: Zeitstempel und Facility** werden mitgeschickt (`__REALTIME_TIMESTAMP`, `SYSLOG_FACILITY`). Der Server verwirft damit doppelt gelieferte Einträge, nachgeholte Logs landen zur richtigen Zeit. Quellnamen werden auf die 100 Zeichen des Servers gekürzt.
- **Linux-Agent: `journalctl` blockiert nicht mehr** an vollem stderr-Puffer und lädt den Rückstand nicht mehr komplett in den Speicher.
- **Windows-Agent: mehrzeilige Meldungen gingen verloren** — `Substring` rechnete mit der Länge vor dem Ersetzen der Zeilenumbrüche und warf; `catch {}` verschluckte das samt Rest des Kanals (Test: 119 von 300 Ereignissen kamen an, jetzt 300).
- **Windows-Agent: Umlaute** — PowerShell 5.1 schickte den Text als ISO-8859-1, der Server lehnte die ganze Lieferung ab. Jetzt UTF-8-Bytes.
- **Windows-Agent: Zeitfenster je Kanal** statt eines gemeinsamen — Ereignisse, die während des Lesens anderer Kanäle entstanden, fielen durch die Lücke.

## 2026.09.23.10.00.00

### Security
- **Jeder Agent holt sich beim Installieren einen eigenen Schlüssel.** Bisher lag auf jedem Rechner derselbe: Wer einen davon aufmachte, hatte den Schlüssel für alle Geräte — und konnte im Namen jedes beliebigen Rechners Logzeilen erfinden oder Geräte samt Logs löschen. Beide Installer (Linux und Windows) tauschen den mitgegebenen Schlüssel jetzt über `POST /api/agents/enroll` gegen einen eigenen, der nur für diesen Rechner gilt. Gespeichert wird nur noch dieser.
- Der mitgegebene Schlüssel darf eine **Einladung** sein (im Web-UI unter Zugangsschlüssel): kurzlebig, zählbar, darf ausschließlich einen Geräteschlüssel anfordern. Damit muss der Generalschlüssel nicht mehr auf jeden Rechner kopiert werden.
- Beim Deinstallieren wird der Schlüssel des Geräts auf dem Server entwertet. Ein deinstallierter Agent lässt keinen gültigen Schlüssel zurück.

### Changed
- Die Abfrage heißt nicht mehr „Agent-Token“, sondern „Zugangsschlüssel“ — und der Text sagt, dass eine Einladung der bessere Weg ist.
- Die Anmeldung im Linux-Installer läuft über `python3` statt `curl`: python3 ist für den Agenten ohnehin Voraussetzung, curl ist auf Minimal-Installationen nicht immer dabei. Eine Abhängigkeit weniger.

### Fixed
- **Die Installation scheitert nicht, wenn der Server die Anmeldung noch nicht kennt.** Antwortet er mit 404 (ältere Fassung) oder ist er gerade nicht erreichbar, wird der mitgegebene Schlüssel eingetragen und die Installation läuft durch. Nur ein ausdrücklich abgelehnter Schlüssel (401/403) bricht ab — dann stimmt er wirklich nicht.

## 2026.09.15.20.00.00
### Added
- **`--proto udp|tcp`** (auch `LOGBOT_PROTO`) für den Syslog-Modus des Linux-Agents. Das
  Protokoll ließ sich bisher nur interaktiv wählen; mit `--yes` war es immer UDP.

### Fixed
- **Linux-Agent erschien als „Windows-Agent“ mit Docker-IP (z. B. `172.18.0.3`).** Der Agent schickte weder Geräteart noch eigene IP mit. Jetzt sendet der Linux-Agent (Dienst **und** Installationstest) `device_type: "linux_agent"` und seine eigene IP (`ip_address`, die Schnittstelle Richtung Server). Die IP stimmt damit auch hinter einem vorgeschalteten Reverse Proxy wie NPM. Ein fester Wert geht über `"ip_address"` in `/opt/logbot-agent/config.json`.
- Windows-Agent sendet ebenfalls `device_type: "windows_agent"`, statt sich auf den Token-Typ zu verlassen.
- Bestehende Installationen: Agent einmal neu installieren (One-Liner erneut ausführen), damit der neue Dienst geschrieben wird. Die falsche Karte korrigiert der Server aber auch ohne Neuinstallation (siehe Backend).
## 2026.09.09.22.00.00
### Added
- **Windows-Agent als Einzeiler.** `install-windows.ps1` nimmt jetzt Parameter entgegen
  (`-Action`, `-Fqdn`, `-Token`, `-Mode`, `-MinLevel`, `-Insecure`, `-Yes`, `-PurgeServer`)
  und läuft ohne Menü durch:
  `& ([scriptblock]::Create((irm <URL>))) -Action install -Fqdn … -Token … -Yes`
  *Warum nicht `irm | iex`:* Eine Pipeline reicht nur Text weiter, Parameter kommen dabei
  nicht an. `[scriptblock]::Create` macht daraus einen echten Skriptblock.
- **Server-Aufräumen unter Windows** (`Remove-ServerEntry`): meldet den Agent beim
  Deinstallieren ab, wie es der Linux-Agent schon konnte.
- **Gerätenummer merken.** Beide Agents speichern die `agent_id`, die der Server bei der
  ersten Lieferung zurückmeldet (`/opt/logbot-agent/agent_id` bzw.
  `%ProgramData%\LogBot-Agent\agent_id`), und nennen sie beim Abmelden.

### Fixed
- **Installer blieb an Rückfragen stehen.** `ask` wartete unbegrenzt auf Eingabe. Über eine
  Pipe (`curl | sudo bash`), per SSH ohne Terminal oder aus einem Automatismus heraus blieb
  das Skript damit an der ersten Frage hängen. Jetzt: 60 s Zeitlimit
  (`LOGBOT_ASK_TIMEOUT`), und ohne nutzbares Terminal wird gar nicht erst gefragt.
- **Deinstallation hing an mehreren Stellen.**
  `systemctl stop` bekommt 20 s, danach wird der Dienst per `SIGKILL` beendet;
  `disable`, `daemon-reload` und der rsyslog-Neustart bekommen ebenfalls Zeitlimits.
  Der Aufruf an den Server hatte **gar kein** Zeitlimit — jetzt 8 s zum Verbinden, 20 s
  insgesamt. Beim Abmelden wird nur noch gefragt, was tatsächlich fehlt.
- **Aufräumen auf dem Server traf womöglich den falschen Eintrag.** Der HTTPS-Agent meldete
  eine MAC, die der Server nie gespeichert hatte, und eine lokale IP, die hinter NAT nicht
  zur gespeicherten passte — übrig blieb der Abgleich über den Hostnamen allein. Jetzt geht
  die gemerkte Gerätenummer mit, und `all_for_hostname` räumt zusätzlich alle weiteren
  Einträge desselben Hostnamens ab (nach einem IP-Wechsel blieben sonst Karteileichen samt
  Logs liegen).
- Windows: eingefügte Adressen mit `https://` und Pfad werden auf den reinen FQDN gekürzt;
  ein nicht erreichbarer Server bricht die Einrichtung nicht mehr ab (der Agent versucht es
  zur Laufzeit ohnehin erneut).

## 2026.07.18.18.30.00
### Fixed
- **Linux-Installer: Tastatureingabe wurde beim One-Liner ignoriert.** Vorher hatte jede Abfrage einen eigenen 5-s-Timeout – bei `curl … | bash` rauschten die Abfragen durch und eine Eingabe innerhalb der 5 s lief ins Leere. Jetzt gibt es **einen** Countdown am Anfang (`interactive_gate`): Wird eine Taste gedrückt, schaltet der Installer auf **manuell** und fragt ab da **alle** Werte blockierend ab (kein Timeout, Eingabe wird abgewartet). Ohne Tastendruck / ohne Terminal (Pipe/cron) läuft alles automatisch mit Standardwerten. Der Tastaturpuffer wird nach dem Aufweck-Tastendruck geleert, damit die erste echte Abfrage nicht sofort den Default nimmt.
- Robustheit: `interactive_gate` liefert im Kein-Terminal-Fall sauber `return 0` – kein `set -e`-Abbruch mehr im vollautomatischen Lauf.

## 2026.07.18.16.00.00
### Added
- **Linux-Agent: One-Liner-Installation (`curl … | bash`).** Der Installer ist jetzt **teilautomatisch** und pipe-tauglich: `curl -sSL <URL> | sudo bash -s -- --fqdn logbot.example.com --token xxxx`. Werte kommen aus **Parametern**, **Umgebungsvariablen** (`LOGBOT_FQDN`, `LOGBOT_TOKEN`, `LOGBOT_MODE`, …) oder auskommentierten **Platzhaltern** im Skript. Vorrang: Parameter > Env > Platzhalter > Abfrage > Default.
- **Teilautomatischer Ablauf:** Jede Rückfrage hat **5 s Timeout** (via `LOGBOT_TIMEOUT` änderbar) und läuft sonst auf Default. Prompts werden aus `/dev/tty` gelesen → auch der `curl | bash`-Weg fragt in einer SSH-Sitzung nach; ohne Terminal (cron) läuft alles ohne Eingabe. `--yes`/`--unattended` unterdrückt alle Rückfragen.
- **Standard ist jetzt HTTPS** (nicht mehr Syslog) – sinnvoll, weil der Server oft nicht lokal liegt. FQDN + Token sind bei HTTPS zwingend (aus Param/Env/Platzhalter), sonst klarer Abbruch. Syslog weiterhin via `--mode syslog`.
- `--help` zeigt alle Aktionen/Parameter.

### Changed
- **Alle Agent-Daten liegen unter `/opt/logbot-agent/*`** – der Journal-Cursor wanderte von `/var/lib/logbot-agent/cursor` nach `/opt/logbot-agent/cursor` (überschreibbar via `LOGBOT_CURSOR`). Deinstallation räumt Altstände unter `/var/lib/logbot-agent` mit auf.
- Kein blockierendes Startmenü mehr: Standardaktion ist direkt `install`; `uninstall` / `uninstall-purge` / `test` als Argument.
- README (Agents + Haupt-README) um One-Liner, Parameter-Tabelle und Platzhalter-Anleitung erweitert.

_Windows-Agent: gleiche One-Liner-/Teilautomatik-Idee ist als Follow-up vorgesehen (noch offen)._

## 2026.07.18.12.00.00
### Added
- **Linux-Agent: HTTPS-Modus.** Der Installer (`install-linux.sh`) fragt jetzt bei der Installation den **Verbindungsmodus** ab: `1) Syslog (rsyslog UDP/TCP)` wie bisher **oder** `2) HTTPS`. Im HTTPS-Modus wird ein schlanker **Python-systemd-Dienst** (`logbot-agent`) eingerichtet, der **alle** Logs aus journald liest und als JSON-Batches (max. 50) verschlüsselt + Token-authentifiziert an `https://<FQDN>/api/agents/ingest` sendet. Nur Python-Standardbibliothek – keine externen Pakete.
- **DNS-/FQDN-basiert:** HTTPS verlangt bei der Installation die Angabe des **FQDN** (optionale IP als Laufzeit-Fallback). Der Dienst löst den FQDN zur Laufzeit erneut auf (DNS-first, IP-Fallback). Damit funktioniert der Weg auch hinter einem Reverse-Proxy (NPM), durch den rohes Syslog nicht geht.
- Robuster Journal-Cursor (`/var/lib/logbot-agent/cursor`): Start „ab jetzt" (keine History-Flut), at-least-once-Auslieferung, überlebt Neustarts, re-seedet bei rotiertem/ungültigem Cursor.
- **Server-Ingest** setzt `device_type` jetzt dynamisch aus dem Agent-Token (`linux` → `linux_agent`, `windows` → `windows_agent`) statt hart `windows_agent`. Frontend zeigt „Linux-Agent" als Typ/Filter.

### Fixed
- **Deinstallation/Menü (Linux):** Fehlerhafte `ä`-Escapes in `echo`-Ausgaben (wurden wörtlich als `ä` statt `ä` ausgegeben) durch echte Umlaute ersetzt. Fehlerhafte awk-Regex `/link\\/ether/` im MAC-Fallback der Server-Purge korrigiert (`$1=="link/ether"`). Server-Purge nutzt jetzt ein curl-Options-**Array** statt einer Wort-Split-anfälligen Zeichenkette.
- Deinstallation entfernt jetzt **beide** Modi sauber: rsyslog-Konfig + Queue **und** systemd-Dienst + `/opt/logbot-agent` + Cursor-Verzeichnis. Server-Purge übernimmt Host/Port/Token automatisch aus `config.json`.

## 2026.07.11.13.35.01
### Fixed
- **Linux-Agent: Hostname statt IP im Web.** Die rsyslog-Weiterleitung nutzt jetzt `RSYSLOG_TraditionalForwardFormat` (enthält `<PRI>` + `%HOSTNAME%`) statt `RSYSLOG_TraditionalFileFormat`. Ohne `<PRI>` konnte der Syslog-Server die Nachricht nicht parsen und ordnete die Logs der Absender-IP statt dem Hostnamen zu. Bestehende Installationen einmal neu konfigurieren (Installer → Installieren), damit `/etc/rsyslog.d/99-logbot.conf` neu geschrieben wird.

## 2026.07.09.19.55.08
### Fixed
- **FQDN als LogBot-Server-Adresse** wird jetzt zuverlässig unterstützt (Linux-Installer): robuste DNS-Auflösung über mehrere Methoden (`getent ahosts` → `python3` → `dig`/`host`), zusätzlicher TCP-Reachability-Test. Kein harter Abbruch (`exit 1`) mehr bei nicht sofort auflösbarem Namen — es wird gewarnt und nachgefragt (rsyslog löst zur Laufzeit ohnehin erneut auf).
- Eingegebene IP-Adresse wird nicht mehr heimlich durch einen Reverse-DNS-Namen ersetzt.

### Removed
- `nslookup` als Resolver entfernt (liefert bei NXDOMAIN auf vielen Systemen rc=0 → Falsch-Positive).

_Vorherige Stände: Windows-Agent `2026.02.20.19.00.09`, Agents-README `2026.03.31.17.26.46`._
