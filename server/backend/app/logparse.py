# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Rohzeilen lesbar machen (Anzeige-Parser fuer App und UI)
# ==============================================================================
"""
Aus einer Rohzeile etwas machen, das ein Mensch lesen kann.

Das Problem: `raw_message` ist genau das, was das Geraet geschickt hat. Bei
einer FRITZ!Box ist das ein Satz, bei einem Switch eine Zahlenwurst, bei einer
Firewall dreissig `key=value`-Paare hintereinander, bei einem Container eine
JSON-Zeile. In der App steht davon bisher der komplette Klumpen - unlesbar.

Dieses Modul zerlegt die Zeile in drei Dinge:

* `summary`  - ein kurzer Satz, der in eine Zeile passt. Das ist es, was in der
               Liste steht.
* `fields`   - die erkannten Schluessel/Wert-Paare, sortiert und benannt.
* `highlights` - die paar Angaben, die fast immer interessieren (Quell-IP,
               Ziel-IP, Benutzer, Ereignis-ID, Rueckgabewert). Die App kann sie
               als Abzeichen zeigen, ohne die ganze Tabelle aufzuklappen.

Bewusst getrennt vom Syslog-Server: der schreibt beim Empfang und darf nichts
verlieren. Hier wird nur *angezeigt* - wenn die Erkennung danebenliegt, steht
die Rohzeile weiterhin unveraendert daneben.

Der Parser aendert nie Daten in der Datenbank.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# =============================================================================
# Namenstabellen
# =============================================================================
SEVERITY_NAMES = {
    0: "emergency", 1: "alert", 2: "critical", 3: "error",
    4: "warning", 5: "notice", 6: "info", 7: "debug",
}

FACILITY_NAMES = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth", 5: "syslog",
    6: "lpr", 7: "news", 8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
    12: "ntp", 13: "audit", 14: "alert", 15: "clock",
    16: "local0", 17: "local1", 18: "local2", 19: "local3",
    20: "local4", 21: "local5", 22: "local6", 23: "local7",
}

# Schluessel, die in Firewall- und Windows-Logs immer wieder auftauchen. Der
# Klarname macht aus "srcip" ein "Quell-IP" - das ist der halbe Gewinn.
FIELD_LABELS = {
    "srcip": "Quell-IP", "src_ip": "Quell-IP", "src": "Quelle",
    "source_ip": "Quell-IP", "saddr": "Quell-IP", "clientip": "Client-IP",
    "dstip": "Ziel-IP", "dst_ip": "Ziel-IP", "dst": "Ziel", "daddr": "Ziel-IP",
    "srcport": "Quell-Port", "dstport": "Ziel-Port", "sport": "Quell-Port",
    "dport": "Ziel-Port", "port": "Port", "proto": "Protokoll",
    "protocol": "Protokoll", "action": "Aktion", "policyid": "Regel",
    "rule": "Regel", "user": "Benutzer", "username": "Benutzer",
    "usr": "Benutzer", "account": "Konto", "uid": "Benutzer-ID",
    "pid": "Prozess-ID", "ppid": "Eltern-Prozess", "exe": "Programm",
    "cmd": "Befehl", "command": "Befehl", "unit": "systemd-Unit",
    "service": "Dienst", "status": "Status", "result": "Ergebnis",
    "duration": "Dauer", "bytes": "Bytes", "sentbyte": "Gesendet",
    "rcvdbyte": "Empfangen", "url": "Adresse", "method": "Methode",
    "path": "Pfad", "host": "Host", "hostname": "Hostname",
    "eventid": "Ereignis-ID", "event_id": "Ereignis-ID", "channel": "Kanal",
    "level": "Stufe", "severity": "Schweregrad", "facility": "Bereich",
    "mac": "MAC-Adresse", "ssid": "WLAN", "interface": "Schnittstelle",
    "if": "Schnittstelle", "iface": "Schnittstelle", "vlan": "VLAN",
    "session": "Sitzung", "sessionid": "Sitzung", "reason": "Grund",
    "msg": "Meldung", "message": "Meldung", "error": "Fehler",
    "exit_code": "Rückgabewert", "code": "Code",
    # Netfilter/iptables schreibt in Grossbuchstaben und ohne "ip" im Namen.
    "in": "Eingang", "out": "Ausgang", "spt": "Quell-Port", "dpt": "Ziel-Port",
    "len": "Länge", "ttl": "TTL", "app": "Programm", "model": "Modell",
    "devname": "Gerät", "policyid": "Regel",
}

# Diese Felder kommen in der App als Abzeichen nach oben - in dieser Reihenfolge.
HIGHLIGHT_ORDER = [
    "action", "result", "status", "user", "username", "account",
    "srcip", "src_ip", "source_ip", "src", "clientip",
    "dstip", "dst_ip", "dst",
    "dstport", "dport", "dpt", "proto", "protocol", "eventid", "event_id",
    "unit", "service", "exe", "interface", "in", "out", "ssid", "mac",
    "exit_code", "code", "reason",
]

MAX_HIGHLIGHTS = 6
MAX_FIELDS = 40
MAX_SUMMARY = 240


# =============================================================================
# Muster
# =============================================================================
_PRI = re.compile(r"^<(\d{1,3})>")

# RFC5424: <34>1 2024-01-01T00:00:00Z host app pid msgid [sd] message
_RFC5424 = re.compile(
    r"^<(?P<pri>\d{1,3})>1\s+(?P<ts>\S+)\s+(?P<host>\S+)\s+(?P<app>\S+)\s+"
    r"(?P<procid>\S+)\s+(?P<msgid>\S+)\s+(?P<sd>-|\[.*?\](?:\[.*?\])*)\s*(?P<msg>.*)$",
    re.DOTALL,
)

# RFC3164: <34>Oct 11 22:14:15 host prog[123]: message
_RFC3164 = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}(?:\s+\d{4})?\s+"
    r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?P<host>\S+)\s+"
    r"(?P<prog>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?:\s*(?P<msg>.*)$",
    re.DOTALL,
)

# UniFi Netconsole: <6>{f1d1} [1234.567890] daemon[12]: message
_UNIFI_CONSOLE = re.compile(
    r"^<(?P<pri>\d+)>\{(?P<seq>[0-9a-fA-F]+)\}\s*\[(?P<uptime>[\d.]+)\]\s*"
    r"(?P<prog>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?:\s*(?P<msg>.*)$",
    re.DOTALL,
)

# UniFi MAC/Modell: <30>784558fc21cf,U6-LR-6.7.31+15618: hostapd: message
_UNIFI_MAC = re.compile(
    r"^<(?P<pri>\d+)>(?P<mac>[0-9a-fA-F]{12}),(?P<model>[^:]+):\s*"
    r"(?P<prog>[^\s:]+):\s*(?P<msg>.*)$",
    re.DOTALL,
)

# Cisco IOS: %LINK-3-UPDOWN: Interface ...
_CISCO = re.compile(r"%(?P<facility>[A-Z0-9_]+)-(?P<sev>\d)-(?P<mnemonic>[A-Z0-9_]+):\s*(?P<msg>.*)$",
                    re.DOTALL)

# key=value und key="value with spaces"
_KV = re.compile(r'(?P<key>[A-Za-z_][\w.\-]*)=(?P<value>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s]*)')

# Fuehrender Zeitstempel in der eigentlichen Nachricht - der steht schon in der Spalte.
_LEADING_TS = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|"
    r"[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+"
)

# Kernel-Zeitstempel: [ 1234.567890] am Zeilenanfang
_KERNEL_UPTIME = re.compile(r"^\[\s*(?P<uptime>\d+\.\d+)\]\s*")

# Strukturierte Daten aus RFC5424: [id key="v" key2="v2"]
_SD_ELEMENT = re.compile(r'\[(?P<id>[^\s\]]+)(?P<params>(?:\s+[\w.\-]+="(?:[^"\\]|\\.)*")*)\]')

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")

# Zeilen, die nur aus Trennern und Hex bestehen, bringen niemandem etwas.
_NOISE = re.compile(r"^[\s\-=_*#|.]{4,}$")


# =============================================================================
# Hilfsfunktionen
# =============================================================================
def _unquote(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return value


def _label_for(key: str) -> str:
    return FIELD_LABELS.get(key.lower(), key)


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def decode_priority(pri: int) -> Tuple[int, int, str, str]:
    """<134> -> (Facility 16, Severity 6, 'local0', 'info')."""
    facility, severity = divmod(int(pri), 8)
    return (facility, severity,
            FACILITY_NAMES.get(facility, f"facility{facility}"),
            SEVERITY_NAMES.get(severity, str(severity)))


def _kind_of(key: str, value: str) -> str:
    """Womit hat man es zu tun? Steuert nur die Darstellung in der App."""
    lowered = key.lower()
    if _looks_like_ip(value):
        return "ip"
    if _MAC.fullmatch(value or ""):
        return "mac"
    if lowered in ("action", "result", "status", "level", "severity"):
        return "state"
    if lowered.endswith(("port", "id", "pid", "code")) and value.isdigit():
        return "number"
    if value.startswith(("http://", "https://")):
        return "url"
    return "text"


def _collect_kv(text: str) -> Tuple[Dict[str, str], str]:
    """Zieht key=value-Paare heraus. Gibt (Paare, Resttext) zurueck.

    Nur wenn mindestens zwei Paare gefunden werden - ein einzelnes `x=1` mitten
    im Satz ist keine Struktur, sondern ein Satzteil.
    """
    matches = list(_KV.finditer(text or ""))
    if len(matches) < 2:
        return {}, text

    fields: Dict[str, str] = {}
    for match in matches:
        key = match.group("key")
        if key in fields:
            continue
        fields[key] = _unquote(match.group("value"))

    remainder = _KV.sub("", text).strip(" ,;|")
    return fields, remainder


def _flatten_json(data: Any, prefix: str = "", out: Optional[Dict[str, str]] = None,
                  depth: int = 0) -> Dict[str, str]:
    """Verschachteltes JSON auf flache Schluessel bringen (a.b.c = wert)."""
    out = {} if out is None else out
    if depth > 4 or len(out) > MAX_FIELDS:
        return out
    if isinstance(data, dict):
        for key, value in data.items():
            _flatten_json(value, f"{prefix}.{key}" if prefix else str(key), out, depth + 1)
    elif isinstance(data, list):
        if all(not isinstance(v, (dict, list)) for v in data):
            out[prefix or "items"] = ", ".join(str(v) for v in data[:20])
        else:
            for index, value in enumerate(data[:10]):
                _flatten_json(value, f"{prefix}[{index}]", out, depth + 1)
    elif data is not None:
        out[prefix or "value"] = str(data)
    return out


def _try_json(text: str) -> Optional[Dict[str, str]]:
    """JSON-Zeile erkennen und flach machen. None, wenn es keine ist."""
    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _flatten_json(data)


def _parse_structured_data(raw: str) -> Dict[str, str]:
    """Die [id key="v"]-Bloecke aus RFC5424 in flache Felder."""
    fields: Dict[str, str] = {}
    for element in _SD_ELEMENT.finditer(raw or ""):
        element_id = element.group("id")
        for param in _KV.finditer(element.group("params") or ""):
            key = param.group("key")
            name = key if element_id in ("-", "") else f"{element_id}.{key}"
            fields[name] = _unquote(param.group("value"))
    return fields


# =============================================================================
# Hauptfunktion
# =============================================================================
def parse(raw: str, message: str = "", level: str = "", source: str = "",
          facility: Optional[int] = None, extra: Optional[dict] = None) -> dict:
    """Zerlegt eine Rohzeile in etwas Lesbares.

    `raw` ist die Rohzeile aus der Datenbank, `message`/`level`/`source` sind
    die bereits beim Empfang bestimmten Werte - sie dienen als Rueckfallebene,
    wenn sich aus der Rohzeile nichts holen laesst.
    """
    raw = (raw or "").strip()
    result: Dict[str, Any] = {
        "format": "plain",
        "summary": "",
        "fields": {},
        "highlights": [],
        "meta": {},
        "readable": False,
    }

    if not raw:
        result["summary"] = (message or "").strip()
        result["meta"] = _meta_from_known(level, source, facility)
        return _finish(result, message)

    body = raw
    meta: Dict[str, Any] = {}

    # -- 1. Rahmen erkennen ---------------------------------------------------
    if (match := _RFC5424.match(raw)):
        result["format"] = "rfc5424"
        facility_num, severity_num, facility_name, severity_name = decode_priority(match.group("pri"))
        meta.update({
            "timestamp": match.group("ts"), "host": match.group("host"),
            "app": _dash(match.group("app")), "pid": _dash(match.group("procid")),
            "msgid": _dash(match.group("msgid")),
            "facility": facility_name, "facility_code": facility_num,
            "severity": severity_name, "severity_code": severity_num,
        })
        result["fields"].update(_parse_structured_data(match.group("sd")))
        body = match.group("msg")

    elif (match := _UNIFI_CONSOLE.match(raw)):
        result["format"] = "unifi-netconsole"
        facility_num, severity_num, facility_name, severity_name = decode_priority(match.group("pri"))
        meta.update({
            # {f1d1} ist eine Sequenznummer, kein Hostname - genau die Verwechslung
            # macht UniFi-Logs sonst unlesbar.
            "sequence": match.group("seq"), "uptime_seconds": match.group("uptime"),
            "app": match.group("prog"), "pid": match.group("pid"),
            "facility": facility_name, "facility_code": facility_num,
            "severity": severity_name, "severity_code": severity_num,
        })
        body = match.group("msg")

    elif (match := _UNIFI_MAC.match(raw)):
        result["format"] = "unifi"
        facility_num, severity_num, facility_name, severity_name = decode_priority(match.group("pri"))
        mac = match.group("mac")
        meta.update({
            "mac": ":".join(mac[i:i + 2] for i in range(0, 12, 2)),
            "model": match.group("model"), "app": match.group("prog"),
            "facility": facility_name, "facility_code": facility_num,
            "severity": severity_name, "severity_code": severity_num,
        })
        body = match.group("msg")

    elif (match := _RFC3164.match(raw)):
        result["format"] = "rfc3164"
        facility_num, severity_num, facility_name, severity_name = decode_priority(match.group("pri"))
        meta.update({
            "timestamp": match.group("ts"), "host": match.group("host"),
            "app": match.group("prog"), "pid": match.group("pid"),
            "facility": facility_name, "facility_code": facility_num,
            "severity": severity_name, "severity_code": severity_num,
        })
        body = match.group("msg")

    elif (match := _PRI.match(raw)):
        result["format"] = "syslog-priority"
        facility_num, severity_num, facility_name, severity_name = decode_priority(match.group(1))
        meta.update({"facility": facility_name, "facility_code": facility_num,
                     "severity": severity_name, "severity_code": severity_num})
        body = raw[match.end():]

    # -- 2. Rest aufraeumen ---------------------------------------------------
    body = (body or "").strip()

    if (match := _KERNEL_UPTIME.match(body)):
        meta.setdefault("uptime_seconds", match.group("uptime"))
        body = body[match.end():]

    # Der Zeitstempel steht schon in einer eigenen Spalte. Ein zweiter am
    # Anfang der Meldung kostet nur Platz.
    body = _LEADING_TS.sub("", body).strip()

    # -- 3. Inhalt zerlegen ---------------------------------------------------
    if (json_fields := _try_json(body)) is not None:
        result["format"] = "json" if result["format"] == "plain" else result["format"] + "+json"
        result["fields"].update(json_fields)
        # Die Meldung selbst steckt in einem der ueblichen Schluessel.
        for key in ("message", "msg", "log", "text", "event", "description"):
            if key in json_fields and json_fields[key].strip():
                body = json_fields[key]
                break
        else:
            body = ""
    else:
        if (cisco := _CISCO.search(body)):
            result["format"] = "cisco-ios"
            meta.update({"cisco_facility": cisco.group("facility"),
                         "cisco_severity": cisco.group("sev"),
                         "cisco_mnemonic": cisco.group("mnemonic")})
            body = cisco.group("msg")

        kv_fields, remainder = _collect_kv(body)
        if kv_fields:
            result["format"] = ("logfmt" if result["format"] == "plain"
                                else result["format"] + "+logfmt")
            result["fields"].update(kv_fields)
            body = remainder

    # -- 4. Zusammenfassung ---------------------------------------------------
    summary = " ".join((body or "").split())
    if _NOISE.match(summary):
        summary = ""

    # Erst aufraeumen, dann zusammenfassen: ein leeres "OUT=" darf nicht als
    # "Ausgang: " in der Zusammenfassung landen.
    result["fields"] = _trim_fields(result["fields"])

    if not summary:
        # Kein freier Text uebrig? Dann aus den wichtigsten Feldern einen Satz
        # bauen - besser als eine leere Zeile in der Liste.
        summary = _summary_from_fields(result["fields"], meta) or (message or "").strip()

    if len(summary) > MAX_SUMMARY:
        summary = summary[:MAX_SUMMARY - 1].rstrip() + "…"

    # -- 5. Bekanntes ergaenzen und aufraeumen --------------------------------
    known = _meta_from_known(level, source, facility)
    for key, value in known.items():
        meta.setdefault(key, value)

    if extra:
        for key in ("event_id", "group", "ingested_via"):
            if extra.get(key) is not None:
                meta.setdefault(key, extra[key])

    result["summary"] = summary
    result["meta"] = {k: v for k, v in meta.items() if v not in (None, "", "-")}
    result["highlights"] = _build_highlights(result["fields"], result["meta"])
    return _finish(result, message)


def _finish(result: dict, message: str) -> dict:
    """Letzter Schliff: hat sich das Zerlegen ueberhaupt gelohnt?"""
    original = " ".join((message or "").split())
    result["readable"] = bool(
        result["fields"] or result["highlights"]
        or (result["summary"] and result["summary"] != original)
        or result["format"] not in ("plain",)
    )
    if not result["summary"]:
        result["summary"] = original
    return result


def _dash(value: Optional[str]) -> Optional[str]:
    """RFC5424 schreibt '-' fuer 'nicht gesetzt'."""
    return None if value in ("-", "", None) else value


def _meta_from_known(level: str, source: str, facility: Optional[int]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if level:
        meta["severity"] = level
    if source:
        meta["app"] = source
    if facility is not None:
        meta["facility"] = FACILITY_NAMES.get(int(facility), str(facility))
        meta["facility_code"] = int(facility)
    return meta


def _trim_fields(fields: Dict[str, str]) -> Dict[str, str]:
    """Leere Werte weg, zu lange kuerzen, Anzahl begrenzen."""
    cleaned: Dict[str, str] = {}
    for key, value in fields.items():
        text = str(value).strip()
        if not text or text == "-":
            continue
        if len(text) > 500:
            text = text[:499] + "…"
        cleaned[key] = text
        if len(cleaned) >= MAX_FIELDS:
            break
    return cleaned


def _build_highlights(fields: Dict[str, str], meta: Dict[str, Any]) -> List[dict]:
    """Die paar Angaben, die fast immer interessieren - als Abzeichen."""
    lowered = {key.lower(): (key, value) for key, value in fields.items()}
    highlights: List[dict] = []
    seen: set = set()

    for wanted in HIGHLIGHT_ORDER:
        if wanted not in lowered:
            continue
        key, value = lowered[wanted]
        if value in seen:
            continue
        seen.add(value)
        highlights.append({"key": key, "label": _label_for(key), "value": value,
                           "kind": _kind_of(key, value)})
        if len(highlights) >= MAX_HIGHLIGHTS:
            return highlights

    # Nichts Benanntes gefunden? Dann wenigstens die IPs aus dem Text.
    if not highlights:
        for key in ("mac", "model", "app", "unit"):
            if meta.get(key):
                highlights.append({"key": key, "label": _label_for(key),
                                   "value": str(meta[key]), "kind": _kind_of(key, str(meta[key]))})
        if len(highlights) >= MAX_HIGHLIGHTS:
            return highlights[:MAX_HIGHLIGHTS]

    return highlights


def _summary_from_fields(fields: Dict[str, str], meta: Dict[str, Any]) -> str:
    """Baut aus Feldern einen Satz, wenn kein freier Text uebrig blieb."""
    lowered = {key.lower(): value for key, value in fields.items()}

    action = lowered.get("action") or lowered.get("result") or lowered.get("status")
    source_ip = (lowered.get("srcip") or lowered.get("src_ip")
                 or lowered.get("source_ip") or lowered.get("src"))
    dest_ip = lowered.get("dstip") or lowered.get("dst_ip") or lowered.get("dst")
    dest_port = lowered.get("dstport") or lowered.get("dport") or lowered.get("dpt")
    protocol = lowered.get("proto") or lowered.get("protocol")
    user = lowered.get("user") or lowered.get("username") or lowered.get("account")

    parts: List[str] = []
    if action:
        parts.append(str(action).upper())
    if user:
        parts.append(f"Benutzer {user}")
    if source_ip:
        parts.append(f"von {source_ip}")
    if dest_ip:
        parts.append(f"nach {dest_ip}" + (f":{dest_port}" if dest_port else ""))
    if protocol and (source_ip or dest_ip):
        parts.append(f"({protocol})")
    if not parts and lowered.get("reason"):
        parts.append(str(lowered["reason"]))

    if parts:
        return " ".join(parts)

    # Immer noch nichts? Die ersten drei Felder als "k: v" - besser als leer.
    pairs = [f"{_label_for(k)}: {v}" for k, v in list(fields.items())[:3]]
    return " · ".join(pairs)


# =============================================================================
# Bequemer Zugriff fuer die Endpunkte
# =============================================================================
def parse_log_row(row: Any) -> dict:
    """Zerlegt eine Log-Zeile aus der Datenbank (ORM-Objekt oder dict)."""
    def field(name: str, default: Any = None) -> Any:
        if isinstance(row, dict):
            return row.get(name, default)
        return getattr(row, name, default)

    return parse(
        raw=field("raw_message") or field("message") or "",
        message=field("message") or "",
        level=field("level") or "",
        source=field("source") or "",
        facility=field("facility"),
        extra=field("extra_data") or {},
    )
