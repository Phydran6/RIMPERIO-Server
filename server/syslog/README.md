# Syslog-Empfänger

Nimmt Syslog auf **UDP und TCP 514** an, zerlegt es und schreibt es in die
Datenbank. Eigener Container, eigener Prozess — bewusst getrennt vom Backend:
Ein Lastausschlag beim Logeingang soll die Oberfläche nicht ausbremsen.

← [Übersicht](../../README.md) · [Alle Dokumente](../../docs/README.md)

## Erkannte Formate

| Format | Beispiel |
|--------|----------|
| RFC 5424 | `<34>1 2026-01-01T00:00:00Z host app 123 ID47 [sd] msg` |
| RFC 3164 (BSD) | `<34>Oct 11 22:14:15 host su[123]: msg` |
| UniFi Netconsole | `<6>{f1d1} [1234.56] daemon[12]: msg` |
| UniFi MAC/Modell | `<30>784558fc21cf,U6-LR-6.7.31: hostapd: msg` |
| Cisco IOS | `<189>… %LINK-3-UPDOWN: msg` |
| Fortinet u. ä. | `key=value key2="wert mit leerzeichen"` |
| JSON | `<134>{"level":"error","msg":"…"}` |
| generisch | alles andere, mit Priority-Auswertung |

Bei UniFi ist die Hex-Angabe in `{…}` eine **Sequenznummer, kein Hostname** —
eine Verwechslung, die UniFi-Logs sonst unbrauchbar macht.

## Warum es schnell ist

Bei Hunderten Nachrichten pro Sekunde wäre der naive Weg — pro Nachricht ein
`SELECT` für das Gerät, ein `INSERT` für die Zeile, ein `UPDATE` für
`last_seen` — der Flaschenhals. Stattdessen:

- **Agent-Cache im Speicher** (5 min), statt pro Nachricht nachzuschlagen
- **Sammel-Einfügungen** per PostgreSQL `COPY` (100 Zeilen oder 2 Sekunden)
- **`last_seen` gebündelt** alle 2 Sekunden

Ergebnis: rund 2 statt 96 Datenbankvorgänge pro Sekunde.

## Konfiguration

Über die Umgebung (siehe [`../docker-compose.yml`](../../docker-compose.yml)):
`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DATABASE_URL`,
`DB_SSLMODE`, `SYSLOG_PORT`.

## Geräte anbinden

**Linux (rsyslog):**
In `/etc/rsyslog.d/rimperio.conf` eintragen:

```
*.* @RIMPERIO-IP:514
```

**UniFi Controller:** Settings → System → Remote Logging → Enable + RIMPERIO-IP

**Alles außerhalb des eigenen Netzes** gehört nicht hierher, sondern an den
[Agent](../../agent/logship/README.md): Syslog ist unverschlüsselt und weist den Absender
nicht aus.

## Anzeige

Der Empfänger speichert die Rohzeile unverändert. Lesbar gemacht wird sie erst
bei der Anzeige — durch `backend/app/logparse.py`, das die Datenbank nie
anfasst. Geht die Erkennung daneben, steht die Rohzeile weiterhin daneben.
