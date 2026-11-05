# Caddy

Reverse Proxy und TLS — der einzige Dienst, der von außen erreichbar ist.

← [Übersicht](../README.md) · [Architektur](../docs/architektur.md)

- `/api/*` → `backend:8000`
- alles andere → `frontend:80`

Die Adresse steuert `RIMPERIO_SITE_ADDRESS` aus der `.env`:

- `:80` — HTTP auf allen Schnittstellen (einfacher Erststart)
- `rmm.example.de` — Caddy holt automatisch ein Let's-Encrypt-Zertifikat und
  schaltet HTTPS ein (Ports 80/443 erreichbar, DNS zeigt auf den Host).

Konfiguration: [`Caddyfile`](Caddyfile). Zertifikate und Zustand liegen in den
Docker-Volumes `caddy_data` / `caddy_config`.
