# Lizenz & Abrechnung

← [Dokumentation](../README.md)

**Der Server bleibt lizenzfrei.** Wer RIMPERIO selbst betreibt, bekommt ohne
Lizenz die **Community-Ausgabe**: Logs, Fernwartung, Patchmanagement, SNMP,
Proxmox, App – alles, ohne Gerätegrenze. Nichts wird abgeschaltet.

Eine **Lizenz** braucht, wer RIMPERIO gewerblich als Dienst einsetzt
(Enterprise, Systemhaus). Sie schaltet die Enterprise-Funktionen frei:

| Funktion | Community | Enterprise |
|---|---|---|
| Überwachen, Patchen, Fernwartung, Logs, SNMP … | ✔ | ✔ |
| Geräte zählen, Preise einstellen, Vorschau der Monatsabrechnung | ✔ | ✔ |
| Abrechnung exportieren (CSV), Monate festschreiben | – | ✔ |
| Abrechnungs-API für ERP/Rechnungsprogramm | – | ✔ |
| Mandanten (sobald die Mandantenfähigkeit da ist) | – | ✔ |

In der Oberfläche: **Lizenz & Abrechnung → Lizenz & Geräte** und **→ Abrechnung**.

## Lizenzmodelle

| Modell | Bedeutung |
|---|---|
| **je Gerät** (`per_device`) | Jedes angeschlossene Gerät belegt einen Platz. Mit `max_devices` begrenzt – oder ohne Grenze, dann wird nach tatsächlicher Nutzung abgerechnet (siehe *Nutzung melden*). |
| **je Instanz** (`per_instance`) | Pauschal für diese RIMPERIO-Installation, beliebig viele Geräte. |
| **je Mandant** (`per_tenant`) | Je Mandant, mit `max_tenants` begrenzbar. Bis die Mandantenfähigkeit fertig ist, gilt der **Standort** eines Geräts als Mandant. |

## Geräte lizenzieren sich selbst

Alle 5 Minuten gleicht RIMPERIO die Geräte ab: Rechner und Log-Quellen,
SNMP-Geräte, Proxmox-Hosts. Jedes neue Gerät bekommt automatisch einen Platz,
solange das Kontingent reicht.

- **Wer zuerst da war, behält seinen Platz.** Ist das Kontingent voll, steht ein
  neues Gerät auf *über Kontingent*: Es wird **weiter überwacht**, RIMPERIO
  meldet es aber (Kopfzeile, Systemtagebuch, Meldung „Lizenz“).
- Ein Gerät ohne Lebenszeichen gibt seinen Platz nach 30 Tagen frei
  (einstellbar unter *Abrechnung → Preise*).
- *Nicht abrechnen* (am Gerät oder für eine ganze Geräteklasse) – das Gerät
  belegt dann auch keinen Lizenzplatz.

Geräteklassen: Windows-Server, Windows-Arbeitsplatz, Linux, macOS,
Netzwerkgerät (SNMP), Proxmox-Host, Log-Quelle (nur Logs), Sonstiges. Die
Klasse wird erkannt und lässt sich je Gerät überschreiben, ebenso der Mandant.

## Woher die Lizenz kommt

Jede Installation hat eine **Instanz-ID** (*Lizenz & Geräte → Diese Instanz*).
Die Lizenz ist eine mit Ed25519 signierte JSON-Datei für genau diese ID.

| Quelle | Wie |
|---|---|
| **GitHub-Lizenzbereich** (Standard) | RIMPERIO holt alle 12 h `licenses/<instanz-id>.json` aus dem Lizenz-Repository (Standard `Phydran6/RIMPERIO-Licenses`, Branch `main`). Der Herausgeber steuert dort alles: ausstellen, verlängern, Kontingent ändern, aussetzen, widerrufen. |
| **Eigener Lizenz-API-Endpunkt** | RIMPERIO schickt per `POST` Instanz-ID, Version und Nutzung; die Antwort ist die signierte Lizenzdatei. Optional mit Token. |
| **Von Hand** | Datei einfügen oder hochladen – für Installationen ohne Internet. Gilt bis zu ihrem Ablaufdatum. |

**Kulanz:** Eine online bezogene Lizenz muss sich regelmäßig bestätigen. Ist
die Quelle nicht erreichbar, gilt sie `grace_days` (Standard 14) weiter, danach
fällt RIMPERIO auf Community zurück. Dasselbe nach dem Ablaufdatum. Widerruf
(`status: revoked`) wirkt beim nächsten Abruf sofort. Die Überwachung läuft in
jedem Fall ohne Unterbrechung weiter.

Einstellungen per `.env` (Vorgabe; die Oberfläche hat Vorrang):

| Variable | Bedeutung |
|---|---|
| `RIMPERIO_LICENSE_REPO` / `_BRANCH` / `_PATH` | GitHub-Lizenzbereich |
| `RIMPERIO_LICENSE_URL` | eigener Lizenz-Endpunkt |
| `RIMPERIO_LICENSE_GITHUB_TOKEN` | Lesezugriff auf ein **privates** Lizenz-Repository |

> **Datenschutz:** Für die Prüfung fragt RIMPERIO bei GitHub nach der Datei
> mit seiner Instanz-ID – dabei geht nichts weiter als die ID heraus. Nur beim
> eigenen Lizenz-Endpunkt werden (abschaltbar) Gerätezahlen mitgeschickt. Wer
> gar keinen Abruf möchte: Quelle *nur von Hand*.

**Klon aus einer Sicherung?** Eine Rücksicherung behält die Instanz-ID (die
Lizenz passt weiter). Ein zweiter Server aus derselben Sicherung bekommt mit
*Neue Kennung …* eine eigene ID und braucht eine eigene Lizenz.

### Lizenz-Endpunkt: Protokoll

```http
POST https://lizenz.example.de/api/rimperio
Authorization: Bearer <optionales Token>
Content-Type: application/json

{"instance_id": "…", "version": "2026.11.27…",
 "usage": {"month": "2026-11", "devices_active": 42, "devices_licensed": 40,
           "devices_over_limit": 2, "tenants": 5, "peak_devices_month": 43,
           "devices_month": 45, "by_class": {"windows_client": 30, "…": 0}}}
```

Antwort `200` mit der Lizenzdatei (oder `{"envelope": …}`), `404` wenn es für
die Instanz keine Lizenz gibt.

## Abrechnung je Gerät (Systemhaus)

Die Preise legt das Systemhaus komplett selbst fest. Gesucht wird in dieser
Reihenfolge – der erste Treffer gilt:

1. eigener Preis am Gerät
2. Preis der Geräteklasse beim Mandanten
3. Standardpreis des Mandanten
4. Preis der Geräteklasse
5. Standardpreis

RIMPERIO hält **jeden Tag fest, an dem ein Gerät aktiv war**. Daraus wird
monatlich gerechnet – **Zählweise**:

| Zählweise | Rechnung |
|---|---|
| je angefangenem Monat | volles Gerät, wenn es an mindestens *n* Tagen aktiv war (Standard: 1 Tag) |
| anteilig | aktive Tage ÷ Tage im Monat |
| Höchststand | die meisten gleichzeitig aktiven Geräte an einem Tag |

Je Mandant dazu: Name und Kundennummer für die Rechnung, Inklusiv-Geräte
(werden bei den günstigsten zuerst abgezogen), Rabatt in %, Grundgebühr,
Mindestbetrag, „nicht abrechnen“ (z. B. die eigene Firma). Steuersatz global.

Am **Monatsersten** schreibt RIMPERIO den Vormonat fest – spätere
Preisänderungen ändern eine gestellte Rechnung nicht mehr. Neu festschreiben
geht von Hand. Export als CSV (für deutsches Excel: `;` und Dezimalkomma) –
je Position oder als **Gerätenachweis** je Gerät mit aktiven Tagen.

## Abrechnungs-API

Für Rechnungsprogramm, ERP oder n8n: unter *Abrechnung → Abrechnungs-API*
einen Schlüssel (`rbk_…`) anlegen. Er darf nur lesen.

```bash
curl -H "Authorization: Bearer rbk_…" https://rimperio.example/api/license/v1/usage?month=2026-11
curl -H "Authorization: Bearer rbk_…" "https://rimperio.example/api/license/v1/usage?month=2026-11&tenant=Kunde%20A&format=csv"
curl -H "Authorization: Bearer rbk_…" https://rimperio.example/api/license/v1/devices
curl -H "Authorization: Bearer rbk_…" https://rimperio.example/api/license/v1/status
```

| Endpunkt | Liefert |
|---|---|
| `GET /api/license/v1/usage?month=JJJJ-MM[&tenant=][&format=csv][&detail=true][&live=true]` | Monatsabrechnung je Mandant (festgeschrieben, sonst Stand jetzt) |
| `GET /api/license/v1/devices[?tenant=]` | alle gezählten Geräte mit Mandant, Klasse, Zustand, Monatspreis |
| `GET /api/license/v1/status` | Ausgabe, Kontingent, Nutzung im Monat |

## Lizenzen ausstellen (Herausgeber)

Wie Schlüssel erzeugt, das Lizenz-Repository auf GitHub eingerichtet und
Lizenzen signiert werden: [`licensing/README.md`](../../licensing/README.md).
