# Changelog

Änderungen werden **pro Bereich** dokumentiert. Versionsformat: **[Semantic Versioning 2.0.0](https://semver.org/lang/de/)**
— `MAJOR.MINOR.PATCH`, ab `0.0.1`. Ältere Einträge tragen noch den Zeitstempel `JJJJ.MM.TT.HH.MM.SS`.

← [Zur Übersicht](../README.md) · [Alle Dokumente](../README.md)

---

## Wo steht was

| | |
|---|---|
| **[Release-Verlauf](releases.md)** | Die Versionsgeschichte des Gesamtprojekts — was in welchem Release dazukam. Stand früher in der Haupt-README. |
| [Agents](agents.md) | Linux- und Windows-Installer, Forwarder |
| [Backend](backend.md) | FastAPI-API |
| [Frontend](frontend.md) | Vue-Ober## Versionsschema

Seit `0.0.1` folgt RIMPERIO dem allgemeinen Standard **Semantic Versioning 2.0.0**:

| Teil | Wird erhöht, wenn … | Beispiel |
|------|---------------------|----------|
| `PATCH` | nur Fehler behoben werden, nichts Neues dazukommt | `0.4.2` → `0.4.3` |
| `MINOR` | neue Funktionen dazukommen und alles Bisherige weiter funktioniert | `0.4.3` → `0.5.0` |
| `MAJOR` | etwas Inkompatibles kommt (API, Datenbank ohne Selbst-Umbau, Agent-Protokoll) | `0.5.0` → `1.0.0` |
| `-vorab` | ein Stand zum Testen vor dem Release, wird als **Prerelease** veröffentlicht | `1.0.0-rc.1` |

- `0.y.z` heißt nach SemVer „in Entwicklung“ — `1.0.0` ist die erste als stabil erklärte Fassung.
- Das Release-Tag ist immer `v` + Version: `VERSION` = `0.0.1` → Tag `v0.0.1`.
- Build-Angaben (`+…`) werden nicht verwendet.
- Die alten Zeitstempel-Versionen (`2026.11.25.22.00.00`, Tags `v2026.11.25`) bleiben lesbar und gelten
  **immer als älter** als jede SemVer-Version. Eine vierstellige Jahreszahl vorne ist darum nie ein SemVer-Major.

## Version anheben

Maßstab ist die Datei **`VERSION`**. Von Hand an mehreren Stellen nachziehen muss man nichts mehr — das macht
[`scripts/version.sh`](../../scripts/version.sh):

```bash
scripts/version.sh bump patch      # Fehlerbehebung
scripts/version.sh bump minor      # neue Funktion
scripts/version.sh bump major      # inkompatible Änderung
scripts/version.sh set 1.0.0-rc.1  # genau diese Version
scripts/version.sh check           # stimmt alles? (läuft auch in der CI)
```

Es schreibt die Nummer nach `VERSION`, `server/backend/app/config.py` (`app_version`),
`server/frontend/package.json` + `package-lock.json` und `install.sh` (`RIMPERIO_VERSION`). Der Agent (exe,
MSI, Linux) liest `VERSION` beim Bauen selbst. Dazu gehört ein Abschnitt `## v<VERSION> (Datum) – Titel` in
[`releases.md`](releases.md) — ohne ihn schlägt `check` fehl.

Nach dem Merge auf `main` legt `.github/workflows/auto-release.yml` Tag und Release an und baut den Agent.

10.18.22.00.00 |
| Frontend | 2026.10.18.22.00.00 |
| Syslog | 2026.10.11.20.00.00 |
| Datenbank / Deployment | 2026.10.11.20.00.00 |

---

## Konventionen

- Neueste Version steht oben.
- Kategorien: **Added**, **Changed**, **Fixed**, **Removed**, **Security**
  (bzw. Neu, Geändert, Behoben, Entfernt, Sicherheit).
- Ein Änderungsblock pro Version — die **Projektversion**, in der die Änderung erscheint
  (`## 0.0.2`). Der `Version:`-Eintrag im Datei-Kopf trägt ab `0.0.1` ebenfalls die
  Projektversion, in der die Datei zuletzt geändert wurde.
- Nur den Bereich versionieren und eintragen, der tatsächlich geändert wurde.
- Ein Eintrag sagt, **was** sich geändert hat und **warum** — nicht, welche Zeile
  angefasst wurde. Bei Fehlerbehebungen gehört die Ursache dazu.
