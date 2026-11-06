# Bedienung

Die täglichen Handgriffe in der Oberfläche.

← [Übersicht](../README.md) · [Architektur](architektur.md)

---

## Anmelden

Beim ersten Start gibt es ein Konto: **`admin` / `admin`**. Bitte sofort unter
*Einstellungen → Eigenes Passwort* ändern.

Rollen:

| Rolle | Darf |
|---|---|
| **admin** | alles — inklusive Benutzer, Schwellen, Enrollment-Geheimnis |
| **operator** | Agents sehen und steuern, Befehle schicken, Alarme auflösen |
| **readonly** | nur sehen |

---

## Dashboard

Ein Blick: wie viele Agents, wie viele online/offline, offene Alarme, wartende
Befehle — dazu die aktiven Alarme und die Verteilung nach Plattform und Standort.
Die Seite aktualisiert sich von selbst.

---

## Agents

- **Liste** mit Online-Zustand, Standort, System und den letzten Werten für
  CPU/RAM/Platte. Such- und Filterfeld oben.
- **Agent hinzufügen** zeigt die fertigen Installationsbefehle (Windows kurz,
  Windows MSI, Linux) mit bereits eingesetztem Server und Geheimnis.
- **Ein Agent im Detail:**
  - Live-Werte mit kleinem Verlauf,
  - Inventur (OS, Prozessor, RAM, Platten, MAC, Laufzeit, Agent-Version),
  - **Befehl ausführen** — Shell wählen (PowerShell/CMD/Bash/sh), Zeitlimit
    setzen, abschicken; die Ausgabe erscheint im Verlauf, sobald der Agent sie
    zurückmeldet,
  - Standort/Beschreibung pflegen, Agent entfernen (admin).

> Der Agent holt Befehle im Check-in-Takt (Standard 60 s) ab — ein frisch
> abgeschickter Befehl läuft also innerhalb einer Taktlänge an.

---

## Alarme

Liste aller Alarme, standardmäßig nur die aktiven. Die meisten lösen sich selbst
auf, sobald der Grund wegfällt (Agent meldet sich wieder, Auslastung sinkt).
Übrige lassen sich von Hand auflösen (operator/admin).

---

## Einstellungen

- **Alarm-Schwellen** (admin): CPU, RAM, Platte in %, und „offline nach" in
  Sekunden.
- **Enrollment-Geheimnis** (admin): das gemeinsame Geheimnis für neue Agents —
  anzeigen und wechseln. Bestehende Agents bleiben beim Wechsel verbunden.
- **Eigenes Passwort** (alle): altes und neues Passwort.

---

## Benutzer (admin)

Anlegen, Rolle ändern, sperren/entsperren, Passwort zurücksetzen, löschen. Zwei
Sicherungen greifen immer: man kann sich **nicht selbst löschen** und **nicht den
letzten Admin** entfernen oder herabstufen.

---

## HTTPS einschalten

In der `.env` auf dem Server `RIMPERIO_SITE_ADDRESS` auf die eigene Domain setzen
(statt `:80`), dann `docker compose up -d`. Caddy holt automatisch ein
Let's-Encrypt-Zertifikat (Ports 80/443 müssen erreichbar sein, DNS muss zeigen).

---

## Weiter

- [Architektur](architektur.md)
- [Agent](../agent/README.md)
- [Installer &amp; Signatur](../installer/README.md)
