# Frontend

Die Oberfläche von RIMPERIO — Vue 3 mit Tailwind, gebaut mit Vite, ausgeliefert über
nginx. Spricht die API unter derselben Herkunft (`/api`).

← [Übersicht](../README.md) · [Bedienung](../docs/bedienung.md)

---

## Technik

| | |
|---|---|
| **Framework** | Vue 3 (Composition API) |
| **Zustand** | Pinia |
| **Routing** | vue-router (History-Modus, Zugangsschutz) |
| **Design** | Tailwind CSS (dunkles Thema, Markenfarben) |
| **Diagramme** | Inline-SVG-Sparklines — keine Chart-Bibliothek |
| **Build** | Vite |

Die Oberfläche fragt die API im kurzen Takt ab (Polling, 5 s) — damit Online-
Zustand, Messwerte und Befehlsverlauf von selbst aktuell bleiben.

---

## Ansichten

| Datei | Zweck |
|---|---|
| [`views/Login.vue`](src/views/Login.vue) | Anmeldung |
| [`views/Dashboard.vue`](src/views/Dashboard.vue) | Kennzahlen, aktive Alarme, Verteilung |
| [`views/Agents.vue`](src/views/Agents.vue) | Liste + „Agent hinzufügen" mit fertigen Befehlen |
| [`views/AgentDetail.vue`](src/views/AgentDetail.vue) | Live-Werte, Inventur, Befehl ausführen, Verlauf |
| [`views/Alerts.vue`](src/views/Alerts.vue) | Alarme ansehen/auflösen |
| [`views/Settings.vue`](src/views/Settings.vue) | Schwellen, Enrollment-Geheimnis, Passwort |
| [`views/Users.vue`](src/views/Users.vue) | Benutzerverwaltung (admin) |

Gemeinsames: [`components/`](src/components) (Rahmen, Kacheln, Sparkline, Code-Block),
[`stores/auth.js`](src/stores/auth.js), [`api.js`](src/api.js), [`router/`](src/router).

---

## Lokal

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173, /api wird an :8000 weitergereicht
npm run build   # erzeugt dist/
```
