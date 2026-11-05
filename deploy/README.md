# Deployment &amp; Betrieb

Hinweise zum laufenden Server. Installation: der Einzeiler aus der
[Übersicht](../README.md#schnellstart) (`install.sh`).

← [Übersicht](../README.md) · [Architektur](../docs/architektur.md) · [Datenbank](../db/README.md)

---

## Tägliche Handgriffe

```bash
cd /opt/rimperio

docker compose ps                  # Zustand aller Dienste
docker compose logs -f backend     # Backend-Protokoll
docker compose restart backend     # einen Dienst neu starten
docker compose up -d --build        # nach Code-Änderung neu bauen
docker compose down                 # alles anhalten (Daten bleiben in Volumes)
```

---

## Aktualisieren

```bash
cd /opt/rimperio
git pull
docker compose up -d --build
```

Oder einfach den Installer erneut laufen lassen — er zieht den Stand und baut neu.
Die `.env` bleibt dabei unangetastet.

---

## Sichern &amp; Zurückspielen

Wichtig ist die Datenbank (siehe [db/README.md](../db/README.md)) und die `.env`.

```bash
# Sichern
docker compose exec db pg_dump -U rimperio rimperio | gzip > rimperio-$(date +%F).sql.gz
cp .env rimperio.env.backup
```

---

## Voraussetzungen

- Linux (Ubuntu 22.04+ oder vergleichbar), x86_64 oder ARM64
- Docker mit Compose-Plugin *(installiert `install.sh` bei Bedarf selbst)*
- Root-Zugriff
- ~1 GB RAM und ~3 GB Platte für den Stack; mehr je nach Zahl der Agents und
  Aufbewahrung der Messwerte

---

## Varianten

- **HTTPS:** `RIMPERIO_SITE_ADDRESS=domain` in der `.env` → Caddy macht den Rest.
- **Externe Datenbank:** im Backend `DB_HOST`/`DB_PORT`/`POSTGRES_*` auf die
  externe Instanz zeigen lassen und den `db`-Dienst aus der Compose-Datei nehmen.
- **Mehr Aufbewahrung:** Messwerte werden nach 14 Tagen aufgeräumt
  (`TELEMETRY_RETENTION_DAYS` in [`backend/app/main.py`](../backend/app/main.py)).
