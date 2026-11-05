# Release-Verlauf

Datumsbasierte Versionen: `JAHR.MONAT.TAG.STUNDE.MINUTE.SEKUNDE`.
Neueste oben. Kategorien nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

---

## 2026.10.31.15.40.00 — Grundsystem

Erste, lauffähige Fassung von RIMPERIO.

### Neu

- **Backend (FastAPI):** Benutzer-Anmeldung (JWT) mit Rollen admin/operator/readonly;
  Agent-Enrollment mit gemeinsamem Geheimnis; Check-in mit Telemetrie; Inventur;
  Befehls-Warteschlange und Ergebnis-Rückmeldung; Alarme mit Schwellen und
  Offline-Erkennung (selbstheilend); Benutzer- und Einstellungsverwaltung;
  OpenAPI unter `/api/docs`.
- **Agent (Go):** ein Binary für Windows und Linux; läuft als Systemdienst
  (Windows-Service / systemd über kardianos/service); Messwerte und Inventur über
  gopsutil; führt Befehle in PowerShell/CMD/Bash/sh mit Zeitlimit aus; Anmeldung
  direkt (`setup`) oder per Registry-Bootstrap aus dem MSI (Selbst-Anmeldung beim
  ersten Start).
- **Frontend (Vue 3 + Tailwind):** Dashboard, Agent-Liste und -Detail mit
  Live-Werten und Befehlskonsole, Alarme, Einstellungen, Benutzerverwaltung.
- **Installer:** signierbare `.exe` und `.msi` (WiX v4); `sign.ps1` für Azure
  Trusted Signing / PFX; `build-agent.ps1`, `build-msi.ps1`; PowerShell- und
  Linux-Schnellinstaller; ehrliche Anleitung zu Code Signing und Virenscannern.
- **Betrieb:** Docker-Compose-Stack (Caddy, Backend, Frontend, PostgreSQL),
  Einzeiler-Installer `install.sh`, automatisches HTTPS über Caddy.
- **CI:** `ci.yml` baut Backend/Agent/Frontend; `agent-release.yml` baut,
  signiert und veröffentlicht die Agent-Artefakte bei einem `v*`-Tag.

### Sicherheit

- Agent-Token serverseitig nur als SHA-256; Passwörter mit Argon2.
- Datenbank nicht nach außen veröffentlicht; Agents sprechen nur ausgehend.
- Keine Erkennungs-Umgehung: Vertrauen entsteht über echte Signatur, nicht über
  Tarnung.
