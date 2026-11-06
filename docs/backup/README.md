# Sicherung und Wiederherstellung

*System → Sicherung* — nur für Administratoren.

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

## Der Grundsatz

**Vor jedem Systemeingriff steht dieselbe Frage: vorher sichern — ja oder nein?**

Update, Rückfall, Zurückspielen, Zusatzdienst ein- oder ausschalten: Überall
erscheint derselbe Dialog. Er lässt sich nicht wegklicken, nur beantworten —
und „Nein“ ist eine gültige Antwort, die man aber bewusst wählen muss.

Das hängt nicht an der Oberfläche, sondern am Endpunkt: Ohne ausgefüllte
Entscheidung antwortet der Server mit HTTP 400. Auch ein Skript, das den Dialog
umgehen will, muss die Entscheidung mitschicken.

Scheitert die Sicherung, **läuft der Eingriff nicht an**. Genau dafür ist sie da.

---

## Was in eine Sicherung kommt

Sechs Bereiche, jeder einzeln wählbar — beim Sichern wie beim Zurückspielen:

| Bereich | Inhalt | Standard |
|---------|--------|:--------:|
| **Einstellungen** | Allgemeines, Aufbewahrung, Archivierung, KI, Mail | ✓ |
| **Erscheinungsbild** | Farben, Texte, hochgeladenes Logo und Favicon | ✓ |
| **Benutzer & Anmeldung** | Konten, Rollen, MFA-Geheimnisse, Passkeys | ✓ |
| **Geräte & Agent-Token** | bekannte Geräte samt Regeln, Agent-Token | ✓ |
| **Webhooks** | Definitionen inklusive Token | ✓ |
| **Logdaten** | die eigentlichen Logzeilen — mit Abstand der größte Teil | — |

Logdaten sind absichtlich nicht vorausgewählt: Vor einem Update will man die
Konfiguration retten, nicht zehn Gigabyte Logs kopieren. Wer sie will, hakt sie
an.

---

## Der Dateiaufbau

Eine Sicherung ist eine ganz normale ZIP-Datei:

```
rimperio-backup-20260909-120000-a1b2c3.zip
├── manifest.json     ← IMMER unverschlüsselt lesbar
└── payload.zip       ← die Daten
    oder payload.bin  ← dieselben Daten, AES-256-GCM verschlüsselt
```

**Warum das Manifest außen und im Klartext liegt:** Beim Zurückspielen muss man
*vor* der Passwortabfrage sehen können, von welchem Server und welcher Version
die Sicherung stammt. Sonst fällt ein Versionskonflikt erst auf, wenn die Daten
schon halb in der Datenbank stehen.

Im Manifest steht kein Inhalt — nur Version, Commit, Zeitpunkt, Umfang,
Zeilenzahlen und Prüfsummen.

Innen liegt jede Tabelle als `jsonl.gz`. Das komprimiert Logzeilen deutlich
besser als das ZIP allein und lässt sich zeilenweise einlesen, statt Millionen
Datensätze auf einmal in den Speicher zu holen.

---

## Verschlüsselung

Optional, mit Passwort:

- **AES-256-GCM** für den Inhalt
- **PBKDF2-HMAC-SHA256**, 210 000 Runden, zufälliges Salz
- Das Manifest bleibt lesbar, damit die Versionsprüfung vor der Passwortabfrage
  läuft.

> **Ohne das Passwort ist die Sicherung nicht mehr zu öffnen.** Es gibt keinen
> Ersatzweg, keine Hintertür und keine Wiederherstellung. Das ist der Sinn der
> Sache — aber es heißt auch: Passwort verloren, Sicherung verloren.

Ein falsches Passwort wird beim Entschlüsseln erkannt (GCM prüft die
Unversehrtheit mit), nicht erst beim Einlesen. Zusätzlich wird die SHA-256-Summe
des entpackten Inhalts geprüft — eine unterwegs beschädigte Datei fällt auf,
bevor sie in die Datenbank geht.

---

## Zurückspielen

Zweistufig, mit Absicht:

**1. Nachsehen** (`/inspect`) — was steckt drin, wie viele Datensätze pro
Bereich, von welcher Version, passt sie zu diesem Server?

**2. Auswählen und zurückspielen** — welche Bereiche, und wie:

| Modus | Wirkung |
|-------|---------|
| **Ersetzen** | Vorhandene Daten in den gewählten Bereichen werden verworfen |
| **Ergänzen** | Nur hinzufügen, was fehlt — Vorhandenes bleibt |

Nur die Tabellen der gewählten Bereiche werden angefasst. Wer nur die Benutzer
zurückholt, behält seine Logs.

Nach dem Einspielen werden die ID-Zähler nachgezogen — sonst vergäbe die nächste
Einfügung eine Nummer, die es schon gibt.

---

## Versionsprüfung

Vor jedem Zurückspielen wird die Version der Sicherung mit der des Servers
verglichen:

| Fall | Einstufung | Folge |
|------|-----------|-------|
| gleiche Version | **ok** | läuft |
| Sicherung **älter** | *Hinweis* | läuft — das ist der Normalfall, dafür gibt es Sicherungen |
| Version unlesbar | *Warnung* | läuft, aber ungeprüft |
| Sicherung **neuer** als der Server | **blockiert** | Abbruch |

Der letzte Fall ist der gefährliche: Der Server ist dann älter als die Daten,
die er bekommen soll, und kann Spalten enthalten, die er nicht kennt. Richtig
wäre, erst den Server zu aktualisieren. Wer es trotzdem will, setzt in der
Oberfläche ausdrücklich das Häkchen — eine bewusste Entscheidung, kein
Durchklicken.

Dasselbe gilt für die Formatversion der Datei selbst.

---

## Auf einen anderen Server umziehen

1. Auf dem alten Server: Sicherung anlegen, Bereiche wählen, **herunterladen**.
2. Auf dem neuen Server: **hochladen** — sie wird geprüft, aber noch nicht
   eingespielt.
3. Nachsehen, was drinsteckt und ob die Version passt.
4. Zurückspielen.

Beim Hochladen gilt eine Obergrenze von 1,5 GB. Größere Bestände gehen besser
über die [Archivierung](../operate/README.md#aufbewahrung-und-archivierung) oder
einen `pg_dump`.

---

## Wo die Sicherungen liegen

In einem eigenen Docker-Volume (`backup_data`, im Container unter `/backups`) —
sie überstehen damit einen Neubau der Container, und genau dafür sind sie da.

Aufgehoben werden standardmäßig die letzten 10:

In die `.env` eintragen:

```
RIMPERIO_KEEP_BACKUPS=10
```

Ältere räumt RIMPERIO nach jeder neuen Sicherung selbst weg. Der Knopf *Alte
aufräumen* macht dasselbe von Hand.

**Wichtig:** Eine Sicherung auf demselben Server ist gegen einen Fehlgriff gut,
nicht gegen einen Plattenausfall. Was zählt, ist die heruntergeladene Kopie
woanders.

---

## Auf der Kommandozeile

Die klassischen Wege bleiben:

Datenbank sichern:

```bash
docker compose exec postgres pg_dump -U rimperio rimperio > backup.sql
```

Datenbank einspielen:

```bash
docker compose exec -T postgres psql -U rimperio rimperio < backup.sql
```

---

## Weiter

- [Updates](../updates/README.md) — dort wird die Sicherungsfrage gestellt
- [Datenbank](../../db/README.md) — Schema und Migration
- [Betrieb](../operate/README.md) — Archivierung als Dauerlösung
