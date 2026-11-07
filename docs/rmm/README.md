# Fernwartung (RMM)

Rechner mit RIMPERIO-Agent aus der Ferne überwachen und steuern. In der Oberfläche unter
**Fernwartung**.

← [Dokumentation](../README.md) · [Agent](../../agent/README.md) · [Installer & Signatur](../../agent/installer/README.md)

## Ein Gerät, eine Zeile

Ein Rechner ist **ein** Gerät – egal ob er Logs schickt, den RIMPERIO-Agent trägt oder
beides. Meldet sich der Agent an und gibt es schon ein Gerät mit gleichem Hostnamen (etwa
eines, das per Syslog liefert), wird es weiterverwendet. Die Gerätekarte unter *Geräte*
verlinkt dann zur Fernwartung, die Rechner-Ansicht zu den Logs.

## Rechner anbinden

1. Unter *Verwaltung → Zugang & Sicherheit → Zugangsschlüssel* eine **Einladung** anlegen
   (zählbar, läuft ab). Der Generalschlüssel geht auch, sollte aber nicht auf jeden Rechner.
2. *Fernwartung → Rechner → Agent hinzufügen*: Schlüssel eintragen, Befehl kopieren
   (PowerShell, MSI oder Linux).
3. Der Rechner erscheint nach dem ersten Check-in (≤ 1 Minute).

Der Agent bekommt bei der Anmeldung ein eigenes Token; der Server speichert nur dessen
SHA-256. „Agent entfernen“ trennt den Agent vom Gerät, die Logs bleiben.

## Was es gibt

| Seite | Inhalt |
|---|---|
| **Rechner** | Liste mit Status, Standort, System, CPU/RAM/Platte; Detail mit Befehlen, Skripten, Patches, Checks, Platten/Prozessen und Inventur |
| **Skripte** | Bibliothek (PowerShell, cmd, bash, sh), Ausführung je Rechner |
| **Checks** | Prüfungen, die der Agent selbst im Takt ausführt: CPU, RAM, Platte, Dienst, Port, Ping, Skript (Exit-Code) – global, je Standort oder je Rechner |
| **Patches** | Ausstehende Updates je Rechner, suchen und installieren |
| **Rechner-Alarme** | Offline, Schwelle überschritten, Check fehlgeschlagen; Schwellen einstellen |

Lesen darf jeder angemeldete Benutzer, **Eingriffe nur Administratoren**. Jeder Eingriff
(Befehl, Skript, Patch, Änderung) steht im **Systemtagebuch** (Kategorie `rmm`).

## Melden

Alarme gehen über dasselbe Meldesystem wie alles andere: *Benachrichtigungen* (Mail,
Telegram, Webhook/n8n). Je Art ein- und ausschaltbar: *Rechner offline*, *über Schwelle*,
*Check fehlgeschlagen*, *Updates verfügbar*; bei Behebung kommt eine Entwarnung.

## Sicherung

Der Bereich **Fernwartung (RMM)** sichert Skripte, Checks, Ergebnisse, Patch-Stand,
Befehlsverlauf und Alarme. Messwerte (Telemetrie) bleiben 14 Tage und werden nicht
gesichert.

## API

Die Oberfläche spricht `/api/rmm/…` (Anmeldung wie überall), der Agent `/api/rmm/agent/…`
(Bearer-Token). Vollständig und ausprobierbar unter `/api/docs`.

| Pfad | Zweck |
|---|---|
| `POST /api/rmm/agent/enroll` | Anmeldung mit Zugangsschlüssel → Agent-Token |
| `POST /api/rmm/agent/checkin` | Lebenszeichen + Messwerte, holt Befehle und Checks ab |
| `POST /api/rmm/agent/inventory` · `results` · `check-results` | Inventur, Befehlsergebnisse, Check-Ergebnisse |
| `POST /api/rmm/agent/unregister` | Abmeldung durch den Agent (beim Deinstallieren) |
| `GET /api/rmm/agents` · `/agents/{uid}` | Rechner (mit `uninstall_pending`) |
| `DELETE /api/rmm/agents/{uid}` | Eintrag sofort entfernen; mit `?uninstall=true` stattdessen den Agent sich selbst deinstallieren lassen |
| `POST /api/rmm/agents/{uid}/commands` · `/run-script/{id}` · `/patches/{scan,apply}` | Eingriffe |
| `GET/POST/PUT/DELETE /api/rmm/scripts` · `/checks` | Bibliothek und Prüfungen |
| `GET /api/rmm/alerts` · `/summary` · `/settings` | Alarme, Übersicht, Schwellen |
