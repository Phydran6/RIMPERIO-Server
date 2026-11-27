# RIMPERIO-Lizenzen

Dieses Repository steuert die Lizenzen aller RIMPERIO-Instanzen. Jede Instanz
holt sich alle 12 Stunden `licenses/<instanz-id>.json` und prüft die Signatur.

| Was | Wo |
|---|---|
| Lizenz ausstellen / ändern | `sources/<instanz-id>.yml` anlegen bzw. bearbeiten, committen |
| Verlängern, Kontingent ändern | `valid_until`, `max_devices`, `max_tenants` in der Quelle ändern |
| Aussetzen / widerrufen | `status: suspended` bzw. `status: revoked` – wirkt beim nächsten Abruf |
| Signieren | macht die Action *Lizenzen signieren* bei jedem Push von selbst |

Die Dateien unter `licenses/` sind **signiert** – geändert werden nur die
Quellen unter `sources/`. Eine von Hand veränderte Lizenzdatei erkennt
RIMPERIO als ungültig.

> **Öffentlich oder privat?** Signierte Lizenzen dürfen öffentlich liegen –
> fälschen kann sie niemand ohne den privaten Schlüssel. Ein öffentliches
> Repository funktioniert ohne jede Einrichtung bei den Instanzen. In einem
> **privaten** Repository braucht jede Instanz einen Lesezugriff
> (`RIMPERIO_LICENSE_GITHUB_TOKEN` in ihrer `.env`). Beachten: in einem
> öffentlichen Repository ist auch `licensee` lesbar – dann lieber nur eine
> Kundennummer eintragen.

Ausführlich: `licensing/README.md` im RIMPERIO-Server-Repository.
