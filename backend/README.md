# Backend

Die API von RIMPERIO — FastAPI auf PostgreSQL. Nimmt Agents an, speichert Messwerte,
reicht Befehle durch, pflegt Alarme und verwaltet Benutzer.

← [Übersicht](../README.md) · [Architektur](../docs/architektur.md)

---

## Technik

| | |
|---|---|
| **Framework** | FastAPI (async) |
| **Datenbank** | PostgreSQL 17 über SQLAlchemy 2.0 (async, asyncpg) |
| **Auth** | Benutzer: JWT-Sitzungs-Token · Agents: opakes Token (serverseitig nur als SHA-256) |
| **Passwörter** | Argon2 |
| **Doku** | OpenAPI/Swagger unter `/api/docs` |

Das Schema legt das Backend beim Start selbst an (`create_all`); ein eigener
Hintergrunddienst erkennt Offline-Agents und räumt alte Messwerte weg.

---

## Wichtige Endpunkte

| Zweck | Route | Zugang |
|---|---|---|
| Anmeldung | `POST /api/auth/login` | — |
| Agent anmelden | `POST /api/agents/enroll` | Enrollment-Geheimnis |
| Agent-Check-in (Messwerte + Befehle) | `POST /api/agents/checkin` | Agent-Token |
| Inventur / Ergebnisse | `POST /api/agents/inventory` · `/results` | Agent-Token |
| Agents auflisten/ansehen | `GET /api/agents` · `/api/agents/{uid}` | Sitzung |
| Befehl einreihen | `POST /api/agents/{uid}/commands` | operator+ |
| Dashboard | `GET /api/dashboard/summary` | Sitzung |
| Alarme | `GET /api/alerts` · `POST /api/alerts/{id}/resolve` | Sitzung / operator+ |
| Benutzer | `GET/POST/PATCH/DELETE /api/users` | admin |
| Einstellungen | `GET/PUT /api/settings…` | Sitzung / admin |
| Gesundheit | `GET /api/health` | — |

Vollständig und zum Ausprobieren: **`/api/docs`**.

---

## Aufbau

| Datei | Inhalt |
|---|---|
| [`app/main.py`](app/main.py) | App, Lifespan (Schema, Erst-Admin, Hintergrunddienst), Router |
| [`app/config.py`](app/config.py) | Einstellungen aus der Umgebung |
| [`app/database.py`](app/database.py) | Engine, Session, Basisklasse |
| [`app/models.py`](app/models.py) | Benutzer, Agents, Telemetrie, Befehle, Alarme, Settings |
| [`app/schemas.py`](app/schemas.py) | Ein-/Ausgabeformen (Pydantic) |
| [`app/security.py`](app/security.py) | Passwörter, Token, Zugangs-Abhängigkeiten |
| [`app/service.py`](app/service.py) | Online-Zustand, Schwellen, Alarme |
| [`app/routes/`](app/routes) | die Endpunkte je Thema |

---

## Lokal

```bash
# Am einfachsten über den Stack:
docker compose up -d --build
docker compose logs -f backend

# Direkt (braucht eine erreichbare PostgreSQL und gesetzte Umgebung):
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Die wichtigsten Umgebungswerte stehen in [`.env.example`](../.env.example).
