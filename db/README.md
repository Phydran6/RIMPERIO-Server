# Datenbank

PostgreSQL 17 im Container `db`. **Das Schema legt das Backend selbst an**
(`create_all` beim Start) — es gibt hier bewusst kein `init.sql`, das
auseinanderlaufen könnte. Die Modelle stehen in
[`backend/app/models.py`](../backend/app/models.py).

← [Übersicht](../README.md) · [Deployment](../deploy/README.md)

## Tabellen (Kurzfassung)

| Tabelle | Inhalt |
|---|---|
| `users` | Benutzer, Rolle, Passwort-Hash (Argon2) |
| `agents` | verwaltete Rechner, Token-Hash, letzte Werte, Inventur |
| `telemetry` | Zeitreihe der Messwerte (automatisch nach 14 Tagen aufgeräumt) |
| `commands` | eingereihte/ausgeführte Befehle samt Ausgabe |
| `alerts` | aktive und aufgelöste Alarme |
| `settings` | Laufzeiteinstellungen (Schwellen, Enrollment-Geheimnis) |

## Handgriffe

```bash
# In die Datenbank schauen
docker compose exec db psql -U rimperio -d rimperio -c "\dt"

# Sicherung
docker compose exec db pg_dump -U rimperio rimperio | gzip > rimperio-$(date +%F).sql.gz

# Zurückspielen (Stack läuft, DB leer)
gunzip -c rimperio-DATUM.sql.gz | docker compose exec -T db psql -U rimperio -d rimperio
```

Der Port 5432 wird **nicht** nach außen veröffentlicht — die Datenbank ist nur im
Docker-Netz erreichbar.
