# Agents

Kleine Dienste, die Logs von einem Rechner an den RIMPERIO-Server schicken.
Für **Linux** und **Windows**, jeweils als Einzeiler installierbar.

← [Zurück zur Übersicht](../README.md) · [Alle Dokumente](../../docs/README.md)

---

## Zwei Betriebsarten

|                     | **Agent-basiert (HTTPS)** — Standard        | **Syslog (UDP/TCP)**              |
|---------------------|---------------------------------------------|-----------------------------------|
| Transport           | HTTPS an `/api/agents/ingest`               | Port 514                          |
| Verschlüsselt       | ja                                           | nein                              |
| Anmeldung           | Zugangsschlüssel, je Gerät ein eigener       | keine                             |
| Über das Internet   | **ja** — dafür ist er gedacht                | nein, nur im eigenen Netz         |
| Adressierung        | FQDN (DNS), IP als Rückfallebene             | FQDN oder IP                      |
| Braucht auf Linux   | python3 + systemd + journald                 | rsyslog                           |

**Kurz:** Rechner im eigenen Netz können Syslog sprechen. Alles, was **nicht im
gleichen Netz** hängt, geht agent-basiert — nur dort sind die Daten unterwegs
verschlüsselt und der Absender nachweisbar.

Beide Wege funktionieren mit einem **FQDN**. Beim HTTPS-Modus ist er faktisch
Pflicht: ein Zertifikat lautet auf einen Namen, nicht auf eine IP.

---

## Linux — Einzeiler

**Voll automatisch** (FQDN und Token mitgeben, nichts wird gefragt):

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh \
  | sudo bash -s -- --fqdn rimperio.example.com --token DEIN-AGENT-TOKEN --yes
```

**Mit Rückfragen** (5 s Countdown; eine Taste drücken schaltet auf manuell):

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh | sudo bash
```

**Syslog statt HTTPS:**

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh \
  | sudo bash -s -- --mode syslog --fqdn rimperio.example.com --port 514 --yes
```

| Aktion | Befehl |
|--------|--------|
| Testnachrichten senden | `sudo bash install-linux.sh test` |
| Deinstallieren | → [Einzeiler zum Deinstallieren](#deinstallieren--einzeiler) |

**Optionen:** `--fqdn` · `--token` · `--ip` · `--port` · `--mode https\|syslog` ·
`--proto udp\|tcp` *(Syslog)* · `--min-level info\|warning\|error` · `--insecure` ·
`--user <konto>` · `--yes` · `--timeout <s>` (alle auch als `RIMPERIO_*`-Umgebungsvariable).

### Nicht als root: eigenes Dienstkonto

Der HTTPS-Agent läuft unter einem **eigenen Dienstkonto** — Standard `rimperio`, anders
mit `--user <konto>` (im manuellen Modus fragt der Installer danach). Fehlt das Konto,
wird es angelegt: Systemkonto, ohne Anmeldung, ohne Passwort. root lehnt der Installer ab.
Das Journal liest der Dienst über die Gruppe `systemd-journal`, schreiben darf er nur in
sein eigenes Verzeichnis (`ProtectSystem=strict`, `NoNewPrivileges`).

Alles liegt unter `/opt/RMM/rimperio` — geteilt mit dem
[Fernwartungs-Agent](../README.md#linux-dienstkonto-statt-root), unter demselben Konto:

| Pfad | Inhalt | Gehört |
|---|---|---|
| `lib/log-agent.py` | das Programm | root, 0644 |
| `etc/log-agent.json` | Konfiguration mit Geräteschlüssel | Dienstkonto, 0600 |
| `var/log-agent.cursor`, `var/log-agent.id` | Leseposition, Gerätenummer | Dienstkonto |

Der Dienst heißt `rimperio-log-agent`:

```bash
systemctl status rimperio-log-agent
journalctl -u rimperio-log-agent -f
```

**Aktualisieren:** den Einzeiler noch einmal, ohne `--fqdn` und `--token` — die Werte der
vorhandenen Installation gelten weiter, eine neue Anmeldung ist nicht nötig:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh | sudo bash -s -- --yes
```

Eine **ältere Fassung** (lief als root unter `/opt/rimperio-agent`, Dienst `rimperio-agent`)
wird dabei übernommen: Einstellungen, Geräteschlüssel, Leseposition und Gerätenummer
wandern mit, der Agent macht genau dort weiter. Danach sind Dienst und Verzeichnis der
alten Fassung weg.

---

## Windows — Einzeiler

PowerShell **als Administrator**:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-windows.ps1))) -Action install -Fqdn rimperio.example.com -Token DEIN-AGENT-TOKEN -Yes
```

> **Warum diese Schreibweise und nicht `irm … | iex`?**
> Eine Pipeline reicht nur Text weiter — Parameter kommen dabei nicht an.
> `[scriptblock]::Create` macht aus dem geholten Text einen echten Skriptblock,
> und der nimmt Parameter entgegen wie eine Funktion.

**Ohne Parameter** erscheint das gewohnte Menü:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-windows.ps1)))
```

| Aktion | Befehl |
|--------|--------|
| Testnachrichten | `-Action test` |
| Deinstallieren | → [Einzeiler zum Deinstallieren](#deinstallieren--einzeiler) |

**Parameter:** `-Action install\|test\|uninstall` · `-Fqdn` · `-ServerIP` ·
`-ServerPort` · `-Token` · `-Mode https\|syslog` · `-MinLevel` · `-Insecure` ·
`-Yes` · `-PurgeServer`

Installation unter `%ProgramData%\RIMPERIO-Agent`, Ausführung als geplante Aufgabe
`RIMPERIOAgent` unter `SYSTEM`:

```powershell
Get-ScheduledTask -TaskName RIMPERIOAgent
```

### MSI-Paket?

Noch nicht dabei. Der PowerShell-Einzeiler deckt dasselbe ab und lässt sich per
GPO, Intune oder Softwareverteilung ausrollen — ohne Signaturkette und ohne
Paketpflege bei jeder Version. Für eine unbeaufsichtigte Verteilung genügt:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm <URL>))) -Action install -Fqdn rimperio.example.com -Token XXX -Yes"
```

---

## Zugangsschlüssel

Im Web-UI unter **Verwaltung → Zugang & Sicherheit → Zugangsschlüssel** (nur
für Administratoren).

**Jeder Rechner bekommt seinen eigenen Schlüssel.** Der Agent holt ihn sich beim
Installieren selbst: Er legt den mitgegebenen Schlüssel einmal vor, bekommt
dafür einen eigenen und speichert nur diesen. Was man beim Installieren angibt,
ist also nur die Eintrittskarte.

Drei Arten stehen zur Wahl:

| Art | Darf | Gedacht für |
|---|---|---|
| **Einladung** | nur einen Geräteschlüssel anfordern | der übliche Weg — kurzlebig und zählbar |
| **Gerät** | nur für sein Gerät liefern, nur sich selbst abmelden | wird beim Anmelden automatisch erzeugt |
| **Generalschlüssel** | alles | Sammler wie n8n, die für fremde Geräte einliefern |

**Nimm eine Einladung.** Sie läuft ab (Standard 24 Stunden) und gilt nur einmal.
Damit liegt der Generalschlüssel nirgends mehr auf einem Endgerät herum — und
genau das war vorher das Problem: Auf jedem Rechner lag derselbe Schlüssel. Wer
einen davon aufmachte, hatte den Schlüssel für alle und konnte im Namen jedes
beliebigen Geräts Logzeilen erfinden oder Geräte samt Logs löschen.

Ein Geräteschlüssel darf **nur für sein eigenes Gerät** liefern und **nur sich
selbst** abmelden. Geht ein Rechner verloren, entwertet man diesen einen — die
anderen laufen weiter. Beim Deinstallieren wird er automatisch zurückgezogen.

**Ein Schlüssel wird genau einmal angezeigt**, direkt nach dem Erzeugen. Danach
steht in der Datenbank nur noch seine Prüfsumme. Wer ihn verlegt, würfelt ihn
neu — das ist eine Sache von zwei Klicks und besser als ein Schlüsselbund, den
jeder Datenbankabzug mitnimmt.

> **Ältere Server:** Kennt der Server die Anmeldung noch nicht (HTTP 404),
> trägt der Installer den mitgegebenen Schlüssel ein und läuft durch. Die
> Installation scheitert also nicht an einer neueren Agent-Fassung.

---

## Deinstallieren — Einzeiler

Einfach kopieren und auf dem Rechner ausführen, von dem der Agent weg soll.
Keine Rückfragen, Adresse und Token nimmt der Agent aus seiner eigenen Konfiguration.

### Linux

Komplett: Agent weg + Gerät und Logs auf dem Server gelöscht:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh | sudo bash -s -- uninstall-purge --yes
```

Nur hier: Agent weg, Gerät und Logs bleiben auf dem Server:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh | sudo bash -s -- uninstall --yes
```

### Windows (PowerShell als Administrator)

Komplett: Agent weg + Gerät und Logs auf dem Server gelöscht:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-windows.ps1))) -Action uninstall -PurgeServer -Yes
```

Nur hier: Agent weg, Gerät und Logs bleiben auf dem Server:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-windows.ps1))) -Action uninstall -Yes
```

### Was wird entfernt?

| | Komplett | Nur hier |
|---|---|---|
| Dienst bzw. geplante Aufgabe, Agent-Dateien, Konfiguration mit Token | ✔ | ✔ |
| Linux: Dienstkonto und `/opt/RMM/rimperio` — sofern vom Installer angelegt und nicht noch der Fernwartungs-Agent darunter läuft | ✔ | ✔ |
| Gerät auf dem Server, **auch ältere Einträge mit demselben Hostnamen** | ✔ | – |
| Logs dieses Geräts auf dem Server | ✔ | – |

**Gut zu wissen**

- **Server nicht erreichbar?** Der Agent wird trotzdem lokal entfernt (nach höchstens 20 s).
  Das Gerät dann im Web-UI unter *Geräte* von Hand löschen.
- **Syslog-Agent (Linux):** Er hat keinen Token. Für „komplett“ deshalb Adresse und
  Token mitgeben: `… uninstall-purge --fqdn rimperio.example.com --port 443 --token DEIN-AGENT-TOKEN --yes`
- **Lieber mit Menü?** Unter Linux den [Setup-Assistenten](../README.md#schnellstart)
  nehmen (Punkt 7/8). Unter Windows den Einzeiler ohne Parameter starten (Punkt 3).

<details>
<summary>Wie findet der Server das richtige Gerät?</summary>

Der Agent merkt sich beim ersten Senden seine Gerätenummer
(`/opt/RMM/rimperio/var/log-agent.id` bzw. `%ProgramData%\RIMPERIO-Agent\agent_id`) und nennt
sie beim Abmelden. Fehlt sie, sucht der Server nach MAC, dann Hostname + IP, dann
Hostname allein. Welcher Weg gegriffen hat, steht in der Antwort (`matched_by`).
</details>

---

## Fehlersuche

| Symptom | Ursache und Abhilfe |
|---------|---------------------|
| Installer bleibt stehen | Behoben: Rückfragen haben jetzt ein Zeitlimit (60 s, `RIMPERIO_ASK_TIMEOUT`), ohne Terminal wird gar nicht gefragt. `--yes` überspringt alles. |
| Deinstallation hängt | Behoben: `systemctl stop` bekommt 20 s, dann wird der Dienst abgeschossen; der Server-Aufruf hat 20 s Zeitlimit. |
| `HTTP 401` beim Senden | Token stimmt nicht oder wurde zurückgezogen. Im Web-UI neu holen. |
| Keine Logs, Dienst läuft | `journalctl -u rimperio-log-agent -f` ansehen. Meist DNS oder eine Firewall auf 443. |
| „Der Fernwartungs-Agent läuft als …“ | Beide Agents teilen sich `/opt/RMM/rimperio` und damit ein Konto. `--user` weglassen oder dasselbe Konto nennen. |
| Zertifikat wird abgelehnt | Bei eigener CA: `--insecure` bzw. `-Insecure`. Besser: die CA auf dem Rechner bekannt machen. |
| Gerät steht doppelt im Web-UI | Der Rechner hat die IP gewechselt. Einmal `uninstall-purge` räumt alle Einträge des Hostnamens ab. |

---

## Verwandte Dokumente

- [Installation des Servers](../../docs/install/README.md)
- [API und Ingest](../../docs/api/README.md)
- [Änderungen an den Agents](../../docs/changelog/agents.md)
