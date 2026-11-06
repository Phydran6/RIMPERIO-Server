# Architektur

Wie die Teile zusammenspielen — vom Dienst auf dem verwalteten Rechner bis zur
Kachel im Browser.

← [Übersicht](../README.md) · [Bedienung](bedienung.md) · [Agent](../agent/README.md)

---

## Das Gesamtbild

```mermaid
flowchart TB
    subgraph R["Verwaltete Rechner"]
        direction LR
        RW["Windows-Agent<br/>Dienst (Go)"]
        RL["Linux-Agent<br/>systemd (Go)"]
    end

    subgraph S["RIMPERIO-Server — ein Docker-Stack"]
        direction TB
        CA["caddy<br/>80/443, TLS"]
        BE["backend — FastAPI<br/>Auth, Enrollment, Check-in,<br/>Alarme, Benutzer"]
        FE["frontend — Vue 3<br/>Oberfläche"]
        DB[("postgres<br/>Agents, Telemetrie,<br/>Befehle, Alarme, Settings")]
        BG["Hintergrunddienst<br/>Offline-Erkennung, Aufräumen"]
    end

    B["Browser"]

    RW & RL -->|"HTTPS + Agent-Token"| CA
    CA -->|"/api/*"| BE
    CA -->|"/"| FE
    BE --> DB
    BG --- BE
    B -->|"REST"| CA
```

---

## Datenwege

Ein Agent redet ausschließlich **ausgehend** mit dem Server — er wird nie von
außen angesprochen.

| Weg | Wer | Wie | Wohin |
|---|---|---|---|
| **Enrollment** | neuer Agent | `POST /api/agents/enroll` mit Enrollment-Geheimnis | Backend → bekommt Agent-Token |
| **Check-in** | jeder Agent | `POST /api/agents/checkin`, `Bearer <AGENT-TOKEN>`, mit Messwerten | Backend → PostgreSQL, Antwort: offene Befehle |
| **Inventur** | jeder Agent | `POST /api/agents/inventory` | Backend → PostgreSQL |
| **Ergebnisse** | jeder Agent | `POST /api/agents/results` | Backend → PostgreSQL |

Und heraus:

| Weg | Wofür |
|---|---|
| **Browser** (`/api/*`) | Dashboard, Agents, Alarme, Einstellungen |
| **OpenAPI** (`/api/docs`) | API ausprobieren und einbinden |

---

## Drei Sorten Zugang

```mermaid
flowchart LR
    U["Benutzer"] -->|"Passwort"| T1["Sitzungs-Token<br/>(JWT, kurzlebig)"]
    N["neuer Agent"] -->|"Enrollment-Geheimnis"| T2["Agent-Token<br/>(einmalig ausgegeben)"]
    T1 --> API["API /api/*"]
    T2 --> ING["Ingest /api/agents/checkin…"]
```

- **Sitzungs-Token** — aus `POST /api/auth/login`, signiertes JWT, läuft ab
  (`RIMPERIO_SESSION_MINUTES`). Rollen: **admin**, **operator**, **readonly**.
- **Agent-Token** — bei der Anmeldung einmal ausgegeben, serverseitig nur als
  SHA-256 gespeichert. Darf nur einchecken, Inventur und Ergebnisse liefern.
- **Enrollment-Geheimnis** — gemeinsamer Vertrauensanker für die Erst-Anmeldung.
  Jederzeit im Browser wechselbar; bestehende Agents bleiben verbunden.

---

## Der Pull-Takt

```mermaid
sequenceDiagram
    participant A as Agent
    participant BE as Backend
    participant DB as PostgreSQL
    loop alle 60 s (Standard)
        A->>BE: POST /checkin (CPU, RAM, Platten …)
        BE->>DB: Telemetrie speichern, Schwellen prüfen
        BE-->>A: offene Befehle (pending → running)
        A->>A: ausführen (Shell, Zeitlimit)
        A->>BE: POST /results (stdout, stderr, Code)
        BE->>DB: Befehl abschließen (done/error/timeout)
    end
```

Ein Befehl wandert so durch die Zustände: **pending** (eingereiht) →
**running** (vom Agent abgeholt) → **done** / **error** / **timeout**.

---

## Dienste und Häfen

| Dienst | Image / Bau | Port | Bemerkung |
|---|---|---|---|
| `caddy` | `caddy:2-alpine` | **80**, **443** | einziger Weg von außen; TLS automatisch bei gesetzter Domain |
| `backend` | FastAPI | 8000 *(intern)* | API, Auth, Ingest, Hintergrunddienst |
| `frontend` | Vue 3 hinter nginx | 80 *(intern)* | ausgelieferte Oberfläche |
| `postgres` | `postgres:17-alpine` | 5432 *(intern)* | nicht nach außen veröffentlicht |

---

## Alarme

Das Backend prüft jede frische Telemetrie gegen die Schwellen
([Einstellungen](bedienung.md#einstellungen)): CPU, RAM, kritischste Platte.
Der Hintergrunddienst erkennt zusätzlich **offline** (zu lange kein Check-in).
Alarme sind selbstheilend — fällt der Grund weg, werden sie automatisch
aufgelöst; von Hand auflösen geht trotzdem.

---

## Weiter

- [Bedienung](bedienung.md)
- [Agent](../agent/README.md)
- [Installer &amp; Signatur](../installer/README.md)
