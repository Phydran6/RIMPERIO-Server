# Mandanten – mehrere Kunden mit RIMPERIO

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

RIMPERIO kann mehrere Kunden auf zwei Arten bedienen:

| | **Mandanten in einer Instanz** | **Eine Instanz je Kunde** |
|---|---|---|
| Was | Ein RIMPERIO, darin Kunden (Mandanten), sauber getrennt | Mehrere RIMPERIO nebeneinander auf einem Server |
| Einrichten | `--multi-tenant` beim Installieren oder später *Benutzer → Mandanten* | `install.sh --instance kunde1` |
| Datenbank | eine, Trennung je Zeile | je Instanz eine eigene |
| Updates, Sicherung | einmal für alle | je Instanz |
| Betreiber sieht | alle Kunden auf einen Blick, umschaltbar | jede Instanz einzeln |
| Ports | 80/443/514 | je Instanz eigene (8080/8443/1514 …) |
| Passt, wenn | ein Dienstleister viele Kunden betreut | ein Kunde eine strikt eigene Installation will |

Beides lässt sich kombinieren: eine Instanz je Kunde, und darin trotzdem Mandanten.

---

## Mandanten in einer Instanz

### Einschalten

**Beim Installieren** – der Installer fragt im manuellen Modus (Taste im Countdown),
der Assistent ([`server/setup.sh`](../../server/setup.sh)) immer. Im Einzeiler:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/install.sh \
  | sudo bash -s -- --multi-tenant --yes
```

**Nachträglich** – in der Oberfläche unter *Benutzer → Mandanten → Einschalten*. Oder
beim Update:

```bash
curl -sSL … /install.sh | sudo bash -s -- update --multi-tenant -y
```

Der Installer schreibt `RIMPERIO_MULTITENANT=true` in die `.env`; das Backend schaltet
beim nächsten Start ein. Wer danach in der Oberfläche ausschaltet, bleibt ausgeschaltet –
der Wert aus der `.env` wirkt nur einmal.

**Ausschalten** blendet Verwaltung und Umschalter aus. Daten und Zuordnungen bleiben.
Konten, die einem Mandanten gehören, bleiben auf ihn beschränkt – aus einem Kunden-Konto
wird nie stillschweigend ein Betreiber-Konto.

### Bisherige Installation aufteilen

Beim Einschalten gehört alles Vorhandene dem **Betreiber**. Dann:

1. *Benutzer → Mandanten → Neuen Mandanten anlegen*. Gehörte die Instanz bisher genau
   einem Kunden: „Den bisherigen Bestand übernehmen“ anhaken – Geräte, SNMP, Proxmox-Hosts,
   NAS und Zugangsschlüssel wandern zu ihm.
2. Sonst unten unter **Zuordnen**: Art wählen (Geräte, Schlüssel, Proxmox-Hosts …),
   markieren, Ziel-Mandant, *zuordnen*. Logs, Messwerte, Befehle, Alarme und Updates
   wandern mit ihrem Gerät.
3. Unter *Benutzer* je Kunde Konten anlegen und den Mandanten wählen.

### Wer sieht was

| | Betreiber (Konto ohne Mandant) | Kunden-Konto (mit Mandant) |
|---|---|---|
| Geräte, Logs, Alarme, Checks, Updates, Proxmox, SNMP, Meldungen | alle; oben rechts auf einen Mandanten umschaltbar | nur die eigenen |
| Benutzer, Zugangsschlüssel | alle | nur die eigenen; Admins legen sie für ihren Mandanten an |
| Skripte, Checks, Patch-Richtlinien, SNMP-Vorlagen | alle | eigene + die des Betreibers (nur lesen) |
| Server: Updates, Sicherung, Konsole, Container, Datenbank, Netzwerk, HTTPS, Mail, KI, SSO, LDAP, Systemtagebuch | ja | **nie** – auch nicht als Admin |
| Netzwerksuche, Log-Sperrregeln, Aufbewahrung | ja | nein (gelten für die ganze Instanz) |
| Proxmox-Hosts anlegen/ändern | ja | nein – der Betreiber legt an und ordnet zu, der Kunde nutzt sie |
| SNMP-Geräte | ja | ja, abgefragt über einen **eigenen Agent** (nicht vom Server aus) |
| NAS, Sicherungsziele und -aufträge | ja | sehen, aktualisieren, „jetzt sichern“, prüfen; Anlegen und Verbindungen beim Betreiber. Die Sicherung von RIMPERIO selbst bleibt immer beim Betreiber |
| Zugangsdaten (Vaultwarden) | ja | nein |

Ein **Kunden-Admin** verwaltet seinen Mandanten selbst: Benutzer, Einladungen und
Generalschlüssel, eigene Skripte, Checks und Patch-Richtlinien, Freigaben. Der Server
setzt die Grenzen durch, nicht die Oberfläche – auch wer die API direkt anspricht, kommt
nicht weiter.

Mit **„Ansehen“** in der Liste (oder dem Umschalter oben rechts) sieht der Betreiber
genau, was dieser Kunde sieht; was er in dieser Ansicht anlegt, gehört dem Kunden.
*Nur Betreiber* zeigt alles ohne Mandant.

### Geräte anbinden

Unter *Geräte → Zugangsschlüssel* beim Anlegen den **Mandanten** wählen (Kunden-Admins
bekommen automatisch ihren). Was sich mit dem Schlüssel anmeldet, gehört diesem
Mandanten. Jeder Mandant kann einen **eigenen Generalschlüssel** haben – der meldet nur
Geräte dieses Mandanten an und liefert nur für sie.

Zwei Kunden mit `server01` auf `192.168.1.10` sind zwei Geräte. Gesucht wird beim
Anmelden nur unter den Geräten des Mandanten, dessen Schlüssel benutzt wird.

Syslog (Port 514) kennt keinen Schlüssel: ein Gerät, das nur per Syslog liefert, landet
beim Betreiber und wird dann zugeordnet. Kunden mit überlappenden privaten Netzen
schicken besser per Log-Agent (HTTPS, mit Schlüssel) – oder bekommen eine eigene
Instanz.

### Patch-Richtlinien

Eine Richtlinie mit Mandant gilt nur für dessen Geräte, eine ohne Mandant für alle. Bei
gleichem Geltungsbereich (*global*, *Standort*, *Gerät*) gewinnt die des Mandanten;
ein engerer Bereich gewinnt wie bisher immer.

### Meldungen

Meldungen (Freigaben, Fehlschläge, SNMP) gehören dem Mandanten des Geräts – im Eingang
und in der App sieht sie nur, wer den Mandanten sieht. Unter *Mandanten → Bearbeiten*
lassen sich **zusätzliche Empfänger** je Mandant eintragen; sie bekommen Meldungen zu
dessen Geräten über den eingerichteten Mailversand.

### Sperren und Löschen

**Sperren:** Die Konten des Mandanten kommen nicht mehr hinein (auch die App nicht); die
Geräte liefern weiter.

**Löschen:** Der Bestand geht an den Betreiber oder einen anderen Mandanten. Konten des
Mandanten werden mitgelöscht (nach Rückfrage) – sie werden nie zu Betreiber-Konten.
Generalschlüssel und Einladungen des Mandanten werden **entwertet**, damit ein
ehemaliger Kunde damit nichts mehr anmelden kann; Geräteschlüssel wandern mit ihren
Geräten. Gibt es beim Ziel schon ein Gerät mit gleichem Namen und gleicher IP, bricht
RIMPERIO ab und nennt die Geräte.

### App

Die App zeigt Kunden-Konten ihren Mandanten unter *Mehr*. Betreiber schalten dort die
Ansicht um (*Mehr → Mandant*). Braucht einen Server mit der Fähigkeit `tenants`.

### Technik

- Tabelle `tenants`; Spalte `tenant_id` an `users`, `agents`, `agent_tokens`,
  `pve_hosts`, `notifications`, `snmp_devices`, `snmp_credentials`, `nas_devices`,
  `backup_targets`, `backup_jobs` (gehört genau einem; NAS-Vorgänge und Sicherungsläufe hängen daran)
  und `scripts`, `checks`, `patch_policies`, `snmp_templates` (leer = für alle).
  Angelegt beim Start (`app/tenancy.py`, `ensure_schema`).
- Getrennt wird zentral in der Datenbankschicht: jede ORM-Abfrage bekommt je Tabelle die
  passende Bedingung (`do_orm_execute` + `with_loader_criteria`); Logs, Messwerte,
  Befehle, Alarme, Updates hängen über `agent_id` dran. Beim Speichern bekommen neue
  Zeilen den Mandanten, fremde oder gemeinsame Zeilen lassen sich nicht ändern
  (`before_flush`, Antwort 403).
- Welcher Mandant gilt, setzt die Anmeldung (`auth.get_current_user`) bzw. der Schlüssel
  eines Agents. Betreiber schalten mit dem Kopf `X-Rimperio-Tenant: <id>` um (`0` = nur
  ohne Mandant).
- Kunden-Konten erreichen nur die Wege in `tenancy.TENANT_PATHS` (Positivliste).
- Eindeutig ist ein Gerät je Mandant: Index `idx_agents_hostname_ip_tenant` auf
  Hostname, IP und Mandant.
- API: `GET /api/tenants/context` (für alle), `GET/POST/PUT/DELETE /api/tenants`,
  `POST /api/tenants/enable`, `GET /api/tenants/assignable`, `POST /api/tenants/assign`
  (nur Betreiber-Admins).

---

## Eine Instanz je Kunde

Mehrere RIMPERIO laufen nebeneinander auf einem Server – jede mit eigenem Verzeichnis,
eigener Datenbank, eigenen Containern und Ports:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/install.sh \
  | sudo bash -s -- --instance kunde1 --yes
```

| | Standard-Instanz | `--instance kunde1` |
|---|---|---|
| Verzeichnis | `/opt/rimperio` | `/opt/rimperio-kunde1` |
| Container | `rimperio-app-*`, `rimperio-ext-*` | `rimperio-kunde1-app-*`, `rimperio-kunde1-ext-*` |
| Compose-Projekt | `rimperio` | `rimperio-kunde1` |
| HTTP / HTTPS | 80 / 443 | erster freier ab 8080 / 8443 |
| Syslog | 514 | erster freier ab 1514 |
| PostgreSQL (nur lokal) | 5432 | erster freier ab 5433 |

Ports lassen sich vorgeben: `--http-port`, `--https-port`, `--syslog-port`, `--db-port`.
Im manuellen Modus fragt der Installer nach (Enter = Vorschlag). Alles landet in der
`.env` der Instanz (`RIMPERIO_NAME`, `COMPOSE_PROJECT_NAME`, `RIMPERIO_INSTALL_DIR`,
`HTTP_PORT`, `HTTPS_PORT`, `SYSLOG_PORT`, `DB_HOST_PORT`).

Aktualisieren und entfernen mit demselben Namen:

```bash
curl -sSL … /install.sh | sudo bash -s -- update --instance kunde1 -y
curl -sSL … /install.sh | sudo bash -s -- uninstall --instance kunde1
```

Das Update in der Oberfläche (*RIMPERIO-Server → Updates*) arbeitet automatisch im
Verzeichnis der Instanz.

**HTTPS:** Let's Encrypt braucht Port 80 und 443 von außen – die hat meist schon die
Standard-Instanz. Für weitere Instanzen entweder das eigene Zertifikat (`--tls internal`)
oder einen Reverse Proxy davor (z. B. die Standard-Instanz bzw. Nginx Proxy Manager), der
je Domain an den HTTP-Port der Instanz weiterleitet.

**Agents** einer Instanz zeigen auf deren Adresse samt Port (z. B.
`https://rmm-kunde1.example.de` hinter dem Proxy oder `https://server:8443`), Syslog auf
deren Syslog-Port.

**Gut zu wissen:** Jede Instanz hat volle Rechte auf dem Host (Konsole, Updates,
Container) – wie die Standard-Instanz. Kunden-Admins einer eigenen Instanz sind damit
Admins des Servers. Wer das nicht will, gibt Kunden Konten mit Mandant (siehe oben) oder
nutzt die gehärtete Variante (`server/deploy/hardened.yml`). Zusatzdienste (Portainer &
Co.) laufen am besten nur in der Standard-Instanz; braucht eine weitere sie auch, in deren
`.env` eigene Ports setzen (`PORTAINER_PORT`, `N8N_PORT`, `OPENWEBUI_PORT` …).
