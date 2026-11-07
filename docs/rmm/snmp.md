# SNMP-Überwachung

Netzwerkgeräte ohne Agent überwachen: Switches, Router, Firewalls, Drucker, USVs, NAS. In
der Oberfläche unter **Fernwartung → SNMP-Geräte**.

← [Fernwartung](README.md) · [Patchmanagement](patching.md) · [Dokumentation](../README.md)

## Einrichten

1. **Zugangsdaten** anlegen: v1/v2c mit Community, oder v3 mit Benutzer,
   Authentisierung (MD5, SHA, SHA-224/256/384/512) und Verschlüsselung (DES, AES).
2. **Gerät hinzufügen**: Adresse eintragen, *Testen*. RIMPERIO zeigt sysName/sysDescr und
   hakt die passenden Vorlagen an (erkannt über eine Probe-OID je Vorlage). Oder unter
   *Aus Netzwerksuche* ein gefundenes Gerät mit einem Klick übernehmen.
3. Speichern. Abgefragt wird im eingestellten Takt (Standard 5 Minuten).

**Wer fragt ab?** Der RIMPERIO-Server — oder ein RIMPERIO-Agent im Netz des Geräts, etwa an
einem anderen Standort. Der Agent kann zusätzlich AES-192/256.

## Mitgelieferte Vorlagen

| Vorlage | Inhalt |
|---|---|
| System (immer) | Laufzeit, Erreichbarkeit |
| Schnittstellen (immer) | Status, Datenrate ein/aus (64-Bit-Zähler), Eingangsfehler je Port |
| Host-Ressourcen | CPU, belegter Speicher/Platten in % |
| USV (RFC 1628) | Batterie, Ladung, Restlaufzeit, Netz/Batterie, Last |
| APC | Batterie, Ladung, Restlaufzeit, Last, Ausgang, Batterietausch |
| Drucker | Toner/Verbrauchsmaterial in %, Status, Seitenzähler |
| Synology | System, Temperatur, Netzteil, Lüfter, DSM-Update, Platten, RAID |
| Cisco | CPU (5 min), Speicher |
| MikroTik | Temperaturen, Spannung, RouterOS-Version |
| FortiGate | FortiOS, CPU, Speicher, Sitzungen, Platte |
| Net-SNMP | Load, Swap (Linux, Synology, QNAP, pfSense) |

Mitgelieferte Vorlagen sind fest; *Als eigene kopieren* macht eine bearbeitbare daraus.
Eigene Werte gehen auch direkt am Gerät (Einstellungen → Eigene Werte).

## Schwellen und Alarme

Jeder Wert hat — wo sinnvoll — eine Warn- und eine kritische Schwelle. Am Gerät lassen sie
sich je Wert überschreiben (gilt für alle Zeilen, etwa alle Ports) oder stumm schalten.

- Gerät antwortet zweimal nicht → Alarm *nicht erreichbar* (kritisch)
- Wert über/unter Schwelle → Alarm mit Schwere
- wieder normal → Entwarnung

Alarme stehen unter *Rechner-Alarme*, kritische zusätzlich im Eingang (App, Glocke), und
gehen über *Benachrichtigungen* (Ereignis „SNMP-Gerät meldet ein Problem“) nach außen.

## Verlauf

Datenraten, CPU, Speicher, Temperaturen und USV-Werte werden 14 Tage gespeichert; am Gerät
zeigt *Verlauf* die Kurve (6 h bis 14 Tage).

## Format eigener Werte

```json
[
  {"key": "temp", "label": "Temperatur", "group": "Sensoren", "type": "gauge",
   "oid": "1.3.6.1.4.1.9999.1.1.0", "unit": "°C", "scale": 0.1, "warn": 60, "crit": 70,
   "history": true},
  {"key": "psu", "label": "Netzteil", "type": "enum",
   "table": {"column": "1.3.6.1.4.1.9999.2.1.3", "label": "1.3.6.1.4.1.9999.2.1.2"},
   "map": {"1": "ok", "2": "Fehler"}, "ok": ["1"], "crit": ["2"]}
]
```

Arten: `gauge`, `rate` (Zähler → je Sekunde, `bits: true` → bit/s), `ratio` (`num`/`den`),
`enum` (Zustand mit `map`, `ok`, `warn`, `crit`), `ticks` (Zeit in Tagen), `text`.
`op: "<"` dreht die Schwelle um (für „zu wenig“).

## API

`/api/snmp/devices`, `/credentials`, `/templates`, `/probe`, `/discovered`,
`/devices/{id}/history`, `/devices/{id}/poll`. Der Agent holt seine Aufträge im Check-in
(`snmp`) und liefert an `/api/rmm/agent/snmp-results`.
