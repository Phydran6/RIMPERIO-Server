# Lizenzen ausstellen – für den Herausgeber

← [Lizenz & Abrechnung](../docs/license/README.md)

RIMPERIO-Lizenzen werden in einem **eigenen GitHub-Repository** verwaltet
(Standard: `Phydran6/RIMPERIO-Licenses`). Jede Instanz holt sich dort ihre
Datei `licenses/<instanz-id>.json` und prüft die Signatur mit dem
öffentlichen Schlüssel, der im Server-Code steht. Fälschen kann eine Lizenz
nur, wer den privaten Schlüssel hat – und der liegt nur als GitHub-Secret im
Lizenz-Repository.

```
Lizenz-Repository                         RIMPERIO-Instanz
  sources/<id>.yml   ──Action signiert──►   licenses/<id>.json  ◄── alle 12 h abgeholt,
  (bearbeiten Sie)                          (nie von Hand ändern)    Signatur geprüft
```

## Einmalig einrichten

**1. Schlüsselpaar erzeugen** (lokal, mit `pip install cryptography pyyaml`):

```bash
python licensing/rimperio_license.py keygen --key-id rimperio-2026 --out rimperio-2026.key
```

Die Ausgabe nennt die Zeile für den öffentlichen Schlüssel. Die Datei
`rimperio-2026.key` ist der **private** Schlüssel – nie committen, sicher
aufbewahren (Passwortmanager).

**2. Öffentlichen Schlüssel in den Server eintragen:** die ausgegebene Zeile in
[`server/backend/app/license_keys.py`](../server/backend/app/license_keys.py)
unter `TRUSTED_KEYS` einfügen, committen, Release. Ab dieser Version können
Instanzen Lizenzen prüfen. (Vorher läuft jede Instanz als Community – das ist
gewollt, nicht kaputt.)

**3. Lizenz-Repository anlegen** – z. B. `Phydran6/RIMPERIO-Licenses`:

```bash
cp -r licensing/repo-template/. ../RIMPERIO-Licenses/
```

Darin sind: `README.md`, die Vorlage `sources/EXAMPLE.yml`, der leere Ordner
`licenses/` und die Action `.github/workflows/sign-licenses.yml`.

**4. Im Lizenz-Repository auf GitHub** (*Settings → Secrets and variables → Actions*):

| Art | Name | Wert |
|---|---|---|
| Secret | `RIMPERIO_LICENSE_SIGNING_KEY` | Inhalt von `rimperio-2026.key` |
| Variable | `RIMPERIO_LICENSE_KEY_ID` | `rimperio-2026` |

und unter *Settings → Actions → General → Workflow permissions* „Read and write
permissions“ erlauben (die Action committet die signierten Dateien).

**Öffentlich oder privat?** Öffentlich funktioniert ohne jede Einrichtung bei
den Instanzen (die Dateien sind signiert, nicht geheim) – dann aber in
`licensee` lieber nur eine Kundennummer. Privat: jede Instanz braucht
`RIMPERIO_LICENSE_GITHUB_TOKEN` (Fine-grained Token, nur *Contents: Read*
auf dieses Repository) in ihrer `.env`.

## Lizenz ausstellen, ändern, widerrufen

1. Der Kunde schickt seine **Instanz-ID** (*Lizenz & Geräte → Diese Instanz →
   Lizenzanfrage kopieren* – enthält auch die aktuellen Gerätezahlen).
2. Im Lizenz-Repository `sources/<instanz-id>.yml` anlegen (Vorlage:
   `sources/EXAMPLE.yml` oder `python licensing/rimperio_license.py new --instance-id … --max-devices 250`).
3. Committen. Die Action signiert und legt `licenses/<instanz-id>.json` ab.
4. Die Instanz holt sie beim nächsten Abruf – oder sofort mit *Lizenz jetzt prüfen*.

| Vorhaben | in der Quelle |
|---|---|
| verlängern | `valid_until` |
| mehr Geräte / Mandanten | `max_devices` / `max_tenants` |
| unbegrenzt, Abrechnung nach Nutzung | `max_devices: null` |
| aussetzen (z. B. offene Rechnung) | `status: suspended` |
| widerrufen | `status: revoked` |
| Kulanz bei Ausfall von GitHub | `grace_days` |
| Funktionen einschränken | `features: [billing]` (Standard: alle) |

Wird eine Quelle **gelöscht**, löscht die Action auch die signierte Datei; die
Instanz fällt nach der Kulanzzeit auf Community zurück. Widerrufen wirkt
sofort.

## Felder

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `license_id` | ✔ | Ihre Lizenznummer |
| `instance_id` | ✔ | ID der Instanz, für die die Lizenz gilt |
| `licensee`, `customer_no`, `note` | | Anzeige |
| `model` | | `per_device` (Standard), `per_instance`, `per_tenant` |
| `max_devices`, `max_tenants` | | Kontingent; `null` = unbegrenzt |
| `features` | | `billing`, `billing_api`, `tenants` (Standard: alle) |
| `valid_from`, `valid_until` | | `JJJJ-MM-TT`; `valid_until: null` = unbefristet |
| `grace_days` | | Kulanz in Tagen (Standard 14) |
| `status` | | `active`, `suspended`, `revoked` |
| `issued_at` | | setzt das Werkzeug beim Signieren |

## Ohne GitHub

```bash
# signieren
RIMPERIO_LICENSE_SIGNING_KEY="$(cat rimperio-2026.key)" \
  python licensing/rimperio_license.py sign sources/<id>.yml --key-id rimperio-2026 --out <id>.json
# prüfen
python licensing/rimperio_license.py verify <id>.json --public-key <öffentlicher Schlüssel>
```

Die Datei kann der Kunde unter *Lizenz & Geräte → Lizenzdatei einspielen*
hochladen, oder ein eigener Lizenz-Endpunkt liefert sie aus (Protokoll in
[`docs/license/README.md`](../docs/license/README.md#lizenz-endpunkt-protokoll)).

## Schlüsselwechsel

Neuen Schlüssel erzeugen und **zusätzlich** in `TRUSTED_KEYS` eintragen,
Release abwarten, dann Secret und `RIMPERIO_LICENSE_KEY_ID` im
Lizenz-Repository tauschen und die Action von Hand laufen lassen (alle
Lizenzen werden mit dem neuen Schlüssel neu signiert). Den alten Schlüssel
erst entfernen, wenn alle Instanzen aktualisiert sind.
