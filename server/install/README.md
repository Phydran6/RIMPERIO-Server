# Systemprüfung

← [Übersicht](../README.md) · [Installation](../docs/install/README.md)

[`preflight.sh`](preflight.sh) beantwortet eine Frage: **Trägt dieser Rechner
RIMPERIO — und die Zusatzdienste, die man dazuhaben will?**

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

Ohne Repository, direkt von GitHub:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/server/install/preflight.sh | sudo bash
```

`install.sh` ruft das Skript von selbst auf — nachdem die Auswahl getroffen ist,
denn die Anforderungen hängen davon ab.

## Was geprüft wird

| Bereich | Punkte |
|---------|--------|
| System | Kernel, Architektur, Betriebssystem, Paketmanager, Container-Umgebung |
| Rechte | root |
| Ausstattung | RAM, Swap, freier Plattenplatz, Prozessorkerne — gegen die Auswahl gerechnet |
| Docker | vorhanden, Dienst antwortet, Compose-**Plugin** (nicht das alte `docker-compose`) |
| Ports | 80, 443, 514, 5432 plus die der gewählten Dienste |
| Netz | DNS-Auflösung, GitHub erreichbar |
| Uhrzeit | wird per NTP gestellt |

## Die drei Antworten

| Rückgabe | Bedeutung |
|---------:|-----------|
| `0` | passt |
| `2` | wird knapp — läuft, kann aber langsam werden |
| `1` | geht nicht |

**Warum nicht einfach ja/nein:** Ein kleiner Server trägt RIMPERIO problemlos —
nur eben nicht mit Portainer, n8n und Postfix obendrauf. Das gehört gesagt,
nicht verboten. Ein harter Ausschluss bleibt dem vorbehalten, was wirklich nicht
geht: 32-Bit-ARM (keine passenden Images für PostgreSQL und Caddy), fehlendes
Compose-Plugin, zu wenig Speicher für PostgreSQL.

Auch ein `1` lässt sich im Installer übergehen — dann aber als bewusste
Entscheidung, mit ausdrücklicher Rückfrage.

## Bedarf je Baustein

| Baustein | RAM | Platte |
|----------|----:|-------:|
| RIMPERIO | 1024 MB | 4096 MB |
| Portainer | 256 MB | 400 MB |
| Watchtower | 128 MB | 150 MB |
| n8n | 768 MB | 1200 MB |
| Postfix | 128 MB | 200 MB |

Die Werte stehen oben in `preflight.sh` und lassen sich dort anpassen. Es sind
Anhaltswerte aus dem Betrieb, keine harten Grenzen.
