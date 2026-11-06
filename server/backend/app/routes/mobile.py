# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Schlanke API fuer die Handy-App
# ==============================================================================
"""
Eine eigene Schnittstelle fuer die App unter `/api/app`.

Warum nicht einfach die vorhandene? Weil eine App andere Sorgen hat als ein
Browser im gleichen Netz:

* **Wenig Daten.** Ueber Mobilfunk zaehlt jedes Kilobyte. Die Antworten hier
  tragen nur, was auf dem Bildschirm landet - keine Rohzeilen, solange sie
  niemand aufklappt.
* **Blaettern per Cursor statt per Seite.** Bei `page=42` verschiebt sich alles,
  sobald waehrenddessen neue Logs eintreffen: man sieht Zeilen doppelt oder
  ueberspringt welche. Mit `before_id` passiert das nicht.
* **Nachlaufen statt neu laden.** `since_id` liefert nur, was seit dem letzten
  Blick dazugekommen ist.
* **Lesbare Logs.** Die Rohzeile ist auf einem Handy unbrauchbar. Deshalb kommt
  jede Zeile hier durch den Anzeige-Parser (`logparse`) und bringt eine
  Zusammenfassung und ein paar Abzeichen mit.
* **Ein Aufruf beim Start.** `/bootstrap` beantwortet auf einen Schlag, was die
  App zum Aufbauen der Oberflaeche braucht.

Die Anmeldung ist dieselbe wie ueberall: Bearer-Token aus `/api/auth/login`
bzw. dem QR-Code-Weg.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import logparse
from ..auth import get_current_user
from ..branding import load_config as load_branding
from ..config import settings
from ..database import get_db
from ..models import Agent, Log, Setting

logger = logging.getLogger("rimperio.mobile")

router = APIRouter(prefix="/api/app", tags=["Mobile App"])

# Was die App an Funktionen erwarten darf. Aeltere App-Versionen koennen daran
# ablesen, was dieser Server kann, statt es auszuprobieren.
API_LEVEL = 2
CAPABILITIES = [
    "cursor-paging",     # before_id / since_id statt Seitenzahlen
    "parsed-logs",       # Zusammenfassung und Abzeichen pro Zeile
    "log-tail",          # Nachlaufen ohne vollstaendiges Neuladen
    "device-summary",
    "severity-filter",
    "search",
    "ai-analysis",       # /api/ai/analyze, falls eingerichtet
]

# Nur diese Spalten holen. `raw_message` bleibt draussen, bis jemand eine
# einzelne Zeile wirklich aufklappt.
LIST_COLUMNS = (
    Log.id, Log.hostname, Log.ip_address, Log.timestamp,
    Log.level, Log.source, Log.message, Log.facility, Log.agent_id,
)

SEVERITY_RANK = {
    "emergency": 0, "emerg": 0, "panic": 0, "alert": 1, "critical": 2, "crit": 2,
    "error": 3, "err": 3, "warning": 4, "warn": 4, "notice": 5, "info": 6,
    "informational": 6, "debug": 7,
}


def _levels_up_to(max_rank: int) -> List[str]:
    return [name for name, rank in SEVERITY_RANK.items() if rank <= max_rank]


def _compact(row: dict, parse: bool) -> dict:
    """Eine Logzeile so klein wie moeglich - aber lesbar."""
    item = {
        "id": row["id"],
        "ts": row["timestamp"].isoformat() if row.get("timestamp") else None,
        "host": row.get("hostname"),
        "ip": row.get("ip_address"),
        "level": row.get("level"),
        "source": row.get("source"),
        "message": row.get("message"),
    }
    if not parse:
        return item

    parsed = logparse.parse(
        raw=row.get("message") or "",
        message=row.get("message") or "",
        level=row.get("level") or "",
        source=row.get("source") or "",
        facility=row.get("facility"),
    )
    # Nur mitschicken, was der Rohtext nicht schon hergibt - sonst wird die
    # Antwort groesser statt kleiner.
    if parsed["summary"] and parsed["summary"] != item["message"]:
        item["summary"] = parsed["summary"]
    if parsed["highlights"]:
        item["badges"] = parsed["highlights"]
    if parsed["meta"].get("app") and parsed["meta"]["app"] != item["source"]:
        item["app"] = parsed["meta"]["app"]
    return item


# =============================================================================
# Start
# =============================================================================
@router.get("/bootstrap")
async def bootstrap(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Alles, was die App beim Start braucht — in einem Aufruf."""
    # load_config liest eine kleine JSON-Datei und ist bewusst synchron.
    branding = load_branding()

    hostnames = (await db.execute(
        select(Agent.hostname).where(Agent.hostname.isnot(None))
        .distinct().order_by(Agent.hostname).limit(500)
    )).scalars().all()
    device_types = (await db.execute(
        select(Agent.device_type).where(Agent.device_type.isnot(None))
        .distinct().order_by(Agent.device_type)
    )).scalars().all()

    return {
        "server": {
            "name": branding.company_name or "RIMPERIO",
            "version": settings.app_version,
            "api_level": API_LEVEL,
            "capabilities": CAPABILITIES,
            "site_url": settings.site_url or None,
        },
        "user": {
            "username": user.username,
            "role": user.role,
            "is_admin": user.role == "admin",
            "mfa_enabled": bool(user.mfa_enabled),
        },
        "theme": {
            "primary_color": branding.primary_color,
            "default_theme": branding.default_theme,
            "allow_theme_toggle": branding.allow_theme_toggle,
            "logo_path": branding.logo_path,
        },
        "filters": {
            "hostnames": list(hostnames),
            "device_types": list(device_types),
            "levels": ["emergency", "alert", "critical", "error",
                       "warning", "notice", "info", "debug"],
        },
    }


# =============================================================================
# Logs
# =============================================================================
def _filtered(query, hostname, level, min_severity, source, search,
              device_type, since_hours):
    if hostname:
        query = query.where(func.lower(Log.hostname) == hostname.strip().lower())
    if level:
        query = query.where(func.lower(Log.level) == level.strip().lower())
    if min_severity:
        rank = SEVERITY_RANK.get(min_severity.strip().lower())
        if rank is None:
            raise HTTPException(status_code=400,
                                detail=f"Unbekannter Schweregrad '{min_severity}'.")
        query = query.where(func.lower(Log.level).in_(_levels_up_to(rank)))
    if source:
        query = query.where(Log.source.ilike(f"%{source.strip()}%"))
    if search:
        query = query.where(Log.message.ilike(f"%{search.strip()}%"))
    if device_type:
        query = query.where(Log.agent_id.in_(
            select(Agent.id).where(Agent.device_type == device_type.strip())))
    if since_hours:
        query = query.where(Log.timestamp >= datetime.utcnow() - timedelta(hours=since_hours))
    return query


@router.get("/logs")
async def logs(
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[int] = Query(None, description="Ältere Zeilen als diese ID (Blättern)"),
    hostname: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    min_severity: Optional[str] = Query(None, description="Dieses Level und alles Dringendere"),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None),
    since_hours: Optional[int] = Query(None, ge=1, le=8760),
    parse: bool = Query(True, description="Zusammenfassung und Abzeichen mitliefern"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Logzeilen, neueste zuerst, per Cursor blätterbar.

    Kein `total`: das Zählen über Millionen Zeilen kostet mehr als die Abfrage
    selbst, und in einer endlos scrollenden Liste sieht es ohnehin niemand.
    """
    query = _filtered(select(*LIST_COLUMNS), hostname, level, min_severity,
                      source, search, device_type, since_hours)
    if before_id:
        query = query.where(Log.id < before_id)

    rows = (await db.execute(
        query.order_by(desc(Log.id)).limit(limit + 1)
    )).mappings().all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_compact(dict(row), parse) for row in rows]

    return {
        "items": items,
        "has_more": has_more,
        # Damit blättert die App weiter, ohne selbst rechnen zu müssen.
        "next_before_id": items[-1]["id"] if items and has_more else None,
        "newest_id": items[0]["id"] if items else None,
    }


@router.get("/logs/tail")
async def tail(
    since_id: int = Query(..., description="Nur Zeilen neuer als diese ID"),
    limit: int = Query(100, ge=1, le=500),
    hostname: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    min_severity: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None),
    parse: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Was ist seit `since_id` dazugekommen?

    Der günstige Weg für eine offene Liste: die App merkt sich die höchste ID
    und fragt in Ruhe nach, statt alles neu zu laden.
    """
    query = _filtered(select(*LIST_COLUMNS), hostname, level, min_severity,
                      None, None, device_type, None).where(Log.id > since_id)
    rows = (await db.execute(query.order_by(desc(Log.id)).limit(limit))).mappings().all()

    items = [_compact(dict(row), parse) for row in rows]
    items.reverse()   # aelteste zuerst: die App haengt sie unten an

    return {
        "items": items,
        "newest_id": items[-1]["id"] if items else since_id,
        "count": len(items),
        "truncated": len(rows) == limit,
    }


@router.get("/logs/{log_id}")
async def log_detail(log_id: int, db: AsyncSession = Depends(get_db),
                     _=Depends(get_current_user)):
    """Eine einzelne Zeile — vollständig und zerlegt.

    Hier kommt auch die Rohzeile mit: wenn der Parser danebenliegt, muss man
    nachsehen können, was wirklich ankam.
    """
    log = (await db.execute(select(Log).where(Log.id == log_id))).scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Diese Logzeile gibt es nicht.")

    parsed = logparse.parse_log_row(log)
    return {
        "id": log.id,
        "ts": log.timestamp.isoformat() if log.timestamp else None,
        "host": log.hostname,
        "ip": log.ip_address,
        "level": log.level,
        "source": log.source,
        "message": log.message,
        "facility": log.facility,
        "agent_id": log.agent_id,
        "extra": log.extra_data or {},
        "parsed": parsed,
        "raw": log.raw_message,
    }


# =============================================================================
# Uebersicht und Geraete
# =============================================================================
@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Die Zahlen für den Startbildschirm der App."""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # Gesamtzahl aus der Schaetzung der Datenbank: ein echtes COUNT(*) ueber
    # Millionen Zeilen laesst den Startbildschirm sekundenlang haengen.
    from sqlalchemy import text
    total = (await db.execute(text(
        "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
    ))).scalar() or 0

    today_count = (await db.execute(
        select(func.count(Log.id)).where(Log.timestamp >= today)
    )).scalar() or 0

    by_level = dict((await db.execute(
        select(func.lower(Log.level), func.count(Log.id))
        .where(Log.timestamp >= today).group_by(func.lower(Log.level))
    )).all())

    offline_timeout = 300
    setting = (await db.execute(
        select(Setting).where(Setting.key == "agent_offline_timeout")
    )).scalar_one_or_none()
    if setting:
        try:
            offline_timeout = int(setting.value)
        except (TypeError, ValueError):
            pass
    cutoff = datetime.utcnow() - timedelta(seconds=offline_timeout)

    total_devices = (await db.execute(select(func.count(Agent.id)))).scalar() or 0
    online_devices = (await db.execute(
        select(func.count(Agent.id)).where(Agent.last_seen >= cutoff)
    )).scalar() or 0

    problems = sum(by_level.get(name, 0)
                   for name in ("emergency", "alert", "critical", "error"))

    return {
        "logs_total": int(total),
        "logs_today": today_count,
        "by_level_today": by_level,
        "problems_today": problems,
        "devices": {"total": total_devices, "online": online_devices,
                    "offline": total_devices - online_devices},
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.get("/devices")
async def devices(
    limit: int = Query(100, ge=1, le=500),
    only_offline: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Kompakte Geräteliste mit Online-Zustand."""
    offline_timeout = 300
    setting = (await db.execute(
        select(Setting).where(Setting.key == "agent_offline_timeout")
    )).scalar_one_or_none()
    if setting:
        try:
            offline_timeout = int(setting.value)
        except (TypeError, ValueError):
            pass
    cutoff = datetime.utcnow() - timedelta(seconds=offline_timeout)

    query = select(Agent).order_by(desc(Agent.last_seen)).limit(limit)
    if only_offline:
        query = query.where((Agent.last_seen < cutoff) | (Agent.last_seen.is_(None)))

    items = []
    for agent in (await db.execute(query)).scalars().all():
        items.append({
            "id": agent.id,
            "host": agent.hostname,
            "ip": agent.ip_address,
            "type": agent.device_type,
            "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
            "online": bool(agent.last_seen and agent.last_seen >= cutoff),
        })
    return {"items": items, "offline_after_seconds": offline_timeout}
