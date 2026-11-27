# Backup-Bereich: Aufträge, Orte, Verlauf

*Backup* im linken Menü – ein eigener Bereich: **was** gesichert wird (Aufträge), **wohin** (Orte)
und was dabei herauskam (Verlauf & Wiederherstellen). Die Sicherung von RIMPERIO selbst vor
Eingriffen steht weiter unter [Sicherung](README.md) und ist dort mit dabei.

← [Sicherung](README.md) · [Alle Dokumente](../README.md)

---

## Orte – wohin

| Art | Wofür |
|---|---|
| **Ordner am RIMPERIO-Server** | Volume `backup_data` (`/backups/jobs/…`) oder ein eingebundener Pfad. Schnell, aber auf demselben Rechner – nur zusammen mit einem zweiten Ort sinnvoll |
| **SMB-Freigabe** | NAS (Synology, QNAP, TrueNAS) oder Windows-Server. SMB2/3. Für ein verbundenes NAS lässt sich die Adresse übernehmen |
| **SFTP** | jeder Linux-Server, viele NAS |
| **FTPS / FTP** | wenn nichts anderes geht (FTP unverschlüsselt) |

Beim Speichern wird jeder Ort **geprüft**: Testdatei schreiben, zurücklesen, vergleichen, löschen.
Zugangsdaten liegen in [Vaultwarden](../integrations/README.md#vaultwarden-zugangsdaten), sonst lokal
verschlüsselt. Je Auftrag entsteht am Ort ein Unterordner `0007-<Name>/`.

**Synology als Ort:** *Systemsteuerung → Freigegebener Ordner* → z. B. `Backup` anlegen, einen eigenen
Benutzer mit Lese-/Schreibrecht nur auf diesen Ordner, SMB (*Dateidienste*) an. In RIMPERIO:
Art SMB, Server = IP des NAS, Freigabe `Backup`, Unterordner `rimperio`.

## Aufträge – was und wann

| Art | Was passiert | Verschlüsselt |
|---|---|:-:|
| **RIMPERIO-Server** | Sicherung der gewählten Bereiche (wie unter *Sicherung*) | ✓ |
| **Ordner auf einem Rechner** | Linux, Windows, **Raspberry Pi**: der Agent packt die Ordner als `tar.gz` und schickt sie per HTTPS an RIMPERIO; verschlüsselt wird beim Empfang | ✓ |
| **NAS-Konfiguration** | DSM-Konfiguration als `.dss` (Benutzer, Freigaben, Einstellungen) – klein, ideal vor Updates | ✓ |
| **Hyper Backup auf dem NAS** | Aufgabe auf dem NAS anstoßen und das Ergebnis abwarten; Ziel und Versionen regelt Hyper Backup | (Hyper Backup) |
| **Proxmox-VM/-Container** | `vzdump` (Snapshot-Modus, zstd) auf einen Proxmox-Speicher oder PBS | (Proxmox) |

**Zeitplan:** täglich zur Uhrzeit, an bestimmten Tagen, alle n Stunden oder nur von Hand (Zeitzone
Europe/Berlin). **Aufbewahrung:** die letzten *n*, optional „aber mindestens *x* Tage“ – ältere Dateien
werden am Ort gelöscht. **Überfällig:** ohne erfolgreichen Lauf seit dem 2,5-Fachen des Takts (täglich:
30 h, wöchentlich: 8 Tage) oder der eingestellten Stundenzahl gibt es eine Meldung.

### Rechner-Sicherungen im Detail

1. RIMPERIO legt den Lauf mit einem **Einmal-Schlüssel** an und schickt dem Agent ein kurzes Skript.
2. Der Agent packt die Ordner:
   * **Linux / Raspberry Pi:** über das Hilfsskript des Agents mit root-Rechten
     (`rimperio-helper backup-tar` – nur absolute, vorhandene Pfade, kein `..`, nur lesen). Ältere
     Installationen ohne diese Aufgabe sichern, was das Dienstkonto lesen darf – Agent neu installieren
     holt das Hilfsskript nach.
   * **Windows:** `tar.exe` (ab Windows 10) als SYSTEM, Upload mit `curl.exe`.
3. Der Strom geht per HTTPS an `PUT /api/backup-upload/<Lauf>` mit dem Einmal-Schlüssel. Der Server
   verschlüsselt beim Empfang und legt die Datei am Ort ab. **Der Rechner bekommt nie Zugang zum Ort.**
   Ein zweiter Upload mit demselben Schlüssel wird abgelehnt.
4. Ist der Rechner offline, wartet der Lauf bis zu 12 Stunden auf ihn.

## Verschlüsselung

Alles, was RIMPERIO selbst ablegt, ist mit **AES-256-GCM** verschlüsselt (je Auftrag abschaltbar).
Format `RIMPBAK1`, Dateiendung `.rimpbak`, als Strom in Blöcken zu 1 MB:

```
"RIMPBAK1" | u32 Länge | Kopf (JSON: Verfahren, Salt, Runden, Inhalt, Auftrag, Zeitpunkt)
je Block:  u32 Länge | AES-256-GCM(Block)
           Nonce = Kopf.nonce (8 Byte) | Blocknummer
           AAD   = SHA-256(Kopf)[:16] | Blocknummer | letzter Block ja/nein
Schlüssel = PBKDF2-HMAC-SHA256(Passwort, Salt, 310 000 Runden)
```

Vertauschte, veränderte oder abgeschnittene Blöcke fallen beim Entschlüsseln auf. Zusätzlich steht die
SHA-256 der gespeicherten Datei im Lauf und wird vor jedem Zurückholen verglichen.

Das **Passwort** erzeugt RIMPERIO beim ersten verschlüsselten Auftrag zufällig und legt es als
**„RIMPERIO · Backup-Schlüssel“** in Vaultwarden ab (oder lokal verschlüsselt). Je Auftrag geht auch ein
eigener Schlüssel. **Diesen Eintrag nie löschen** – ohne ihn sind die Sicherungen verloren.

### Ohne RIMPERIO entschlüsseln

```bash
pip install cryptography
python3 rimperio-decrypt.py RIMPERIO-taeglich-20261126-023000.zip.rimpbak
# Passwort: aus Vaultwarden, „RIMPERIO · Backup-Schlüssel“
python3 rimperio-decrypt.py DATEI.rimpbak --info     # nur den Kopf zeigen, ohne Passwort
```

Das Skript liegt unter [`docs/backup/rimperio-decrypt.py`](rimperio-decrypt.py) und braucht nur Python 3.

## Verlauf & Wiederherstellen

Jeder Lauf mit Zeitpunkt, Dauer, Ort, Größe (verschlüsselt und Inhalt), Prüfsumme und Protokoll.

| Knopf | |
|---|---|
| **Herunterladen** | holt die Datei vom Ort, prüft die Prüfsumme, entschlüsselt – im Browser kommt das Original (`.zip`, `.tar.gz`, `.dss`) |
| **.rimpbak** | die Datei so, wie sie am Ort liegt |
| **Prüfen** | Probe-Wiederherstellung: holen, Prüfsumme, entschlüsseln, Archiv lesen (Einträge zählen) – ersetzt nichts |
| **Zurückspielen …** | nur RIMPERIO-Sicherungen: entschlüsselt unter *Sicherung* bereitlegen, dort Bereiche wählen und zurückspielen |
| **Löschen** | Datei am Ort löschen |

## Meldungen

*Einstellungen → Benachrichtigungen* (und App): **Sicherung fehlgeschlagen**, **Sicherung überfällig**
(beide ab Werk an), **Sicherung erledigt** (ab Werk aus).

## API

| Methode | Pfad | |
|---|---|---|
| GET | `/api/backups/overview` | Kennzahlen, Schlüssel, Arten |
| GET/POST/PUT/DELETE | `/api/backups/targets[/{id}]` | Orte (`password` leer = unverändert) |
| POST | `/api/backups/targets/{id}/test` | schreiben, lesen, löschen |
| GET/POST/PUT/DELETE | `/api/backups/jobs[/{id}]` | Aufträge (`?delete_files=true` löscht auch die Dateien) |
| POST | `/api/backups/jobs/{id}/run` | jetzt sichern |
| GET | `/api/backups/runs?job_id=` | Verlauf |
| GET | `/api/backups/runs/{id}/download?decrypt=true` | Kopf `Authorization` oder `?token=` (Ticket `backup-download`) |
| POST | `/api/backups/runs/{id}/verify` | Probe-Wiederherstellung |
| POST | `/api/backups/runs/{id}/to-restore` | RIMPERIO-Sicherung unter *Sicherung* bereitlegen |
| PUT | `/api/backup-upload/{run}` | Upload des Agents (Kopf `X-Upload-Token`, einmalig) |
