# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.31.23.30.00
# Beschreibung: RIMPERIO - Logs API Endpoints
# ==============================================================================

import csv
import io
import json
import logging
import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc, text, or_
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db, async_session
from ..models import Log, Agent, User
from ..schemas import LogResponse, LogDetailResponse, LogListResponse, LogStatsResponse
from ..auth import get_current_user
from .. import logparse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/logs", tags=["Logs"])

# Columns die die Logliste wirklich braucht (reduziert I/O)
LIST_COLUMNS = (
    Log.id,
    Log.hostname,
    Log.ip_address,
    Log.timestamp,
    Log.level,
    Log.source,
    Log.message,
    # Zusammengefasste Wiederholungen ("5.000x"): steht in extra_data, wird
    # hier als eigene kleine Spalte mitgegeben statt das ganze JSON.
    Log.extra_data["repeat"].as_integer().label("repeat"),
)

# Dieselbe Liste, aber mit allem, was der Anzeige-Parser braucht.
# Bewusst getrennt: `raw_message` ist die mit Abstand groesste Spalte, und wer
# nur ueberfliegen will, soll sie nicht bei jeder Seite mitschleppen.
READABLE_COLUMNS = LIST_COLUMNS + (Log.raw_message, Log.facility, Log.extra_data)

# Wie viele Zeilen hoechstens durch den Parser gehen. Das Zerlegen ist reine
# Textarbeit im Arbeitsspeicher (ein paar Mikrosekunden je Zeile), aber bei
# 1000 Zeilen pro Seite summiert sich auch das - und niemand liest 1000 Zeilen
# am Stueck.
MAX_READABLE_ROWS = 300


def _readable_items(rows: list) -> list:
    """Haengt jeder Zeile ihre lesbare Fassung an.

    Die Rohzeile wandert mit in die Antwort: wenn die Erkennung danebenliegt,
    muss man sehen koennen, was wirklich ankam. Genau deshalb aendert der
    Parser auch nie etwas in der Datenbank - er stellt nur anders dar.
    """
    items = []
    for row in rows:
        item = dict(row)
        parsed = logparse.parse_log_row(item)
        item["parsed"] = {
            "summary": parsed["summary"],
            "format": parsed["format"],
            "readable": parsed["readable"],
            "highlights": parsed["highlights"],
            "fields": parsed["fields"],
            "meta": parsed["meta"],
        }
        # facility und extra_data haben ihren Dienst getan - sie stecken jetzt
        # in `parsed` und muessen nicht doppelt ueber die Leitung.
        item.pop("facility", None)
        item.pop("extra_data", None)
        items.append(item)
    return items

# Kleiner In-Memory-Cache für Dashboard-Stats, um wiederholte Aufrufe zu entlasten
_stats_cache = None
_stats_cache_expires = datetime.min
_stats_cache_lock = asyncio.Lock()
_stats_cache_ttl = int(os.getenv("LOG_STATS_CACHE_SECONDS", "30"))


_LIKE_ESCAPE_CHAR = "\\"
_LIKE_ESCAPE_TABLE = str.maketrans({
    _LIKE_ESCAPE_CHAR: _LIKE_ESCAPE_CHAR * 2,
    "%": f"{_LIKE_ESCAPE_CHAR}%",
    "_": f"{_LIKE_ESCAPE_CHAR}_",
})


def _escape_like(value: str) -> str:
    """Escapt LIKE-Sonderzeichen (%, _, \\) damit User-Input nicht als Wildcard wirkt."""
    return value.translate(_LIKE_ESCAPE_TABLE)


# ==============================================================================
# Logtypen: Schweregrad-Gruppen, Kategorien, Facilities
# ==============================================================================

# Syslog-Severity: kleiner = dringender. Schreibweisen der Agenten variieren.
SEVERITY_ORDER = {
    "emergency": 0, "emerg": 0, "panic": 0,
    "alert": 1,
    "critical": 2, "crit": 2,
    "error": 3, "err": 3,
    "warning": 4, "warn": 4,
    "notice": 5,
    "info": 6, "information": 6, "informational": 6,
    "debug": 7,
}

# Auswahl fuer das UI: "ab diesem Schweregrad und dringender".
# Die keys muessen in SEVERITY_ORDER stehen - daraus wird die Levelliste gebildet.
SEVERITY_GROUPS = [
    {"key": "critical", "label": "Nur kritisch"},
    {"key": "error", "label": "Fehler und dringender"},
    {"key": "warning", "label": "Warnungen und dringender"},
    {"key": "notice", "label": "Notice und dringender"},
    {"key": "info", "label": "Info und dringender"},
]

# Facility-Nummern nach RFC 5424.
FACILITY_NAMES = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth", 5: "syslog",
    6: "lpr", 7: "news", 8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
    12: "ntp", 13: "audit", 14: "alert", 15: "clock",
    16: "local0", 17: "local1", 18: "local2", 19: "local3",
    20: "local4", 21: "local5", 22: "local6", 23: "local7",
}

# Fachliche Kategorien: Treffer ueber Facility ODER Dienstname (source).
LOG_CATEGORIES = {
    "auth": {
        "label": "Anmeldung & Rechte",
        "facilities": [4, 10],
        "sources": ["sshd", "sshd-session", "sudo", "su", "login", "polkit", "polkitd",
                    "systemd-logind", "pam_unix", "gdm-password", "security",
                    "fritzbox-auth"],
    },
    "kernel": {
        "label": "Kernel",
        "facilities": [0],
        "sources": ["kernel"],
    },
    "network": {
        "label": "Netzwerk",
        "facilities": [12],
        "sources": ["networkmanager", "dhclient", "dhcpcd", "dhcpd", "systemd-networkd",
                    "systemd-resolved", "wpa_supplicant", "netplan", "named", "chronyd", "ntpd",
                    "fritzbox-net", "fritzbox-wlan"],
    },
    "firewall": {
        "label": "Firewall & Abwehr",
        "facilities": [],
        "sources": ["ufw", "iptables", "nftables", "firewalld", "fail2ban", "fail2ban-server"],
    },
    "container": {
        "label": "Container",
        "facilities": [],
        "sources": ["docker", "dockerd", "containerd", "podman", "kubelet"],
    },
    "cron": {
        "label": "Zeitpläne (Cron)",
        "facilities": [9],
        "sources": ["cron", "crond", "anacron", "systemd-cron"],
    },
    "mail": {
        "label": "Mail",
        "facilities": [2],
        "sources": ["postfix", "dovecot", "sendmail", "exim", "opendkim"],
    },
    "system": {
        "label": "System & Dienste",
        "facilities": [3, 5],
        "sources": ["systemd", "init", "rsyslogd", "syslog-ng", "systemd-journald", "journald",
                    "fritzbox-sys"],
    },
    "audit": {
        "label": "Audit",
        "facilities": [13],
        "sources": ["auditd", "audit", "fritzbox-audit"],
    },
}


def _levels_up_to(max_severity: int) -> List[str]:
    """Alle Level-Schreibweisen, die mindestens so dringend sind wie max_severity."""
    return [name for name, order in SEVERITY_ORDER.items() if order <= max_severity]


def _severity_condition(min_severity: str):
    """Bedingung für eine Schweregrad-Gruppe ('error' = Fehler und dringender)."""
    key = min_severity.strip().lower()
    threshold = SEVERITY_ORDER.get(key)
    if threshold is None:
        raise HTTPException(status_code=400, detail=f"Unbekannter Schweregrad: {min_severity}")
    return func.lower(Log.level).in_(_levels_up_to(threshold))


def _category_condition(category: str):
    """Bedingung für eine Logtyp-Kategorie (Facility ODER bekannter Dienstname)."""
    key = category.strip().lower()
    entry = LOG_CATEGORIES.get(key)
    if not entry:
        raise HTTPException(status_code=400, detail=f"Unbekannte Kategorie: {category}")

    clauses = []
    if entry["facilities"]:
        clauses.append(Log.facility.in_(entry["facilities"]))
    # ilike statt lower(): nutzt den vorhandenen Trigramm-Index auf source.
    for name in entry["sources"]:
        clauses.append(Log.source.ilike(name))
    if not clauses:
        raise HTTPException(status_code=400, detail=f"Kategorie ohne Kriterien: {category}")
    return or_(*clauses)


def _apply_filters(
    query, hostname, level, source, search, start_date, end_date,
    hostname_exact=False, min_severity=None, category=None, facility=None, device_type=None,
):
    """Filter-Bedingungen auf eine Query anwenden.

    hostname_exact=True -> exakter (case-insensitiver) Hostname statt Teilstring.
    Wird von der Geraete-Ansicht genutzt, damit z.B. "srv1" nicht auch "srv10" zieht.

    min_severity / category / facility / device_type sind die "Logtyp"-Filter:
    Schweregrad-Gruppe, fachliche Kategorie, Syslog-Facility und Art des Geraets.
    """
    hostname = hostname.strip() if hostname else None
    level = level.strip() if level else None
    source = source.strip() if source else None
    search = search.strip() if search else None

    if hostname and hostname_exact:
        # ilike ohne Wildcards: case-insensitiv und nutzt den Trigramm-Index.
        query = query.where(Log.hostname.ilike(_escape_like(hostname), escape=_LIKE_ESCAPE_CHAR))
    elif hostname:
        query = query.where(Log.hostname.ilike(f"%{_escape_like(hostname)}%", escape=_LIKE_ESCAPE_CHAR))
    if level:
        query = query.where(func.lower(Log.level) == level.lower())
    if source:
        query = query.where(Log.source.ilike(f"%{_escape_like(source)}%", escape=_LIKE_ESCAPE_CHAR))
    if search:
        query = query.where(Log.message.ilike(f"%{_escape_like(search)}%", escape=_LIKE_ESCAPE_CHAR))
    if start_date:
        query = query.where(Log.timestamp >= start_date)
    if end_date:
        query = query.where(Log.timestamp <= end_date)

    # --- Logtyp-Filter ---
    if min_severity:
        query = query.where(_severity_condition(min_severity))
    if category:
        query = query.where(_category_condition(category))
    if facility is not None:
        query = query.where(Log.facility == facility)
    if device_type:
        # Ueber die Agent-Tabelle (wenige Zeilen) statt Join ueber die Log-Tabelle.
        query = query.where(
            Log.agent_id.in_(select(Agent.id).where(Agent.device_type == device_type.strip()))
        )
    return query


@router.get("", response_model=LogListResponse)
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    hostname: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    hostname_exact: bool = Query(False, description="Hostname exakt statt als Teilstring vergleichen"),
    min_severity: Optional[str] = Query(None, description="Schweregrad-Gruppe: dieses Level und alles Dringendere"),
    category: Optional[str] = Query(None, description="Logtyp-Kategorie, siehe /api/logs/filter-options"),
    facility: Optional[int] = Query(None, ge=0, le=23, description="Syslog-Facility (0-23)"),
    device_type: Optional[str] = Query(None, description="Geräteart, z.B. linux_agent, windows_agent, syslog"),
    readable: bool = Query(False, description="Zeilen zusätzlich lesbar aufbereitet zurückgeben"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user)
):
    logger.info(
        f"Log-Filter: hostname={hostname}, level={level}, source={source}, search={search}, "
        f"min_severity={min_severity}, category={category}, facility={facility}, "
        f"device_type={device_type}, page={page}"
    )

    filter_args = dict(
        hostname=hostname, level=level, source=source, search=search,
        start_date=start_date, end_date=end_date, hostname_exact=hostname_exact,
        min_severity=min_severity, category=category, facility=facility, device_type=device_type,
    )

    # Nur wenn wirklich lesbar gewuenscht ist, kommen die dicken Spalten mit.
    use_readable = readable and page_size <= MAX_READABLE_ROWS
    columns = READABLE_COLUMNS if use_readable else LIST_COLUMNS

    query = _apply_filters(select(*columns), **filter_args)
    has_filters = any([
        hostname, level, source, search, start_date, end_date,
        min_severity, category, facility is not None, device_type,
    ])

    # Bei Filtern exakten Count, ohne Filter Schätzung aus pg_class
    if has_filters:
        count_query = _apply_filters(select(func.count(Log.id)), **filter_args)
        total = (await db.execute(count_query)).scalar() or 0
    else:
        total = (await db.execute(text(
            "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
        ))).scalar() or 0

    logger.info(f"Log-Filter Ergebnis: {total} Treffer")

    offset = (page - 1) * page_size
    result = await db.execute(query.order_by(desc(Log.timestamp)).offset(offset).limit(page_size))

    rows = result.mappings().all()
    items = _readable_items(rows) if use_readable else [dict(row) for row in rows]

    return LogListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/filter-options")
async def get_filter_options(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Gibt die eindeutigen Werte für Hostname, Source und Level zurück (für Dropdown-Filter).
    Nutzt agents-Tabelle für Hostnames (wenige Zeilen statt 8M+ Logs zu scannen)."""
    # Hostnames aus agents-Tabelle (7 Zeilen statt DISTINCT über 8M Logs)
    hostnames = (await db.execute(
        select(Agent.hostname).where(Agent.hostname.isnot(None)).distinct().order_by(Agent.hostname)
    )).scalars().all()
    # Source/Level: DISTINCT nur über indizierte Spalten, schnell genug
    sources = (await db.execute(
        select(Log.source).where(Log.source.isnot(None)).distinct().order_by(Log.source)
    )).scalars().all()
    levels = (await db.execute(
        select(Log.level).where(Log.level.isnot(None)).distinct().order_by(Log.level)
    )).scalars().all()
    # Geraetearten aus der agents-Tabelle (wenige Zeilen)
    device_types = (await db.execute(
        select(Agent.device_type).where(Agent.device_type.isnot(None)).distinct().order_by(Agent.device_type)
    )).scalars().all()

    return {
        "hostnames": hostnames,
        "sources": sources,
        "levels": levels,
        "device_types": device_types,
        # Logtypen: feste Listen, damit kein DISTINCT ueber die grosse logs-Tabelle noetig ist
        "severities": [{"key": g["key"], "label": g["label"]} for g in SEVERITY_GROUPS],
        "categories": [{"key": key, "label": entry["label"]} for key, entry in LOG_CATEGORIES.items()],
        "facilities": [{"value": number, "label": f"{name} ({number})"} for number, name in sorted(FACILITY_NAMES.items())],
    }


@router.get("/recent", response_model=List[LogResponse])
async def get_recent_logs(limit: int = Query(10, ge=1, le=100),
                          readable: bool = Query(False, description="Lesbar aufbereitet"),
                          db: AsyncSession = Depends(get_db),
                          _=Depends(get_current_user), response: Response = None):
    columns = READABLE_COLUMNS if readable else LIST_COLUMNS
    query = select(*columns).order_by(desc(Log.timestamp)).limit(limit)
    result = await db.execute(query)
    rows = result.mappings().all()
    items = _readable_items(rows) if readable else [dict(row) for row in rows]
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=15"
    return items


@router.get("/stats", response_model=LogStatsResponse)
async def get_log_stats(db: AsyncSession = Depends(get_db), _=Depends(get_current_user), response: Response = None):
    """Schnelle Dashboard-Stats mit kurzem Cache, um DB-Last zu senken."""
    global _stats_cache, _stats_cache_expires

    now = datetime.utcnow()
    if _stats_cache and now < _stats_cache_expires:
        return _stats_cache

    async with _stats_cache_lock:
        now = datetime.utcnow()
        if _stats_cache and now < _stats_cache_expires:
            return _stats_cache

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # Nur Logs ab heute zählen für Level/Source Statistiken (nutzt timestamp-Index)
        today_count = (await db.execute(select(func.count(Log.id)).where(Log.timestamp >= today))).scalar() or 0
        by_level = dict((await db.execute(select(Log.level, func.count(Log.id)).where(Log.timestamp >= today).group_by(Log.level))).all())
        by_source = dict((await db.execute(select(Log.source, func.count(Log.id)).where(Log.timestamp >= today).group_by(Log.source).order_by(desc(func.count(Log.id))).limit(10))).all())

        # Gesamtzahl aus pg_class Statistik (sofort, kein Full-Scan über 8M+ Zeilen)
        total = (await db.execute(text(
            "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
        ))).scalar() or 0

        # Unique Hosts aus agents-Tabelle (7 Zeilen statt 41k+ Logs scannen)
        unique = (await db.execute(select(func.count(Agent.id)))).scalar() or 0

        stats = LogStatsResponse(
            total_logs=total,
            logs_today=today_count,
            logs_by_level=by_level,
            logs_by_source=by_source,
            unique_hosts=unique
        )

        _stats_cache = stats
        _stats_cache_expires = datetime.utcnow() + timedelta(seconds=_stats_cache_ttl)
        if response is not None:
            response.headers["Cache-Control"] = f"public, max-age={_stats_cache_ttl}"
        return stats


# ==============================================================================
# Export (CSV / JSON)
# ==============================================================================

EXPORT_COLUMNS = ("id", "timestamp", "hostname", "ip_address", "level", "source", "message")

# Werte, die mit diesen Zeichen beginnen, interpretiert Excel/Calc als Formel.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_value(value):
    """Datum lesbar machen und Formel-Injection in Tabellenkalkulationen verhindern."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    text_value = str(value)
    if text_value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text_value
    return text_value


async def _stream_export_rows(query):
    """Treffer zeilenweise liefern.

    Bewusst eine EIGENE Session: die per Depends injizierte Session ist beim
    Abarbeiten einer StreamingResponse bereits geschlossen (FastAPI >= 0.106).
    """
    async with async_session() as session:
        result = await session.stream(query.execution_options(yield_per=1000))
        async for row in result.mappings():
            yield row


async def _csv_generator(query):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    # BOM (U+FEFF), damit Excel UTF-8 (Umlaute) korrekt erkennt
    yield "﻿" + buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    async for row in _stream_export_rows(query):
        writer.writerow([_csv_value(row[column]) for column in EXPORT_COLUMNS])
        if buffer.tell() > 32768:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    if buffer.tell():
        yield buffer.getvalue()


async def _json_generator(query):
    yield "["
    first = True
    async for row in _stream_export_rows(query):
        item = {}
        for column in EXPORT_COLUMNS:
            value = row[column]
            item[column] = value.isoformat() if isinstance(value, datetime) else value
        yield ("" if first else ",") + json.dumps(item, ensure_ascii=False)
        first = False
    yield "]"


@router.get("/export")
async def export_logs(
    format: str = Query("csv", pattern="^(csv|json)$"),
    hostname: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    hostname_exact: bool = Query(False),
    min_severity: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    facility: Optional[int] = Query(None, ge=0, le=23),
    device_type: Optional[str] = Query(None),
    limit: int = Query(50000, ge=1, le=500000, description="Maximale Zeilenzahl im Export"),
    _=Depends(get_current_user),
):
    """Exportiert die aktuell gefilterten Logs als CSV oder JSON (gestreamt)."""
    query = _apply_filters(
        select(*LIST_COLUMNS),
        hostname=hostname, level=level, source=source, search=search,
        start_date=start_date, end_date=end_date, hostname_exact=hostname_exact,
        min_severity=min_severity, category=category, facility=facility, device_type=device_type,
    ).order_by(desc(Log.timestamp)).limit(limit)

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    if format == "json":
        return StreamingResponse(
            _json_generator(query),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="rimperio-logs-{stamp}.json"'},
        )

    return StreamingResponse(
        _csv_generator(query),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="rimperio-logs-{stamp}.csv"'},
    )


@router.get("/{log_id}/parsed")
async def get_log_parsed(log_id: int, db: AsyncSession = Depends(get_db),
                         _=Depends(get_current_user)):
    """Dieselbe Zeile, aber zerlegt: Zusammenfassung, Felder, Abzeichen.

    Die Rohzeile kommt unveraendert mit - wenn die Erkennung danebenliegt, muss
    man nachsehen koennen, was wirklich ankam. Der Parser fasst die Datenbank
    nicht an, er stellt nur anders dar.
    """
    result = await db.execute(select(Log).where(Log.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log nicht gefunden")

    return {
        "id": log.id,
        "timestamp": log.timestamp,
        "hostname": log.hostname,
        "ip_address": log.ip_address,
        "level": log.level,
        "source": log.source,
        "message": log.message,
        "raw_message": log.raw_message,
        "extra_data": log.extra_data or {},
        "parsed": logparse.parse_log_row(log),
    }


@router.get("/{log_id}", response_model=LogDetailResponse)
async def get_log(log_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Log).where(Log.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Log nicht gefunden")
    return log
