# Changelog

Änderungen werden **pro Bereich** dokumentiert. Versionsformat: `YYYY.MM.DD.HH.MM.SS`.

← [Zur Übersicht](../README.md) · [Alle Dokumente](../docs/README.md)

---

## Wo steht was

| | |
|---|---|
| **[Release-Verlauf](releases.md)** | Die Versionsgeschichte des Gesamtprojekts — was in welchem Release dazukam. Stand früher in der Haupt-README. |
| [Agents](agents.md) | Linux- und Windows-Installer, Forwarder |
| [Backend](backend.md) | FastAPI-API |
| [Frontend](frontend.md) | Vue-Oberfläche |
| [Syslog](syslog.md) | Syslog-Empfänger |
| [Datenbank & Deployment](database.md) | Postgres-Image, Compose, `install.sh` |

---

## Aktuelle Stände

**Projekt-/Release-Version:** `2026.10.18.22.00.00`

Sie steht an fünf Stellen und muss bei jedem Release überall mitwandern:

| Ort | Bedeutung |
|-----|-----------|
| `VERSION` | **Maßstab für die Update-Prüfung** — wird gegen den Stand auf GitHub verglichen |
| `backend/app/config.py` → `app_version` | Version in der API und in Sicherungen |
| `frontend/package.json` | Version im Seitenmenü |
| `install.sh` → `LOGBOT_VERSION` | Version in der Installer-Ausgabe |
| `CHANGELOG/releases.md` | der Eintrag zum Release |

> Die Haupt-README trägt **bewusst keine Version** mehr: Sie beschreibt, was
> LogBot ist und kann — nicht, welcher Stand gerade aktuell ist. Das steht in
> `VERSION` und hier.

| Bereich | Aktuelle Version |
|---------|------------------|
| Agents | 2026.10.11.20.00.00 |
| Backend | 2026.10.18.22.00.00 |
| Frontend | 2026.10.18.22.00.00 |
| Syslog | 2026.10.11.20.00.00 |
| Datenbank / Deployment | 2026.10.11.20.00.00 |

---

## Konventionen

- Neueste Version steht oben.
- Kategorien: **Added**, **Changed**, **Fixed**, **Removed**, **Security**
  (bzw. Neu, Geändert, Behoben, Entfernt, Sicherheit).
- Ein Änderungsblock pro Version; die Version entspricht dem `Version:`-Zeitstempel
  im Datei-Kopf des jeweiligen Bereichs.
- Nur den Bereich versionieren und eintragen, der tatsächlich geändert wurde.
- Ein Eintrag sagt, **was** sich geändert hat und **warum** — nicht, welche Zeile
  angefasst wurde. Bei Fehlerbehebungen gehört die Ursache dazu.
