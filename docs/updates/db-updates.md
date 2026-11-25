# DB-Updates mit Probelauf

← [Updates](README.md) · [Changelog](../changelog/releases.md)

Datenbank-Container sind anders als jeder andere Container: Ihre Daten liegen in einem Format,
das zur **Hauptversion** passt. PostgreSQL 17 startet nicht auf einem Datenverzeichnis von
PostgreSQL 16 – und ein Image mit `:latest` macht genau diesen Sprung irgendwann von selbst.
RIMPERIO aktualisiert Datenbanken deshalb nie blind, sondern in festen Schritten. Kein Schritt
beginnt, bevor der vorige nachweislich gelungen ist.

| Schritt | Was passiert | Danach |
|---|---|---|
| **1. Analyse** | Datenbank, Version alt/neu, Hauptversions-Sprung?, Größe, freier Platz, Datenverzeichnis auf Volume?, Zeilen je Tabelle (Referenz) | Vorprüfung: ohne Platz, ohne Volume (bei Hauptversion) oder ohne Root-Passwort (MariaDB/MySQL) geht es nicht weiter |
| **2. Sicherung** | logischer Abzug: `pg_dumpall`, `mariadb-dump`, `mongodump`, Redis `SAVE` → `/opt/RMM/rimperio/var/db-backups/<container>/m<id>/` | Prüfsumme im Bericht |
| **3. Probelauf** | Wegwerf-Container mit dem **neuen** Image (gleiche Umgebung, Zugangsdaten über eine Datei mit 0600), Abzug einspielen, Anpassungen ausführen, jede Tabelle zählen; danach restlos entfernen | die echte Datenbank merkt nichts |
| **4. Prüfung** | Zeilenzahlen alt ↔ neu, Fehler beim Einspielen (bekannte harmlose wie „role … already exists“ gefiltert), optional **KI-Einschätzung** mit Vorschlägen für Anpassungen | sauber → Freigabe; nicht sauber → Anpassungen setzen, Probelauf wiederholen |
| **5. Freigabe** | Hauptversion immer von Hand (Web oder App, Bestätigung `MIGRATE`). Gleiche Hauptversion: bei Stufe *Kritisch* (Standard, [Patchmanagement](../rmm/patching.md#stufe-und-dringlichkeit)) laufen Schritte 1–4 schon vor der Freigabe, die Freigabe des Updates gibt dann die Ausführung frei; bei *Wichtig*/*Normal* nach sauberem Probelauf automatisch | |
| **6. Ausführung** | optional Proxmox-Snapshot. Gleiche Hauptversion: frischer Abzug → Container-Update mit Gesundheitsprüfung → Zählung. Hauptversion: Container des Compose-Projekts anhalten → frischer Abzug → DB anhalten → Datenverzeichnis als Archiv → leeren → neues Image (frische Datenbank) → Abzug einspielen → Anpassungen → Zählung → `ANALYZE` / `mariadb-upgrade` → Projekt starten | |
| **7. Rückweg** | automatisch, wenn nach dem Leeren etwas schiefgeht (oder von Hand): Datenverzeichnis aus dem Archiv, altes Image (vorher als `rimperio-dbrollback/<name>:m<id>` gesichert), starten | bis zum Schluss drei Sicherungen: Abzug, Archiv, ggf. Snapshot |

## Anpassungen – wenn die Daten nicht ganz zur neuen Version passen

Zwischen „Abzug einspielen“ und „zählen“ können eigene Befehle laufen (SQL für PostgreSQL und
MariaDB/MySQL, JavaScript für MongoDB) – etwa eine Erweiterung neu anlegen, einen geänderten
Standardwert setzen, eine Spalte umbenennen. Sie laufen im Probelauf **und** bei der Ausführung.
Die KI schlägt sie vor, wenn der Probelauf Fehler zeigt („KI-Anpassungen übernehmen &
Probelauf“); ausgeführt wird erst, wenn der Probelauf mit ihnen sauber ist.

## Ziel-Image

Leer = gleicher Tag, neuer Stand (der Fall, in dem `:latest` von selbst springt). Für einen
gewollten Wechsel (z. B. `postgres:16` → `postgres:17`) das Ziel-Image eintragen. Der Probelauf
geht immer. Für die Ausführung muss das neue Image in der Compose-Datei stehen (ohne neu zu
starten!) – RIMPERIO legt den Container über Compose neu an. Steht dort noch das alte, merkt die
Prüfung das (falsche Version) und der Rückweg stellt den alten Stand her. Container ohne Compose
mit abweichendem Ziel: nur Probelauf.

## Voraussetzungen

- Linux-Rechner mit RIMPERIO-Agent, Docker und dem Agent-Konto in der Gruppe `docker`
  (Standard bei `install-agent.sh`).
- Datenverzeichnis auf einem Volume oder Bind-Mount (sonst wäre es mit dem Container weg).
- Platz: etwa dreimal die Datenbankgröße im Sicherungsordner, doppelt für Docker.
- MariaDB/MySQL: Root-Passwort in der Container-Umgebung (`MARIADB_ROOT_PASSWORD` bzw.
  `MYSQL_ROOT_PASSWORD` oder `*_FILE`).

## Getestet

PostgreSQL 16 → 17 (Compose-Projekt mit zwei Datenbanken und Schema) und MariaDB 10.11 → 11.4:
Probelauf, Ausführung mit Neueinspielen, Rückweg von Hand und automatischer Rückweg bei
falscher Version.
