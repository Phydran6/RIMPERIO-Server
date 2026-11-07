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
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import logparse, public_url
from ..auth import get_current_admin, get_current_user
from ..branding import load_config as load_branding
from ..config import settings
from ..database import get_db
from ..models import Agent, Log, Setting

logger = logging.getLogger("rimperio.mobile")

router = APIRouter(prefix="/api/app", tags=["Mobile App"])

# Was die App an Funktionen erwarten darf. Aeltere App-Versionen koennen daran
# ablesen, was dieser Server kann, statt es auszuprobieren.
API_LEVEL = 3
CAPABILITIES = [
    "notifications",     # /api/app/notifications - Eingang (Freigaben, Fehlschlaege)
    "patch-approvals",   # Updates freigeben/ablehnen inkl. KI-Einschaetzung
    "patch-runs",        # Laeufe ansehen, zurueckrollen
    "patch-install",     # sofort installieren
    "docker-updates",    # Container-Updates wie Watchtower
    "snmp-status",       # SNMP-Geraete und Probleme
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
            "site_url": public_url.site_url() or None,
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


# =============================================================================
# Eingang, Patchmanagement, SNMP (API-Level 3)
# =============================================================================
# Bewusst schmal: nur, was auf ein Handy gehoert - freigeben, ablehnen, sehen,
# was laeuft, und im Notfall zurueckrollen. Eingerichtet wird im Browser.

class AppDecisionIn(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)
    decision: str = Field(pattern="^(approve|reject|defer)$")
    defer_days: int = Field(default=7, ge=1, le=90)


class AppInstallIn(BaseModel):
    agent_uid: str
    item_ids: List[int] = Field(default_factory=list, max_length=500)
    snapshot: Optional[bool] = None


class AppReadIn(BaseModel):
    ids: List[int] = Field(default_factory=list, max_length=500)


def _app_item(it, agent) -> dict:
    ai = it.ai or {}
    return {
        "id": it.id, "host": agent.hostname if agent else None,
        "agent_uid": agent.agent_uid if agent else None, "kind": it.kind,
        "platform": it.platform, "title": it.title, "kb": it.kb or None,
        "from": it.version_from or None, "to": it.version_to or None,
        "security": it.security, "urgent": it.urgent, "reboot": it.reboot,
        "status": it.status, "reason": it.reason,
        "ai": {"risk": ai.get("risk"), "recommendation": ai.get("recommendation"),
               "summary": ai.get("summary"), "known_issues": ai.get("known_issues") or [],
               "error": ai.get("error")} if ai else None,
        "advisories": [{"title": a.get("title"), "link": a.get("link"), "source": a.get("source")}
                       for a in (it.advisories or [])[:4]],
    }


@router.get("/notifications")
async def app_notifications(since_id: Optional[int] = Query(None), unread: bool = Query(False),
                            limit: int = Query(50, ge=1, le=200),
                            db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Neue Meldungen seit `since_id` - die App fragt in Ruhe nach."""
    from ..models import Notification
    from .. import notify
    q = select(Notification)
    if since_id:
        q = q.where(Notification.id > since_id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    rows = (await db.execute(q.order_by(desc(Notification.id)).limit(limit))).scalars().all()
    unread_count = (await db.execute(select(func.count()).select_from(Notification)
                                     .where(Notification.read_at.is_(None)))).scalar_one()
    return {"items": [notify.public(n) for n in rows], "unread": unread_count,
            "newest_id": rows[0].id if rows else since_id}


@router.post("/notifications/read")
async def app_notifications_read(data: AppReadIn, db: AsyncSession = Depends(get_db),
                                 _=Depends(get_current_user)):
    from .. import notify
    return {"marked": await notify.mark_read(db, data.ids or None)}


@router.get("/patching/summary")
async def app_patch_summary(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    from ..models import PatchItem, PatchRun
    from .. import patchmgmt as pm
    counts = dict((await db.execute(select(PatchItem.status, func.count())
                                    .group_by(PatchItem.status))).all())
    running = (await db.execute(select(func.count()).select_from(PatchRun)
                                .where(PatchRun.status.in_(pm.ACTIVE_RUN)))).scalar_one()
    failed = (await db.execute(select(func.count()).select_from(PatchRun).where(
        PatchRun.status == "failed",
        PatchRun.finished_at >= datetime.utcnow() - timedelta(days=7)))).scalar_one()
    return {"needs_approval": counts.get("needs_approval", 0), "automatic": counts.get("auto", 0),
            "approved": counts.get("approved", 0), "reviewing": counts.get("review", 0),
            "failed_items": counts.get("failed", 0), "runs_active": running,
            "runs_failed_7d": failed}


@router.get("/patching/approvals")
async def app_patch_approvals(limit: int = Query(100, ge=1, le=500),
                              db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Was wartet auf eine Entscheidung - dringende zuerst."""
    from ..models import PatchItem
    rows = (await db.execute(select(PatchItem, Agent).join(Agent, Agent.id == PatchItem.agent_id)
                             .where(PatchItem.status == "needs_approval")
                             .order_by(desc(PatchItem.urgent), desc(PatchItem.security), Agent.hostname)
                             .limit(limit))).all()
    return {"items": [_app_item(i, a) for i, a in rows]}


@router.post("/patching/decide")
async def app_patch_decide(data: AppDecisionIn, db: AsyncSession = Depends(get_db),
                           admin=Depends(get_current_admin)):
    from .. import patchmgmt as pm
    try:
        return {"changed": await pm.decide(db, data.ids, data.decision, f"{admin.username} (App)",
                                           data.defer_days)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/patching/install")
async def app_patch_install(data: AppInstallIn, db: AsyncSession = Depends(get_db),
                            admin=Depends(get_current_admin)):
    from .. import patchmgmt as pm
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == data.agent_uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    try:
        run = await pm.install_now(db, agent, data.item_ids, f"{admin.username} (App)", data.snapshot, "app")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pm.public_run(run, agent)


@router.get("/patching/runs")
async def app_patch_runs(limit: int = Query(30, ge=1, le=200),
                         db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    from ..models import PatchRun
    rows = (await db.execute(select(PatchRun, Agent).join(Agent, Agent.id == PatchRun.agent_id)
                             .order_by(desc(PatchRun.id)).limit(limit))).all()
    out = []
    for r, a in rows:
        snap = r.snapshot or {}
        out.append({"id": r.id, "host": a.hostname, "status": r.status, "trigger": r.trigger,
                    "items": len(r.items or []),
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "last": (r.log or [{}])[-1].get("text"),
                    "snapshot": snap.get("name") or snap.get("volid"),
                    "snapshot_delete_after": snap.get("delete_after"),
                    "can_rollback": bool(snap.get("name")) and snap.get("mode") == "snapshot"
                    and not snap.get("deleted_at") and r.status in ("failed", "succeeded")})
    return {"items": out}


@router.post("/patching/runs/{run_id}/rollback")
async def app_patch_rollback(run_id: int, db: AsyncSession = Depends(get_db),
                             admin=Depends(get_current_admin)):
    from .. import patchmgmt as pm, pve
    try:
        run = await pm.rollback(db, run_id, f"{admin.username} (App)")
    except (ValueError, pve.PveError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": run.id, "status": run.status}


@router.get("/snmp")
async def app_snmp(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    from ..models import SnmpDevice, SnmpMetric
    devices = (await db.execute(select(SnmpDevice).order_by(SnmpDevice.name))).scalars().all()
    problems: dict = {}
    for m in (await db.execute(select(SnmpMetric).where(
            SnmpMetric.status.in_(("warning", "critical"))))).scalars():
        problems.setdefault(m.device_id, []).append(
            {"label": m.label, "value": m.value, "text": m.text, "unit": m.unit, "status": m.status})
    return {"items": [{"id": d.id, "name": d.name, "host": d.host, "site": d.site, "status": d.status,
                       "last_ok_at": d.last_ok_at.isoformat() if d.last_ok_at else None,
                       "problems": problems.get(d.id, [])[:5]} for d in devices]}
