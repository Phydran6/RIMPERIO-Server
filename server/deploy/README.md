# Compose-Varianten

Der Standard-Stack steht in [`../docker-compose.yml`](../../docker-compose.yml).
Hier liegen die Varianten, die man dazuschaltet.

← [Übersicht](../../README.md) · [Alle Dokumente](../../docs/README.md)

| Datei | Wofür |
|-------|-------|
| [`external-db.yml`](external-db.yml) | Daten auf einem eigenen Datenbankserver, RIMPERIO läuft nur als Anwendung |
| [`hardened.yml`](hardened.yml) | Ohne erweiterte Container-Rechte |
| [`optional.yml`](optional.yml) | Portainer, Tugtainer, n8n, Open WebUI, Postfix — je hinter einem Profil |

## Verwendung

Externe Datenbank:

```bash
docker compose -f docker-compose.yml -f server/deploy/external-db.yml up -d
```

Gehärtet, ohne erweiterte Container-Rechte:

```bash
docker compose -f docker-compose.yml -f server/deploy/hardened.yml up -d
```

Mit Zusatzdiensten (hier Portainer):

```bash
docker compose -f docker-compose.yml -f server/deploy/optional.yml --profile portainer up -d
```

Dauerhaft, ohne jedes Mal beide Dateien zu nennen — in die `.env`:

```
COMPOSE_FILE=docker-compose.yml:server/deploy/optional.yml
COMPOSE_PROFILES=portainer,tugtainer
```

Genau das schreibt der Installer, wenn man dort Zusatzdienste auswählt, und die
Oberfläche unter *RIMPERIO-Server → Zusatzdienste*.

## Externe Datenbank

In der `.env` entweder `DATABASE_URL` oder die Einzelwerte
(`DB_HOST`, `DB_PORT`, …) setzen. Steht die Datenbank außerhalb des eigenen
Netzes, gehört `DB_SSLMODE=require` dazu — sonst laufen Zugangsdaten und Logs im
Klartext.

Das Schema einmalig anlegen:

```bash
psql "postgresql://user:pw@db.example.com:5432/rimperio" -f db/init.sql
```

Der mitgelieferte PostgreSQL-Container bleibt dabei stehen, das Volume
unangetastet — der Rückweg ist offen.

## Gehärtet

Nimmt dem Backend `privileged`, `pid: host`, `seccomp=unconfined` und
`SYS_BOOT`. RIMPERIO läuft unverändert: Logs annehmen, anzeigen, filtern,
archivieren, Reverse Proxy, Agents.

**Was dann nicht mehr geht:** Neustart-Knopf, Update über die Oberfläche,
Terminal, Zusatzdienst-Schalter und das automatische Übernehmen der Host-DNS.
Der Systemcheck sagt das ausdrücklich an; Updates laufen weiter über den
Einzeiler.

**Warum es die Wahl überhaupt gibt:** Die Standardrechte heben die Trennung
zwischen Container und Server praktisch auf. Wer es schafft, im Backend Code
auszuführen, hat den ganzen Server. Wer die Bequemlichkeit nicht braucht,
sollte sie nicht bezahlen.

## Zusatzdienste

Siehe [Installation → Zusatzdienste](../../docs/install/README.md#zusatzdienste)
und [Integrationen](../../docs/integrations/README.md).
