# Agent als Synology-Paket

Der RIMPERIO-Agent als Paket für **Synology DSM 7**. Gedacht für Standorte, an denen nur das NAS steht: Der Agent
läuft auf dem NAS selbst, baut **nur ausgehend HTTPS** zum RIMPERIO-Server auf und spricht im NAS mit DSM unter
`127.0.0.1`. Kein offener Port, kein VPN, keine Portfreigabe am Router.

← [Installer](../README.md) · [Agent](../../README.md) · [NAS an einem anderen Standort](../../../docs/rmm/nas.md#nas-an-einem-anderen-standort)

---

## Installieren

1. Paket laden: [`rimperio-agent-synology.spk`](https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download/rimperio-agent-synology.spk)
   (ein Paket für alle unterstützten CPUs).
2. In RIMPERIO unter *Rechner → Agent hinzufügen* die **Server-Adresse** und den **Zugangsschlüssel** ablesen.
   Der Abschnitt „Synology-NAS“ zeigt beides zusammen mit dem Link zum Paket.
3. DSM: *Paket-Zentrum → Manuelle Installation* → Datei wählen. DSM weist darauf hin, dass das Paket nicht von
   Synology stammt – bestätigen.
4. Der Assistent fragt:

   | Feld | |
   |---|---|
   | Server-Adresse | z. B. `https://rmm.example.de` |
   | Standort | erscheint in RIMPERIO als Standort des Agents |
   | Zugangsschlüssel | Einladung oder Generalschlüssel. Er dient nur der ersten Anmeldung und wird danach gelöscht. |
   | Zertifikat des Servers nicht prüfen | nur fürs Labor mit selbst signiertem Server-Zertifikat |

5. Nach dem Start meldet sich der Agent selbst an. Unter *Rechner* steht das NAS mit dem System
   „Synology DSM 7.x“. Danach in RIMPERIO: *Geräte → NAS → NAS verbinden → Über einen Agent am Standort*. Den Agent
   wählen; Adresse `127.0.0.1` und Port `5001` sind dann schon eingetragen. Anmelden wie gewohnt.

## Was das Paket tut – und was nicht

| | |
|---|---|
| **Läuft als** | Paketkonto `rimperio-agent` (DSM 7: `run-as: package`), **nicht als root** |
| **Verbindungen** | ausgehend HTTPS zum RIMPERIO-Server; im NAS zu `127.0.0.1:5001` (DSM) und `127.0.0.1:161` (SNMP) |
| **Weiterleitung** | nur an lokale Adressen (privat, Loopback, Link-Local, CGNAT). Damit kann ein Agent auf dem NAS auch weitere Geräte im selben Netz für RIMPERIO erreichen. |
| **Meldet** | CPU, RAM, Platten, Laufzeit, Inventur mit Modell und DSM-Version (`appliance`) |
| **Befehle aus der Oberfläche** | laufen als Paketkonto, ohne root – also kein Patchmanagement des NAS über den Agent. DSM- und Paket-Updates macht RIMPERIO über die DSM-Schnittstelle (wie bei jedem NAS). |
| **Daten** | `/var/packages/rimperio-agent/var/` – `etc/agent.json` (0600, mit Agent-Token), `rimperio-agent.log` |

## Betrieb

| | |
|---|---|
| Starten / Stoppen | Paket-Zentrum → RIMPERIO Agent → Ausführen / Stoppen |
| Protokoll | `/var/packages/rimperio-agent/var/rimperio-agent.log` (per SSH als Administrator mit `sudo`; beim Start ab 5 MB rotiert) |
| Aktualisieren | neues `.spk` manuell installieren. DSM fragt nichts; die Anmeldung bleibt. |
| Deinstallieren | Paket-Zentrum → Deinstallieren. Der Agent meldet sich vorher am Server ab. Das NAS bleibt in RIMPERIO; sein Weg über diesen Agent fällt weg. |
| Aus RIMPERIO entfernen („Entfernen und deinstallieren“) | Der Agent meldet sich ab, löscht seine Anmeldung und hält an. Das Paket selbst entfernt man in DSM. |
| Falsche Server-Adresse | Paket deinstallieren und neu installieren. |

## Voraussetzungen und Grenzen

- **DSM 7.0 oder neuer.** DSM 6.2: stattdessen einen Agent auf einem anderen Rechner am Standort nehmen.
- **CPU:** x86_64 (Intel/AMD), aarch64 (ARMv8, z. B. Realtek RTD1296/1619) und armv7 (z. B. Alpine, Armada 38x).
  Sehr alte ARMv5- und PowerPC-Modelle werden nicht unterstützt. Das Paket meldet das bei der Installation.
- Das Paket ist **nicht von Synology signiert** (wie jedes Paket außerhalb des Synology-Katalogs).

## Bauen

```bash
bash agent/installer/synology/build-spk.sh          # baut die drei Programme mit Go
BIN_DIR=agent/bin bash agent/installer/synology/build-spk.sh   # nimmt fertige Linux-Binaries
```

Ergebnis: `agent/bin/rimperio-agent-synology.spk`. Die Release-Pipeline (`agent-release.yml`) baut es bei jedem
Tag mit und hängt es an das Release. Die CI prüft bei jedem Push den Aufbau und einen Probelauf von `postinst`.

| Datei | |
|---|---|
| `scripts/start-stop-status` | startet `rimperio-agent foreground` mit `RIMPERIO_HOME` = Datenverzeichnis des Pakets, wählt das Programm nach `uname -m` |
| `scripts/postinst` | legt Server, Schlüssel und Standort aus dem Assistenten ab (`rimperio-agent bootstrap`) |
| `scripts/preuninst` | meldet beim Deinstallieren ab (`rimperio-agent cleanup`), nicht bei einem Update |
| `conf/privilege` | Paketkonto statt root |
| `WIZARD_UIFILES/install_uifile` | der Assistent |
| `common.sh` | Programm passend zur CPU |
