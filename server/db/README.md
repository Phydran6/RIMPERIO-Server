# Datenbank

PostgreSQL. Schema, Migration und das Major-Upgrade.

← [Übersicht](../../README.md) · [Alle Dokumente](../../docs/README.md)

| Datei | Wofür |
|-------|-------|
| [`init.sql`](init.sql) | Schema beim ersten Start. Läuft nur, wenn das Volume leer ist. |
| [`migrate.sh`](migrate.sh) | PostgreSQL-Major-Upgrade **mit** Datenerhalt |

## Tabellen

| Tabelle | Inhalt |
|---------|--------|
| `logs` | die Logzeilen — mit Abstand die größte |
| `agents` | bekannte Geräte samt Aufbewahrungsregeln |
| `agent_tokens` | Token für den HTTPS-Ingest |
| `users` | Konten, Rollen, MFA |
| `mfa_backup_codes`, `webauthn_credentials` | Einmal-Codes und Passkeys |
| `app_login_tokens` | kurzlebige Token für die App-Anmeldung per QR |
| `webhooks` | Webhook-Definitionen |
| `settings` | alles, was im Web-UI eingestellt wird |

## Wie Schemaänderungen laufen

Nicht über Alembic, sondern beim Start des Backends: fehlende Spalten, Indizes
und Tabellen werden per `ALTER TABLE … IF NOT EXISTS` nachgezogen
(`backend/app/main.py`, die `ensure_*`-Funktionen).

**Warum so:** Ein Update soll ohne zusätzlichen Schritt durchlaufen — auch auf
einer Installation, die drei Versionen übersprungen hat. Indizes auf `logs`
entstehen `CONCURRENTLY`, damit der Aufbau auf einer Tabelle mit Millionen
Zeilen keine Schreibzugriffe blockiert.

Für eine **externe** Datenbank heißt das: Der Benutzer braucht Rechte zum
Ändern der Tabellen.

## Major-Upgrade (z. B. 16 → 17)

PostgreSQL-Datenverzeichnisse sind zwischen Major-Versionen **nicht**
kompatibel. Ein bestehendes 16er-Volume startet unter einem 17er-Image nicht.
Ein Image-Tausch allein reicht also nie.

### Notbremse

Läuft der Container nach einem Update in eine Fehlerschleife, hilft eine Zeile —
ohne Datenverlust:

In der `.env` auf PostgreSQL 16 zurückstellen:

```bash
echo "POSTGRES_VERSION=16" >> .env
```

Neu starten:

```bash
docker compose up -d
```

Danach in Ruhe migrieren.

### Weg 1 — Daten behalten

Ziel = POSTGRES_VERSION aus .env:

```bash
sudo bash db/migrate.sh
```

Andere Zielversion:

```bash
sudo bash db/migrate.sh 18
```

Ohne Rückfrage:

```bash
sudo bash db/migrate.sh 17 -y
```

Als Einzeiler, findet `/opt/rimperio` selbst:

```bash
curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/db/migrate.sh | sudo bash
```

Das Skript sichert logisch (`pg_dump -Fc`), baut das Volume mit der neuen
Version neu auf und spielt die Daten wieder ein. Die Sicherungsdatei bleibt
danach als `.dump` liegen.

### Weg 2 — Daten nicht nötig

Löscht die Volumes samt alter DB:

```bash
docker compose down -v
```

Frische DB, init.sql läuft neu:

```bash
docker compose up -d --build
```

Anmeldung danach wieder `admin`/`admin`.

## Externe Datenbank

Siehe [`deploy/`](../deploy/README.md). Kurz:

Schema auf der externen Datenbank anlegen:

```bash
psql "postgresql://user:pw@db.example.com:5432/rimperio" -f db/init.sql
```

RIMPERIO damit starten:

```bash
docker compose -f docker-compose.yml -f server/deploy/external-db.yml up -d
```

`DB_SSLMODE=require` gehört dazu, sobald die Datenbank nicht im selben
Docker-Netz steht.

## Sichern

- **Im Web-UI:** [System → Sicherung](../../docs/backup/README.md) — granular, als
  ZIP, optional verschlüsselt.
- **Klassisch:**

  Sichern:

  ```bash
  docker compose exec postgres pg_dump -U rimperio rimperio > backup.sql
  ```

  Einspielen:

  ```bash
  docker compose exec -T postgres psql -U rimperio rimperio < backup.sql
  ```
