# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Mitgelieferte SNMP-Vorlagen (Standard-MIBs und Hersteller)
# ==============================================================================
"""
Vorlagen sagen, was von einem Geraetetyp gelesen wird und wie es zu bewerten ist.

Eine Metrik ist entweder ein Einzelwert (`oid`) oder eine Tabellenspalte
(`table`: `column` = Basis-OID der Spalte, `label` = Basis-OID der
Beschriftungsspalte). Arten (`type`):

* `gauge`   - Wert wie gelesen (mal `scale`)
* `rate`    - Zaehler; daraus wird je Sekunde (bei `bits`: mal 8)
* `ratio`   - `num` / `den` * `scale` (z. B. belegter Speicher in %); mit
              `den_plus_num` wird der Nenner zu num + den (Cisco: frei + belegt)
* `enum`    - Zustand; `map` uebersetzt, `ok`/`warn`/`crit` bewerten
* `ticks`   - Zeit in Hundertstelsekunden, angezeigt als Tage
* `text`    - nur anzeigen

Schwellen: `warn`/`crit` mit `op` (">" Standard, "<" fuer "zu wenig"). Was
`alert: false` traegt, wird angezeigt, loest aber keinen Alarm aus.

`probe`: eine OID, die ein Geraet dieses Typs beantwortet - so schlaegt RIMPERIO
beim Hinzufuegen die passenden Vorlagen selbst vor.
"""

IF_LABEL = "1.3.6.1.2.1.31.1.1.1.1"          # ifName
IF_LABEL_FALLBACK = "1.3.6.1.2.1.2.2.1.2"    # ifDescr
IF_EXCLUDE = r"(?i)^(lo|loopback|null|nu0|sit\d|ip6tnl|gre|teql|imq|ifb|docker|veth|br-)"

BUILTIN_TEMPLATES = [
    {
        "key": "system", "name": "System (MIB-II)", "vendor": "Standard", "always": True,
        "description": "Laufzeit und Erreichbarkeit — jedes SNMP-Gerät.",
        "metrics": [
            {"key": "uptime", "label": "Laufzeit", "group": "System", "type": "ticks",
             "oid": "1.3.6.1.2.1.1.3.0", "unit": "Tage", "alert": False},
        ],
    },
    {
        "key": "interfaces", "name": "Schnittstellen", "vendor": "Standard", "always": True,
        "description": "Status, Datenrate und Fehler je Port (IF-MIB, 64-Bit-Zähler).",
        "metrics": [
            {"key": "if.status", "label": "Status", "group": "Schnittstellen", "type": "enum",
             "table": {"column": "1.3.6.1.2.1.2.2.1.8", "label": IF_LABEL, "label_fallback": IF_LABEL_FALLBACK,
                       "exclude": IF_EXCLUDE, "max_rows": 96},
             "map": {"1": "up", "2": "down", "3": "testing", "5": "dormant", "6": "nicht vorhanden",
                     "7": "lowerLayerDown"}, "ok": ["1", "2", "5", "6", "7"], "alert": False},
            {"key": "if.in", "label": "Eingang", "group": "Schnittstellen", "type": "rate", "bits": True,
             "unit": "bit/s", "history": True, "alert": False,
             "table": {"column": "1.3.6.1.2.1.31.1.1.1.6", "column_fallback": "1.3.6.1.2.1.2.2.1.10",
                       "label": IF_LABEL, "label_fallback": IF_LABEL_FALLBACK, "exclude": IF_EXCLUDE, "max_rows": 96}},
            {"key": "if.out", "label": "Ausgang", "group": "Schnittstellen", "type": "rate", "bits": True,
             "unit": "bit/s", "history": True, "alert": False,
             "table": {"column": "1.3.6.1.2.1.31.1.1.1.10", "column_fallback": "1.3.6.1.2.1.2.2.1.16",
                       "label": IF_LABEL, "label_fallback": IF_LABEL_FALLBACK, "exclude": IF_EXCLUDE, "max_rows": 96}},
            {"key": "if.in_err", "label": "Eingangsfehler", "group": "Schnittstellen", "type": "rate",
             "unit": "/s", "warn": 1, "crit": 10,
             "table": {"column": "1.3.6.1.2.1.2.2.1.14", "label": IF_LABEL, "label_fallback": IF_LABEL_FALLBACK,
                       "exclude": IF_EXCLUDE, "max_rows": 96}},
        ],
    },
    {
        "key": "host", "name": "Host-Ressourcen (CPU, Speicher, Platten)", "vendor": "Standard",
        "probe": "1.3.6.1.2.1.25.1.1.0",
        "description": "HOST-RESOURCES-MIB: Linux, Windows, NAS, MikroTik, Drucker …",
        "metrics": [
            {"key": "cpu", "label": "CPU", "group": "System", "type": "gauge", "aggregate": "avg",
             "unit": "%", "warn": 85, "crit": 95, "history": True,
             "table": {"column": "1.3.6.1.2.1.25.3.3.1.2"}},
            {"key": "storage", "label": "Belegt", "group": "Speicher", "type": "ratio", "scale": 100,
             "unit": "%", "warn": 85, "crit": 95, "history": True,
             "table": {"num": "1.3.6.1.2.1.25.2.3.1.6", "den": "1.3.6.1.2.1.25.2.3.1.5",
                       "label": "1.3.6.1.2.1.25.2.3.1.3",
                       "exclude": r"(?i)virtual memory|swap|/run|/dev/shm|/sys/|cached memory|shared memory|memory buffers|/snap/",
                       "max_rows": 40}},
        ],
    },
    {
        "key": "ups", "name": "USV (UPS-MIB, RFC 1628)", "vendor": "Standard",
        "probe": "1.3.6.1.2.1.33.1.1.1.0",
        "description": "Batteriezustand, Restlaufzeit, Last, Netz/Batterie — alle USVs mit Standard-MIB.",
        "metrics": [
            {"key": "ups.battery", "label": "Batterie", "group": "USV", "type": "enum",
             "oid": "1.3.6.1.2.1.33.1.2.1.0",
             "map": {"1": "unbekannt", "2": "normal", "3": "schwach", "4": "leer"},
             "ok": ["2"], "warn": ["1", "3"], "crit": ["4"]},
            {"key": "ups.charge", "label": "Ladung", "group": "USV", "type": "gauge", "unit": "%",
             "oid": "1.3.6.1.2.1.33.1.2.4.0", "op": "<", "warn": 50, "crit": 20, "history": True},
            {"key": "ups.runtime", "label": "Restlaufzeit", "group": "USV", "type": "gauge", "unit": "min",
             "oid": "1.3.6.1.2.1.33.1.2.3.0", "op": "<", "warn": 15, "crit": 5},
            {"key": "ups.source", "label": "Versorgung", "group": "USV", "type": "enum",
             "oid": "1.3.6.1.2.1.33.1.4.1.0",
             "map": {"1": "andere", "2": "keine", "3": "Netz", "4": "Bypass", "5": "Batterie",
                     "6": "Booster", "7": "Reducer"}, "ok": ["3", "6", "7"], "warn": ["4", "1"], "crit": ["2", "5"]},
            {"key": "ups.load", "label": "Last", "group": "USV", "type": "gauge", "unit": "%",
             "warn": 80, "crit": 95, "history": True,
             "table": {"column": "1.3.6.1.2.1.33.1.4.4.1.5"}},
        ],
    },
    {
        "key": "apc", "name": "APC USV (PowerNet)", "vendor": "APC / Schneider",
        "probe": "1.3.6.1.4.1.318.1.1.1.1.1.1.0",
        "metrics": [
            {"key": "apc.battery", "label": "Batterie", "group": "USV", "type": "enum",
             "oid": "1.3.6.1.4.1.318.1.1.1.2.1.1.0",
             "map": {"1": "unbekannt", "2": "normal", "3": "schwach", "4": "Fehler"},
             "ok": ["2"], "warn": ["1"], "crit": ["3", "4"]},
            {"key": "apc.capacity", "label": "Ladung", "group": "USV", "type": "gauge", "unit": "%",
             "oid": "1.3.6.1.4.1.318.1.1.1.2.2.1.0", "op": "<", "warn": 50, "crit": 20, "history": True},
            {"key": "apc.runtime", "label": "Restlaufzeit", "group": "USV", "type": "gauge", "unit": "min",
             "oid": "1.3.6.1.4.1.318.1.1.1.2.2.3.0", "scale": 1 / 6000, "op": "<", "warn": 15, "crit": 5},
            {"key": "apc.load", "label": "Last", "group": "USV", "type": "gauge", "unit": "%",
             "oid": "1.3.6.1.4.1.318.1.1.1.4.2.3.0", "warn": 80, "crit": 95, "history": True},
            {"key": "apc.output", "label": "Ausgang", "group": "USV", "type": "enum",
             "oid": "1.3.6.1.4.1.318.1.1.1.4.1.1.0",
             "map": {"1": "unbekannt", "2": "Netz", "3": "Batterie", "4": "Smart Boost", "5": "aus (Timer)",
                     "6": "Software-Bypass", "7": "aus", "8": "Neustart", "9": "Bypass (Schalter)",
                     "10": "Bypass (Hardware)", "11": "Ruhezustand", "12": "Smart Trim"},
             "ok": ["2", "4", "12"], "warn": ["1", "6", "9", "10"], "crit": ["3", "5", "7", "8", "11"]},
            {"key": "apc.battery_replace", "label": "Batterie tauschen", "group": "USV", "type": "enum",
             "oid": "1.3.6.1.4.1.318.1.1.1.2.2.4.0", "map": {"1": "nein", "2": "ja"},
             "ok": ["1"], "warn": ["2"]},
        ],
    },
    {
        "key": "printer", "name": "Drucker (Printer-MIB)", "vendor": "Standard",
        "probe": "1.3.6.1.2.1.43.5.1.1.1.1",
        "description": "Toner und Verbrauchsmaterial in %, Gerätezustand.",
        "metrics": [
            {"key": "supply", "label": "Füllstand", "group": "Verbrauchsmaterial", "type": "ratio",
             "scale": 100, "unit": "%", "op": "<", "warn": 15, "crit": 5, "history": True,
             "table": {"num": "1.3.6.1.2.1.43.11.1.1.9", "den": "1.3.6.1.2.1.43.11.1.1.8",
                       "label": "1.3.6.1.2.1.43.11.1.1.6", "max_rows": 30}},
            {"key": "printer.status", "label": "Druckerstatus", "group": "Drucker", "type": "enum",
             "table": {"column": "1.3.6.1.2.1.25.3.5.1.1", "max_rows": 4},
             "map": {"1": "andere", "2": "unbekannt", "3": "bereit", "4": "druckt", "5": "wärmt auf"},
             "ok": ["3", "4", "5", "1"], "warn": ["2"]},
            {"key": "printer.pages", "label": "Seitenzähler", "group": "Drucker", "type": "gauge",
             "table": {"column": "1.3.6.1.2.1.43.10.2.1.4", "max_rows": 2}, "alert": False},
        ],
    },
    {
        "key": "synology", "name": "Synology NAS", "vendor": "Synology",
        "probe": "1.3.6.1.4.1.6574.1.1.0",
        "description": "Systemzustand, Temperatur, Lüfter, Platten, RAID, DSM-Update.",
        "metrics": [
            {"key": "syno.system", "label": "System", "group": "NAS", "type": "enum",
             "oid": "1.3.6.1.4.1.6574.1.1.0", "map": {"1": "normal", "2": "Fehler"}, "ok": ["1"], "crit": ["2"]},
            {"key": "syno.temp", "label": "Temperatur", "group": "NAS", "type": "gauge", "unit": "°C",
             "oid": "1.3.6.1.4.1.6574.1.2.0", "warn": 60, "crit": 70, "history": True},
            {"key": "syno.power", "label": "Netzteil", "group": "NAS", "type": "enum",
             "oid": "1.3.6.1.4.1.6574.1.3.0", "map": {"1": "normal", "2": "Fehler"}, "ok": ["1"], "crit": ["2"]},
            {"key": "syno.fan", "label": "Systemlüfter", "group": "NAS", "type": "enum",
             "oid": "1.3.6.1.4.1.6574.1.4.1.0", "map": {"1": "normal", "2": "Fehler"}, "ok": ["1"], "crit": ["2"]},
            {"key": "syno.update", "label": "DSM-Update", "group": "NAS", "type": "enum",
             "oid": "1.3.6.1.4.1.6574.1.5.4.0",
             "map": {"1": "verfügbar", "2": "aktuell", "3": "verbinde", "4": "getrennt", "5": "andere"},
             "ok": ["2", "3", "4", "5"], "warn": ["1"]},
            {"key": "syno.disk", "label": "Platte", "group": "Platten", "type": "enum",
             "table": {"column": "1.3.6.1.4.1.6574.2.1.1.5", "label": "1.3.6.1.4.1.6574.2.1.1.2"},
             "map": {"1": "normal", "2": "initialisiert", "3": "nicht initialisiert",
                     "4": "Systempartition defekt", "5": "defekt"},
             "ok": ["1", "2"], "warn": ["3"], "crit": ["4", "5"]},
            {"key": "syno.disk_temp", "label": "Plattentemperatur", "group": "Platten", "type": "gauge",
             "unit": "°C", "warn": 55, "crit": 65,
             "table": {"column": "1.3.6.1.4.1.6574.2.1.1.6", "label": "1.3.6.1.4.1.6574.2.1.1.2"}},
            {"key": "syno.raid", "label": "RAID", "group": "RAID", "type": "enum",
             "table": {"column": "1.3.6.1.4.1.6574.3.1.1.3", "label": "1.3.6.1.4.1.6574.3.1.1.2"},
             "map": {"1": "normal", "2": "repariert", "3": "migriert", "4": "erweitert", "5": "wird gelöscht",
                     "6": "wird angelegt", "7": "RAID-Sync", "8": "Paritätsprüfung", "9": "assembliert",
                     "10": "wird abgebrochen", "11": "beeinträchtigt", "12": "abgestürzt",
                     "13": "Daten-Scrubbing", "14": "RAID-Deploying", "15": "RAID-Undeploying",
                     "16": "RAID-Mount-Cache", "17": "RAID-Unmount-Cache", "18": "RAID-Expanding (unfinished)",
                     "19": "RAID-Convert-SHR", "20": "RAID-Migrating-Online", "21": "RAID-Data-Scrubbing"},
             "ok": ["1", "3", "4", "6", "7", "8", "9", "13", "14", "15", "16", "17", "19", "20", "21"],
             "warn": ["2", "5", "10", "18"], "crit": ["11", "12"]},
        ],
    },
    {
        "key": "cisco", "name": "Cisco (CPU, Speicher)", "vendor": "Cisco",
        "probe": "1.3.6.1.4.1.9.9.109.1.1.1.1.8",
        "metrics": [
            {"key": "cisco.cpu", "label": "CPU (5 min)", "group": "System", "type": "gauge", "unit": "%",
             "aggregate": "max", "warn": 80, "crit": 95, "history": True,
             "table": {"column": "1.3.6.1.4.1.9.9.109.1.1.1.1.8"}},
            {"key": "cisco.mem", "label": "Speicher belegt", "group": "System", "type": "ratio", "scale": 100,
             "unit": "%", "warn": 85, "crit": 95, "history": True,
             "table": {"num": "1.3.6.1.4.1.9.9.48.1.1.1.5", "den": "1.3.6.1.4.1.9.9.48.1.1.1.6",
                       "den_plus_num": True, "label": "1.3.6.1.4.1.9.9.48.1.1.1.2"}},
        ],
    },
    {
        "key": "mikrotik", "name": "MikroTik (Gesundheit)", "vendor": "MikroTik",
        "probe": "1.3.6.1.4.1.14988.1.1.4.4.0",
        "description": "Temperatur, Spannung, RouterOS-Version. CPU/Speicher über Host-Ressourcen.",
        "metrics": [
            {"key": "mt.temp", "label": "Temperatur", "group": "Gesundheit", "type": "gauge", "unit": "°C",
             "oid": "1.3.6.1.4.1.14988.1.1.3.10.0", "scale": 0.1, "warn": 65, "crit": 80, "history": True},
            {"key": "mt.cpu_temp", "label": "CPU-Temperatur", "group": "Gesundheit", "type": "gauge", "unit": "°C",
             "oid": "1.3.6.1.4.1.14988.1.1.3.11.0", "scale": 0.1, "warn": 75, "crit": 90},
            {"key": "mt.voltage", "label": "Spannung", "group": "Gesundheit", "type": "gauge", "unit": "V",
             "oid": "1.3.6.1.4.1.14988.1.1.3.8.0", "scale": 0.1, "alert": False},
            {"key": "mt.version", "label": "RouterOS", "group": "System", "type": "text",
             "oid": "1.3.6.1.4.1.14988.1.1.4.4.0"},
        ],
    },
    {
        "key": "fortigate", "name": "FortiGate", "vendor": "Fortinet",
        "probe": "1.3.6.1.4.1.12356.101.4.1.1.0",
        "metrics": [
            {"key": "fg.version", "label": "FortiOS", "group": "System", "type": "text",
             "oid": "1.3.6.1.4.1.12356.101.4.1.1.0"},
            {"key": "fg.cpu", "label": "CPU", "group": "System", "type": "gauge", "unit": "%",
             "oid": "1.3.6.1.4.1.12356.101.4.1.3.0", "warn": 80, "crit": 95, "history": True},
            {"key": "fg.mem", "label": "Speicher", "group": "System", "type": "gauge", "unit": "%",
             "oid": "1.3.6.1.4.1.12356.101.4.1.4.0", "warn": 85, "crit": 95, "history": True},
            {"key": "fg.sessions", "label": "Sitzungen", "group": "System", "type": "gauge",
             "oid": "1.3.6.1.4.1.12356.101.4.1.8.0", "alert": False, "history": True},
            {"key": "fg.disk", "label": "Platte belegt", "group": "System", "type": "ratio", "scale": 100,
             "unit": "%", "warn": 85, "crit": 95,
             "num": "1.3.6.1.4.1.12356.101.4.1.6.0", "den": "1.3.6.1.4.1.12356.101.4.1.7.0"},
        ],
    },
    {
        "key": "ucd", "name": "Linux/Unix (Net-SNMP Last)", "vendor": "Net-SNMP",
        "probe": "1.3.6.1.4.1.2021.10.1.3.1",
        "description": "Load average, Swap — Linux-Server, Synology, QNAP, pfSense.",
        "metrics": [
            {"key": "ucd.load1", "label": "Load (1 min)", "group": "System", "type": "gauge",
             "oid": "1.3.6.1.4.1.2021.10.1.5.1", "scale": 0.01, "alert": False, "history": True},
            {"key": "ucd.load15", "label": "Load (15 min)", "group": "System", "type": "gauge",
             "oid": "1.3.6.1.4.1.2021.10.1.5.3", "scale": 0.01, "alert": False},
            {"key": "ucd.swap", "label": "Swap frei", "group": "Speicher", "type": "ratio", "scale": 100,
             "unit": "%", "op": "<", "warn": 20, "crit": 5,
             "num": "1.3.6.1.4.1.2021.4.4.0", "den": "1.3.6.1.4.1.2021.4.3.0"},
        ],
    },
]

TEMPLATE_INDEX = {t["key"]: t for t in BUILTIN_TEMPLATES}
