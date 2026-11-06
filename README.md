<div align="center">

<img src="docs/assets/rimperio-icon.svg" alt="RIMPERIO" width="140">

# RIMPERIO

**Logs, Überwachung und Fernwartung – selbst gehostet, ein Server, eine Oberfläche.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/Phydran6/RIMPERIO-Server?include_prereleases)](https://github.com/Phydran6/RIMPERIO-Server/releases)
[![CI](https://github.com/Phydran6/RIMPERIO-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/Phydran6/RIMPERIO-Server/actions/workflows/ci.yml)

</div>

RIMPERIO sammelt die Logs aller Systeme im Netz und wartet die eigenen Rechner aus der
Ferne. Server, Arbeitsplätze, Switches, Firewalls und die FRITZ!Box melden hierher. Auf
Windows- und Linux-Rechnern läuft zusätzlich ein schlanker Agent, über den sich Befehle,
Skripte, Updates und Prüfungen steuern lassen. Ein Docker-Stack, ein Installationsbefehl,
alles im Browser.

## Was RIMPERIO kann

| Bereich | Funktionen |
|---|---|
| **Logs** | Syslog UDP/TCP 514, HTTPS-Agents für Linux/Windows, Parser (RFC 5424/3164, UniFi, Cisco, Fortinet, JSON, Netfilter), Suche, Filter, Export, Aufbewahrung je Gerät, Archivierung |
| **Fernwartung** | RIMPERIO-Agent (Windows `.exe`/`.msi`, Linux): Live-Werte, Inventur, Befehle und Skript-Bibliothek, Patchmanagement, Checks (Dienst, Port, Ping, Platte, Skript …), Alarme |
| **Netz** | Netzwerk-Discovery (Subnetze, SNMP), Einbindung von Linux-Rechnern per SSH |
| **Melden & Auswerten** | Mail, Telegram, Webhook/n8n; KI-Auswertung (Claude, ChatGPT, Open WebUI lokal); Handy-App |
| **Zugang** | Benutzer und Rollen, MFA, Passkeys, LDAP, Microsoft 365 (SSO), Zugangsschlüssel und Einladungen |
| **Betrieb** | Updates per Klick, Sicherungen (auch verschlüsselt), Konsole im Browser, Container-Verwaltung, Plattenwächter, Systemtagebuch, TLS/Reverse Proxy |

## Schnellstart

**Server** – auf einem Linux-Host als root. Der Assistent zeigt, was schon installiert ist,
und fragt alles Weitere ab:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/server/setup.sh | sudo bash
```

Der Assistent fragt auch die **Domain für HTTPS** ab und zeigt vor dem Start den fertigen
Einzeiler – direkt geht es so ([alle Optionen](docs/install/README.md#https-gleich-bei-der-installation)):

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/install.sh \n  | sudo bash -s -- --domain rmm.example.de --email du@example.de --yes
```

Danach: Oberfläche unter `https://DOMAIN` bzw. `http://SERVER-IP` (Anmeldung `admin` / `admin`,
**sofort ändern**), API-Doku unter `/api/docs`, Syslog auf Port 514.

**Rechner anbinden** – in der Oberfläche unter *Fernwartung → Rechner → Agent hinzufügen*
stehen die fertigen Befehle (PowerShell, MSI, Linux) samt Server-Adresse.
Für reine Log-Lieferung gibt es die Log-Agents: [agent/logship](agent/logship/README.md).

## Wegweiser

| Ich will … | Hier |
|---|---|
| installieren, aktualisieren, deinstallieren | [docs/install](docs/install/README.md) · [docs/updates](docs/updates/README.md) |
| den Betrieb verstehen (Platte, Aufbewahrung, Wächter) | [docs/operate](docs/operate/README.md) · [docs/backup](docs/backup/README.md) |
| Rechner fernwarten, Agent bauen und signieren | [agent](agent/README.md) · [agent/installer](agent/installer/README.md) |
| Logs von Geräten einsammeln | [agent/logship](agent/logship/README.md) · [docs/integrations](docs/integrations/README.md) |
| die API ansprechen | [docs/api](docs/api/README.md) |
| wissen, was sich geändert hat | [docs/changelog](docs/changelog/README.md) |

> **Herkunft:** RIMPERIO vereint die frühere RMM-Fassung mit dem Log-Server
> [LogBot](https://github.com/Phydran6/Logbot-Server). LogBot bleibt nutzbar, wird aber
> nicht mehr weiterentwickelt – neue Funktionen kommen nur noch hier.

## Download

Agent-Dateien (Windows `.exe`/`.msi`, Linux amd64/arm64) gibt es unter
**[Releases](https://github.com/Phydran6/RIMPERIO-Server/releases/latest)**, ausschließlich
von GitHub Actions aus diesem Quellcode gebaut.

*Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).*
Details: [Code signing policy](#code-signing-policy).

## Code signing policy

*Die Signier-Richtlinie steht auf Englisch, weil die SignPath Foundation sie so
verlangt und prüft.*

Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).

Signed artifacts (`rimperio-agent.exe`, `rimperio-agent.msi`) are built
exclusively from this repository's source code by the GitHub Actions workflow
[`agent-release.yml`](.github/workflows/agent-release.yml) on GitHub-hosted
runners. Every release signing request is approved manually.

### Team roles

| Role | Members |
|---|---|
| Committers and reviewers | [Phydran6](https://github.com/Phydran6) |
| Approvers | [Phydran6](https://github.com/Phydran6) |

Changes from contributors outside the team are reviewed by a committer before
they are merged.

### Privacy policy

This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or operating
it. The RIMPERIO agent communicates **only** with the RIMPERIO server whose
address is configured during installation (`SERVER=…`). That server is operated
by the person or organization that installed the agent, not by this project. The
agent sends system and inventory data (hardware, operating system, installed
software, update status, check results) and runs tasks issued from that server.
No data is sent to the RIMPERIO authors or any other third party.

### Uninstall

Remove *RIMPERIO Agent* via *Settings → Apps* (or
`msiexec /x rimperio-agent.msi`). Without the MSI, run `rimperio-agent uninstall`
as administrator (removes service and configuration), then delete
`C:\Program Files\RIMPERIO Agent\`. On Linux: `sudo rimperio-agent uninstall &&
sudo rm /usr/local/bin/rimperio-agent`. See
[agent/installer/README.md](agent/installer/README.md#deinstallieren).

<div align="center">
<sub>Entwickelt von <a href="https://github.com/Phydran6">Phydran6</a> · MIT-Lizenz</sub>
</div>
