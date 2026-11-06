# API

Die vollständige, immer aktuelle Referenz steht am laufenden Server:
**`http://SERVER-IP/api/docs`** (OpenAPI/Swagger).

Hier steht das, was man vorher wissen will.

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

## Anmeldung

```bash
curl -X POST https://rimperio.example.com/api/auth/login \
  -F "username=admin" -F "password=geheim"
```

Antwort: `{"access_token": "…", "token_type": "bearer"}` — oder bei aktivem MFA
`{"mfa_required": true, "mfa_token": "…"}`, dann Schritt zwei über
`POST /api/auth/login/mfa`.

Danach überall:

```
Authorization: Bearer <access_token>
```

**Zwei Ausnahmen von der Kopfzeile:** Der Ereignisstrom (`EventSource`) und das
Terminal (`WebSocket`) können keine eigenen Kopfzeilen mitschicken — dort geht
der Token als `?token=`. Geprüft wird er genauso streng.

---

## Logs von Agents annehmen

```
POST /api/agents/ingest
Authorization: Bearer <AGENT-TOKEN>
```

```json
{
  "hostname": "srv01",
  "ip_address": "10.0.0.5",
  "device_type": "linux_agent",
  "events": [
    {
      "message": "sshd: Accepted publickey for alice",
      "level": "info",
      "source": "sshd",
      "timestamp": "2026-09-09T12:00:00Z",
      "facility": 4
    }
  ]
}
```

Antwort:

```json
{ "accepted": 1, "duplicates": 0, "message": "ok", "agent_id": 42 }
```

**Bereit für Agents über HTTPS — was dafür da ist:**

| Punkt | Umsetzung |
|-------|-----------|
| Verschlüsselung | TLS über Caddy: Let's Encrypt, eigenes Zertifikat oder interne CA |
| Anmeldung | Agent-Token (Bearer), mehrere möglich, einzeln zurückziehbar |
| Paketgröße | bis 5000 Ereignisse je Aufruf |
| Doppelte Lieferungen | Einträge mit eigenem Zeitstempel bekommen einen `dedup_key` (SHA-256 aus Host, Zeit, Ereignis-ID und Text); Wiederholungen verwirft die Datenbank per `ON CONFLICT DO NOTHING` |
| Fremde Geräte melden | Sammler dürfen `hostname`, `ip_address` und `device_type` des **gemeldeten** Geräts mitschicken — dann erscheint es mit eigener Identität, nicht unter der IP des Sammlers |
| Zuordnung | `agent_id` in der Antwort: der Agent merkt sie sich und räumt beim Deinstallieren genau seinen Eintrag ab |
| Kompression | GZip ab 1 KB Antwortgröße |
| Zeitzonen | Zeitstempel mit Zone werden nach UTC gerechnet |

**Was fehlt:** eine Bremse speziell für den Ingest. Der globale Rate-Limiter
greift, ein eigenes Kontingent pro Token gibt es nicht. Bei vielen Agents
gehört vor den Server ohnehin ein Reverse Proxy — Caddy ist dabei.

---

## Abmelden (Deinstallation)

```
POST /api/agents/decommission
Authorization: Bearer <AGENT-TOKEN>
```

```json
{
  "agent_id": 42,
  "hostname": "srv01",
  "ip_address": "10.0.0.5",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "purge": true,
  "all_for_hostname": true
}
```

`purge: true` löscht Gerät **und** Logs. Ohne wird das Gerät nur stillgelegt.

Zuordnung vom Genauesten zum Ungenauesten — welcher Weg gegriffen hat, steht in
der Antwort als `matched_by`:

1. `agent_id` · 2. `mac_address` · 3. `hostname` + `ip_address` · 4. `hostname`

`all_for_hostname: true` nimmt zusätzlich alle weiteren Einträge desselben
Hostnamens mit. Nötig, weil ein Rechner nach einem IP-Wechsel mehrfach in der
Liste steht — ohne das blieben die alten als Karteileichen mit ihren Logs
liegen.

---

## Schnittstelle für die App

Eigener Zweig unter **`/api/app`** — schlanker als die Browser-API, weil eine
App andere Sorgen hat:

| Endpunkt | Wofür |
|----------|-------|
| `GET /api/app/bootstrap` | alles für den Start in einem Aufruf: Server, Benutzer, Farben, Filterwerte |
| `GET /api/app/logs` | Logzeilen, per Cursor blätterbar (`before_id`) |
| `GET /api/app/logs/tail` | nur, was seit `since_id` dazukam |
| `GET /api/app/logs/{id}` | eine Zeile vollständig samt Rohtext |
| `GET /api/app/summary` | Zahlen für den Startbildschirm |
| `GET /api/app/devices` | kompakte Geräteliste mit Online-Zustand |

**Warum eine eigene Schnittstelle:**

- **Wenig Daten.** Über Mobilfunk zählt jedes Kilobyte. Rohzeilen kommen erst
  mit, wenn jemand eine Zeile aufklappt.
- **Cursor statt Seitenzahl.** Bei `page=42` verschiebt sich alles, sobald
  währenddessen neue Logs eintreffen — man sieht Zeilen doppelt oder
  überspringt welche. Mit `before_id` passiert das nicht.
- **Nachlaufen statt neu laden.** `since_id` liefert nur das Neue.
- **Lesbare Logs.** Jede Zeile kommt durch den Anzeige-Parser und bringt eine
  Zusammenfassung (`summary`) und Abzeichen (`badges`) mit.

`bootstrap` meldet `api_level` und `capabilities` — daran kann eine ältere App
ablesen, was dieser Server kann, statt es auszuprobieren.

---

## Logs lesen und filtern

| Anfrage | Liefert |
|---|---|
| `GET /api/logs?hostname=srv01&min_severity=error&page=1&page_size=100` | Logs, gefiltert und seitenweise |
| `GET /api/logs/filter-options` | verfügbare Werte für die Filter |
| `GET /api/logs/{id}/parsed` | eine Zeile zerlegt: Felder, Abzeichen |
| `GET /api/logs/export?format=csv` | Export, auch `json`, mit denselben Filtern |
| `GET /api/logs/stats` | Statistik |

---

## Verwaltung

| Bereich | Prefix |
|---------|--------|
| Updates, Releases, Kanal, Webhook | `/api/updates` |
| Sicherung und Wiederherstellung | `/api/backup` |
| KI-Auswertung | `/api/ai` |
| Zusatzdienste | `/api/stacks` |
| Mail | `/api/mail` |
| Terminal | `/api/shell` |
| Systemcheck | `/api/diagnostics` |
| Reverse Proxy und TLS | `/api/caddy` |
| Netzwerk und DNS | `/api/network` |

Alles davon ist Administratoren vorbehalten.

**Systemeingriffe verlangen zwei Dinge im Rumpf:** ein Bestätigungswort
(`confirm: "UPDATE"`, `"ROLLBACK"`, `"RESTORE"`, `"CHANGE"`) und die
beantwortete Sicherungsfrage:

```json
{
  "confirm": "UPDATE",
  "backup": { "create": true, "scopes": ["settings", "users"], "passphrase": "" }
}
```

oder, für den bewussten Verzicht:

```json
{ "confirm": "UPDATE", "backup": { "create": false } }
```

Fehlt `backup`, antwortet der Server mit 400. Die Rückfrage lässt sich also
nicht dadurch umgehen, dass man die Oberfläche wegläßt.

---

## Ereignisstrom

```
GET /api/updates/stream?token=<access_token>
```

Server-Sent Events. Ereignisse: `update.available`, `update.cleared`,
`update.state`, `backup.done`, `stack.changed`. Alle 20 Sekunden ein
Lebenszeichen, damit Proxys die Verbindung nicht kappen.

Bewusst kein WebSocket: Es fließt nur in eine Richtung, und SSE läuft ohne
Sonderbehandlung durch jeden Reverse Proxy.

---

## GitHub-Webhook

```
POST /api/updates/webhook
X-Hub-Signature-256: sha256=…
```

Von außen erreichbar, aber nicht offen: Ohne gültige HMAC-Signatur und ohne
gesetztes `RIMPERIO_WEBHOOK_SECRET` wird nichts angenommen. Siehe
[Updates](../updates/README.md#sofortmeldung).

---

## Öffentliche Webhooks

```
GET /api/webhook/{id}/call?token={token}
```

Der einzige Endpunkt, der ohne Anmeldung Logdaten liefert. Gebremst auf 60
Aufrufe pro Minute. Siehe [Integrationen](../integrations/README.md#webhooks).

---

## Weiter

- [Agents](../../agent/logship/README.md)
- [Integrationen](../integrations/README.md)
- [Backend](../../backend/README.md)
