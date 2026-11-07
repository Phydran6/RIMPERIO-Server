# Installation

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

## Der kurze Weg

Mit Führung: Der Setup-Assistent fragt jede Option ab und zeigt vor dem Start
den passenden Einzeiler.

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/server/setup.sh | sudo bash
```

Oder direkt der Installer:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/install.sh | sudo bash
```

Der Installer läuft in dieser Reihenfolge:

1. **5 Sekunden Countdown.** Eine Taste drücken → Rückfragen werden gestellt.
   Nichts drücken → alles läuft mit Standardwerten durch.
2. **Auswahl:** RIMPERIO allein oder mit Zusatzdiensten.
3. **HTTPS:** Domain und Zertifikat (leer = erst mal nur HTTP).
4. **Systemprüfung** gegen genau diese Auswahl.
5. **Docker** wird geholt, falls er fehlt.
6. **Bauen und starten.**

Ohne jede Rückfrage:

```bash
curl -sSL … /install.sh | sudo bash -s -- --yes
```

Mit Zusatzdiensten, unbeaufsichtigt:

```bash
curl -sSL … /install.sh | sudo bash -s -- --with portainer,tugtainer --yes
```

### HTTPS gleich bei der Installation

Der Assistent fragt die Domain ab; der Installer im manuellen Modus ebenso. Direkt im
Einzeiler geht es mit drei Parametern:

| Parameter | Bedeutung |
|---|---|
| `--domain <fqdn>` | Adresse, unter der RIMPERIO per HTTPS erreichbar sein soll, z. B. `rmm.example.de` |
| `--tls letsencrypt` | Zertifikat von Let's Encrypt – die Domain muss öffentlich auf den Server zeigen, Port 80 und 443 offen |
| `--tls internal` | eigenes Zertifikat von Caddy – fürs eigene Netz, der Browser fragt einmal nach |
| `--email <adresse>` | Pflicht für Let's Encrypt (Hinweise zum Zertifikat) |

Ohne `--tls` gilt: mit `--email` Let's Encrypt, sonst `internal`.

```bash
# öffentlich erreichbar, Zertifikat von Let's Encrypt
curl -sSL … /install.sh | sudo bash -s -- --domain rmm.example.de --email du@example.de --yes

# nur im eigenen Netz
curl -sSL … /install.sh | sudo bash -s -- --domain rmm.firma.lan --tls internal --yes
```

Die Werte landen in der `.env` (`RIMPERIO_DOMAIN`, `RIMPERIO_TLS`, `RIMPERIO_ACME_EMAIL`);
beim ersten Start richtet das Backend daraus den Reverse Proxy ein. Danach gilt, was im
Browser unter *Einstellungen → Netzwerk → Reverse Proxy* steht – dort lässt sich alles
ändern, ohne eine Datei anzufassen. Port 80 bleibt immer als Rettungsweg offen.

Aus einem geklonten Repository:

```bash
git clone https://github.com/Phydran6/RIMPERIO-Server.git && cd RIMPERIO-Server && sudo bash install.sh
```

---

## Systemprüfung

Läuft von selbst mit — und auch allein:

Nur RIMPERIO:

```bash
sudo bash server/install/preflight.sh
```

Mit Zusatzdiensten:

```bash
sudo bash server/install/preflight.sh portainer n8n
```

Alles:

```bash
sudo bash server/install/preflight.sh all
```

Ohne Repository:

```bash
curl -sSL …/server/install/preflight.sh | sudo bash
```

Geprüft werden Architektur, Kernel, Betriebssystem, Rechte, RAM, Platte,
Prozessorkerne, Docker samt Compose, belegte Ports, DNS, GitHub-Erreichbarkeit
und ob die Uhr per NTP gestellt wird.

Die Antwort ist bewusst dreistufig:

| Rückgabe | Bedeutung | Folge |
|----------|-----------|-------|
| `0` | passt | läuft durch |
| `2` | wird knapp | Rückfrage, dann weiter |
| `1` | geht nicht | Abbruch (übergehbar) |

**Warum nicht einfach ja/nein?** Ein kleiner Server trägt RIMPERIO problemlos —
nur eben nicht mit Portainer, n8n und Postfix obendrauf. Das gehört gesagt,
nicht verboten. Ein harter Ausschluss ist nur, was wirklich nicht geht: 32-Bit
ARM, fehlendes Compose-Plugin, zu wenig Speicher für PostgreSQL.

Übergehen (auf eigene Verantwortung): `--skip-preflight`.

---

## Hardware-Bedarf

| Baustein | RAM | Platte |
|----------|----:|-------:|
| **RIMPERIO** (Postgres, Backend, Frontend, Caddy, Syslog) | 1024 MB | 4096 MB |
| Portainer | 256 MB | 400 MB |
| Tugtainer | 256 MB | 300 MB |
| n8n | 768 MB | 1200 MB |
| Open WebUI *(ohne Modell)* | 1024 MB | 2500 MB |
| Postfix | 128 MB | 200 MB |

Untergrenze: 768 MB RAM und 2 GB Platte — darunter startet PostgreSQL nicht
zuverlässig. Ab zwei Zusatzdiensten sind zwei Prozessorkerne empfohlen.

Die Werte für die Platte sind der **Grundbedarf**. Logs kommen dazu: Als
Faustzahl braucht eine Million Logzeilen rund 300–500 MB. RIMPERIO räumt ab 80 %
Belegung selbst auf (siehe [Betrieb](../operate/README.md#platte-läuft-voll)).

---

## Zusatzdienste

Fünf Dienste stehen bereit. Keiner läuft, bevor er ausgewählt wird. Alle fünf
sind fremde Images und laufen als `rimperio-ext-*`.

| Dienst | Wofür | Achtung |
|--------|-------|---------|
| **Portainer** | Container im Browser ansehen | braucht den Docker-Socket (nur lesend) |
| **Tugtainer** | prüft, ob für die fremden Images Updates bereitliegen | Docker-Socket nur lesend — es meldet, es tauscht nicht |
| **n8n** | Automatisierung, u. a. für die KI-Auswertung | eigene Oberfläche, eigene Anmeldung |
| **Open WebUI** | KI-Oberfläche, auch für lokale Modelle | das Modell (Ollama) läuft **nicht** hier — `OLLAMA_BASE_URL` zeigt darauf |
| **Postfix** | Mailversand vom Server | konfiguriert wird er später im Web-UI |

> **Watchtower ist raus.** Es hat Images geholt *und* Container eigenmächtig
> ausgetauscht — nachts, ohne Ansage, hinterher schwer zuzuordnen. Tugtainer
> nimmt seinen Platz ein und macht nur eines: nachsehen und melden. Eingespielt
> wird bewusst, unter *System → Container*.

Bei der Installation mit Zusatzdiensten:

```bash
sudo bash install.sh --with portainer,tugtainer
```

Ausdrücklich ohne:

```bash
sudo bash install.sh --no-addons
```

Nachträglich jederzeit im Web-UI unter **System → Zusatzdienste** —
ein Schalter je Dienst, mit Sicherungsfrage davor.

> **Zum Docker-Socket, offen gesagt:** Wer darauf schreiben kann, ist faktisch
> root auf diesem Server. Genau deshalb liegen diese Dienste hinter einem
> Profil, das man bewusst einschaltet, und nicht im Standard-Stack. Portainer
> bekommt den Socket nur lesend — die Oberfläche zeigt dann alles, kann aber
> nichts starten oder löschen. Wer auch verwalten will, nimmt das `:ro` in
> [`server/deploy/optional.yml`](../../server/deploy/optional.yml) heraus.

### Zugangsdaten

Der Installer würfelt sie aus und legt sie in der `.env` ab. Einsehbar sind sie
jederzeit im Web-UI unter **System → Zusatzdienste** — für Administratoren, auf
Klick. Genau dafür ist das gedacht: Ein Passwort, das man nur einmal beim
Installieren sieht, ist zwei Wochen später verloren.

---

## Aktionen des Installers

| Aktion | Wirkung |
|--------|---------|
| `install` *(Standard)* | Installiert; eine vorhandene Installation wird auf Wunsch aktualisiert |
| `update` | Holt den neuen Stand und startet neu gebaute Container |
| `uninstall` | Stoppt und entfernt die Container — **Daten bleiben** |
| `uninstall-purge` | Löscht zusätzlich Volumes, **alle Logs**, die Sicherungs-ZIPs und `/opt/rimperio` |

Was genau bleibt und was weg ist, dazu die Reste danach:
[Deinstallieren](../../README.md#deinstallieren).

**Optionen:** `--dir` · `--repo` · `--branch` · `--ref` · `--with` · `--no-addons` ·
`--skip-preflight` · `--no-build` · `--yes` · `--timeout`
(alle auch als `RIMPERIO_*`-Umgebungsvariable).

Eine vorhandene `.env` wird **nie** überschrieben — sonst passte das
Datenbank-Passwort nicht mehr zum bestehenden PostgreSQL-Volume.

---

## Andere Betriebsarten

Die Compose-Varianten liegen in [`deploy/`](../../server/deploy/README.md):

Externe Datenbank:

```bash
sudo docker compose -f docker-compose.yml -f server/deploy/external-db.yml up -d
```

Ohne erweiterte Container-Rechte:

```bash
sudo docker compose -f docker-compose.yml -f server/deploy/hardened.yml up -d
```

Mit Zusatzdiensten:

```bash
sudo docker compose -f docker-compose.yml -f server/deploy/optional.yml --profile portainer up -d
```

Damit man das nicht jedes Mal tippt, in die `.env`:

```
COMPOSE_FILE=docker-compose.yml:server/deploy/optional.yml
COMPOSE_PROFILES=portainer,tugtainer
```

> **Zur gehärteten Variante:** Sie nimmt dem Backend `privileged`, `pid: host`
> und `SYS_BOOT`. RIMPERIO läuft normal weiter — es entfallen aber der
> Neustart-Knopf, das Update über die Oberfläche, die Konsole, die
> Container-Verwaltung und die Zusatzdienst-Schalter. Der Systemcheck sagt das
> ausdrücklich an.

---

## Nach der Installation

1. **Passwort ändern** — das Startpasswort für `admin` zeigt der Installer am Ende an (es steht auch als `RIMPERIO_ADMIN_INITIAL_PASSWORD` in der `.env`). Ältere Installationen ohne diesen Wert starten mit `admin`/`admin`.
2. **HTTPS einschalten** — Einstellungen → Netzwerk → Reverse Proxy.
   Let's Encrypt, eigenes Zertifikat oder selbstsigniert.
3. **Systemcheck laufen lassen** — System → Systemzustand.
4. **Zugangsschlüssel holen** — Verwaltung → Zugang & Sicherheit → Zugangsschlüssel (am besten eine „Einladung“), dann
   [Rechner anbinden](../../agent/logship/README.md).
5. **Sicherung einrichten** — [System → Sicherung](../backup/README.md).

---

## Wenn etwas klemmt

| Symptom | Ursache |
|---------|---------|
| `DB_PASSWORD muss gesetzt sein` | `.env` fehlt oder ist leer. `install.sh` erneut laufen lassen. |
| Backend startet nicht, alles 502 | `docker compose logs backend` ansehen. Meist die Datenbank. |
| Port 80 belegt | Ein anderer Webserver läuft. Die Systemprüfung sagt das vorher. |
| PostgreSQL startet nicht nach Update | Major-Upgrade nötig — siehe [Datenbank](../../server/db/README.md). |
| Zusatzdienst startet nicht | `docker compose logs <name>`, oder im Web-UI unter System → Zusatzdienste das Protokoll ansehen. |

---

## Weiter

- [Betrieb](../operate/README.md) — der Alltag danach
- [Updates](../updates/README.md) — aktuell bleiben
- [Sicherung](../backup/README.md) — bevor etwas schiefgeht
