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
API_LEVEL = 4
CAPABILITIES = [
    "notifications",     # /api/app/notifications - Eingang (Freigaben, Fehlschlaege)
    "patch-approvals",   # Updates freigeben/ablehnen inkl. KI-Einschaetzung
    "patch-runs",        # Laeufe ansehen, zurueckrollen
    "patch-install",     # sofort installieren
    "patch-installable", # freigegebene/automatische Updates je Rechner/Container installieren
    "docker-updates",    # Container-Updates wie Watchtower
    "snmp-status",       # SNMP-Geraete und Probleme
    "cursor-paging",     # before_id / since_id statt Seitenzahlen
    "parsed-logs",       # Zusammenfassung und Abzeichen pro Zeile
    "log-explain",       # Klartext, Kategorie und Hinweis je Zeile (logexplain)
    "log-handling",      # /api/log-handling: Rauschen, Sperrregeln, Aufbewahrung
    "log-tail",          # Nachlaufen ohne vollstaendiges Neuladen
    "device-summary",
    "severity-filter",
    "search",
    "device-push",       # Geraeteschluessel fuer die Hintergrundabfrage (laeuft nicht nach 24 h ab)
    "server-update",     # RIMPERIO selbst aktualisieren
    "pve-updates",       # /api/pve/updates - Proxmox-Knoten aktualisieren
    "pve-console",       # /api/pve/console/ws - Konsole Knoten/LXC/VM
    "remote-console",    # Befehle auf Rechnern (Linux als rimperio, Windows PowerShell)
    "rdp",               # RDP-Verbindung zu Windows-Rechnern
    "db-updates",        # /api/db-updates - Datenbank-Updates mit Probelauf
    "ai-analysis",       # /api/ai/analyze, falls eingerichtet
]

# Nur diese Spalten holen. `raw_message` bleibt draussen, bis jemand eine
# einzelne Zeile wirklich aufklappt.
LIST_COLUMNS = (
    Log.id, Log.hostname, Log.ip_address, Log.timestamp,
    Log.level, Log.source, Log.message, Log.facility, Log.agent_id,
    Log.extra_data["repeat"].as_integer().label("repeat"),
)

# Stufen, die in der Ansicht der App nicht vorkommen - nur ueber die Suche.
LOW_LEVELS = ("debug", "info", "informational", "notice")

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
    if row.get("repeat") and int(row.get("repeat") or 1) > 1:
        item["repeat"] = int(row["repeat"])
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
    if parsed.get("category") and parsed["category"] != "other":
        item["category"] = parsed["category"]
        item["category_label"] = parsed.get("category_label")
    if (parsed.get("explain") or {}).get("hint"):
        item["has_hint"] = True
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
              device_type, since_hours, exclude_low=False, levels=None):
    if exclude_low:
        query = query.where(func.lower(Log.level).notin_(LOW_LEVELS))
    if levels:
        wanted = [x.strip().lower() for x in levels.split(",") if x.strip()]
        names = set()
        for w in wanted:
            names.update(n for n, r in SEVERITY_RANK.items() if r == SEVERITY_RANK.get(w, -1))
        if names:
            query = query.where(func.lower(Log.level).in_(sorted(names)))
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
    exclude_low: bool = Query(False, description="Debug/Info/Hinweis weglassen (Ansicht der App)"),
    levels: Optional[str] = Query(None, description="Kommagetrennte Stufen, z. B. info,notice (Suche)"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Logzeilen, neueste zuerst, per Cursor blätterbar.

    Kein `total`: das Zählen über Millionen Zeilen kostet mehr als die Abfrage
    selbst, und in einer endlos scrollenden Liste sieht es ohnehin niemand.
    """
    query = _filtered(select(*LIST_COLUMNS), hostname, level, min_severity,
                      source, search, device_type, since_hours, exclude_low, levels)
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
    exclude_low: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Was ist seit `since_id` dazugekommen?

    Der günstige Weg für eine offene Liste: die App merkt sich die höchste ID
    und fragt in Ruhe nach, statt alles neu zu laden.
    """
    query = _filtered(select(*LIST_COLUMNS), hostname, level, min_severity,
                      None, None, device_type, None, exclude_low).where(Log.id > since_id)
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
        rmm_online = bool(agent.rmm_last_seen and agent.rmm_last_seen >= cutoff)
        items.append({
            "id": agent.id,
            "host": agent.hostname,
            "ip": agent.ip_address,
            "type": agent.device_type,
            "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
            "online": bool(agent.last_seen and agent.last_seen >= cutoff) or rmm_online,
            # Fernwartung: nur bei Rechnern mit RIMPERIO-Agent
            "agent_uid": agent.agent_uid,
            "platform": agent.platform,
            "os": agent.os,
            "rmm_online": rmm_online if agent.agent_uid else None,
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
    if run is None:
        return {"id": None, "status": "db_update",
                "message": "Datenbank-Container: DB-Update mit Sicherung und Probelauf gestartet (Mehr → DB-Updates)."}
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


# =============================================================================
# Geraeteschluessel fuer die Hintergrundabfrage (API-Level 4)
# =============================================================================
# Der Sitzungs-Token laeuft nach 24 h ab. Damit die Benachrichtigungen danach
# nicht verstummen, meldet sich das Handy einmal mit Sitzung an und bekommt
# einen Schluessel, der ausschliesslich neue Meldungen abholen darf.
import hashlib as _hashlib
import secrets as _secrets

from fastapi import Header, Request

from ..limiter import client_ip as _client_ip, limiter as _limiter


class DeviceIn(BaseModel):
    name: str = Field(default="", max_length=120)
    platform: str = Field(default="", max_length=16)


def _hash(key: str) -> str:
    return _hashlib.sha256(key.encode()).hexdigest()


@router.post("/devices/register", status_code=201)
async def register_device(data: DeviceIn, request: Request, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    from ..models import AppDevice
    from .. import journal
    key = "rimdev_" + _secrets.token_urlsafe(32)
    dev = AppDevice(user_id=user.id, name=data.name.strip()[:120] or "Handy", platform=data.platform[:16],
                    key_hash=_hash(key), last_ip=_client_ip(request))
    db.add(dev)
    await db.commit()
    await db.refresh(dev)
    journal.fire(category="auth", event="app.device.registered", actor=user.username, target=dev.name,
                 message=f"App auf „{dev.name}“ für Benachrichtigungen angemeldet.")
    return {"device_id": dev.id, "device_key": key}


@router.get("/devices")
async def my_devices(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from ..models import AppDevice
    q = select(AppDevice).where(AppDevice.revoked_at.is_(None))
    if user.role != "admin":
        q = q.where(AppDevice.user_id == user.id)
    rows = (await db.execute(q.order_by(desc(AppDevice.id)))).scalars().all()
    return {"items": [{"id": d.id, "name": d.name, "platform": d.platform, "user_id": d.user_id,
                       "created_at": d.created_at.isoformat() + "Z" if d.created_at else None,
                       "last_seen_at": d.last_seen_at.isoformat() + "Z" if d.last_seen_at else None}
                      for d in rows]}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from ..models import AppDevice
    dev = await db.get(AppDevice, device_id)
    if dev is None or (dev.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Unbekanntes Gerät.")
    dev.revoked_at = datetime.utcnow()
    await db.commit()
    return {"revoked": True}


@router.get("/push")
@_limiter.limit("120/minute")
async def push_poll(request: Request, since_id: Optional[int] = Query(None),
                    limit: int = Query(20, ge=1, le=100),
                    x_device_key: str = Header(..., alias="X-Device-Key"),
                    db: AsyncSession = Depends(get_db)):
    """Neue Meldungen fuer die Hintergrundabfrage - nur mit Geraeteschluessel."""
    from ..models import AppDevice, Notification, User
    from .. import notify
    dev = (await db.execute(select(AppDevice).where(AppDevice.key_hash == _hash(x_device_key or ""),
                                                    AppDevice.revoked_at.is_(None)))).scalar_one_or_none()
    if dev is None:
        raise HTTPException(status_code=401, detail="Gerät unbekannt oder abgemeldet.")
    owner = await db.get(User, dev.user_id)
    if owner is None or not owner.is_active:
        raise HTTPException(status_code=401, detail="Das Konto zu diesem Gerät ist gesperrt.")
    now = datetime.utcnow()
    if not dev.last_seen_at or (now - dev.last_seen_at).total_seconds() > 60:
        dev.last_seen_at, dev.last_ip = now, _client_ip(request)
        await db.commit()
    q = select(Notification)
    if since_id:
        q = q.where(Notification.id > since_id)
    rows = (await db.execute(q.order_by(desc(Notification.id)).limit(limit))).scalars().all()
    unread = (await db.execute(select(func.count()).select_from(Notification)
                               .where(Notification.read_at.is_(None)))).scalar_one()
    return {"items": [{k: v for k, v in notify.public(n).items() if k != "data"} for n in rows],
            "unread": unread, "newest_id": rows[0].id if rows else since_id}


# =============================================================================
# Updates: alles Offene, Installierbares je Rechner, Suche, Lauf im Detail
# =============================================================================
class AppScanIn(BaseModel):
    agent_uid: Optional[str] = None


class AppInstallAllIn(BaseModel):
    agent_uids: List[str] = Field(default_factory=list, max_length=500)
    snapshot: Optional[bool] = None


async def _offline_after(db: AsyncSession) -> int:
    setting = (await db.execute(select(Setting).where(Setting.key == "agent_offline_timeout"))).scalar_one_or_none()
    try:
        return int(setting.value) if setting else 300
    except (TypeError, ValueError):
        return 300


@router.get("/patching/open")
async def app_patch_open(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Alle offenen Updates je Rechner - auch 'wird geprueft' und 'automatisch',
    damit nichts unsichtbar bleibt."""
    from ..models import PatchItem, PatchRun, PveLink
    from .. import patchmgmt as pm, dbmigrate
    from ..rmm import is_online
    offline_after = await _offline_after(db)
    rows = (await db.execute(select(PatchItem, Agent).join(Agent, Agent.id == PatchItem.agent_id)
                             .where(PatchItem.status.in_(("review", "auto", "needs_approval", "approved",
                                                          "installing", "failed")))
                             .order_by(Agent.hostname, desc(PatchItem.security), PatchItem.title))).all()
    active = {r.agent_id: r for r in (await db.execute(select(PatchRun).where(
        PatchRun.status.in_(pm.ACTIVE_RUN)))).scalars()}
    linked = {l.agent_id for l in (await db.execute(select(PveLink))).scalars()}
    hosts: dict = {}
    for it, a in rows:
        h = hosts.setdefault(a.agent_uid, {
            "agent_uid": a.agent_uid, "host": a.hostname, "platform": a.platform, "os": a.os,
            "online": is_online(a, offline_after), "snapshot_possible": a.id in linked,
            "active_run": ({"id": active[a.id].id, "status": active[a.id].status}
                           if a.id in active else None),
            "counts": {}, "items": []})
        h["counts"][it.status] = h["counts"].get(it.status, 0) + 1
        item = _app_item(it, a)
        item["status_label"] = pm.STATUS_LABEL.get(it.status, it.status)
        item["installable"] = it.status in pm.INSTALLABLE
        item["db"] = it.kind == "docker" and dbmigrate.is_db_image((it.data or {}).get("image", ""))
        item["not_before"] = it.not_before.isoformat() + "Z" if it.not_before else None
        h["items"].append(item)
    out = list(hosts.values())
    for h in out:
        h["installable"] = sum(1 for i in h["items"] if i["installable"])
    return {"hosts": out,
            "totals": {"hosts": len(out),
                       "installable": sum(h["installable"] for h in out),
                       "needs_approval": sum(h["counts"].get("needs_approval", 0) for h in out),
                       "reviewing": sum(h["counts"].get("review", 0) for h in out)}}


@router.post("/patching/install-all", status_code=201)
async def app_patch_install_all(data: AppInstallAllIn, db: AsyncSession = Depends(get_db),
                                admin=Depends(get_current_admin)):
    """Freigegebene und automatische Updates jetzt installieren - je Rechner ein
    Lauf mit allem, was dazugehoert (Snapshot, Vorher/Nachher, Neustart, Pruefung)."""
    from ..models import PatchItem
    from .. import patchmgmt as pm
    q = select(Agent).where(Agent.agent_uid.is_not(None))
    if data.agent_uids:
        q = q.where(Agent.agent_uid.in_(data.agent_uids))
    started, skipped = [], []
    for agent in (await db.execute(q)).scalars().all():
        has = (await db.execute(select(PatchItem.id).where(
            PatchItem.agent_id == agent.id, PatchItem.status.in_(pm.INSTALLABLE)))).first()
        if not has:
            continue
        try:
            run = await pm.install_now(db, agent, [], f"{admin.username} (App)", data.snapshot, "app")
            started.append({"host": agent.hostname, "run_id": run.id if run else None,
                            "db_update": run is None})
        except ValueError as exc:
            await db.rollback()
            skipped.append({"host": agent.hostname, "reason": str(exc)})
    return {"started": started, "skipped": skipped}


@router.post("/patching/scan", status_code=201)
async def app_patch_scan(data: AppScanIn, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    """Jetzt nach Updates suchen - ein Rechner oder alle."""
    from .. import patchmgmt as pm
    q = select(Agent).where(Agent.agent_uid.is_not(None))
    if data.agent_uid:
        q = q.where(Agent.agent_uid == data.agent_uid)
    n = 0
    for agent in (await db.execute(q)).scalars().all():
        _, settings = await pm.policy_for(db, agent)
        n += len(await pm.queue_scan(db, agent, settings, f"{admin.username} (App)"))
    await db.commit()
    return {"queued": n}


@router.get("/patching/runs/{run_id}")
async def app_patch_run(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    from ..models import PatchItem, PatchRun
    from .. import patchmgmt as pm
    run = await db.get(PatchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unbekannter Lauf.")
    agent = await db.get(Agent, run.agent_id)
    items = (await db.execute(select(PatchItem).where(PatchItem.id.in_(run.items or [])))).scalars().all()
    out = pm.public_run(run, agent)
    out["item_list"] = [{"id": i.id, "title": i.title, "status": i.status,
                         "status_label": pm.STATUS_LABEL.get(i.status, i.status), "error": i.error} for i in items]
    return out


# =============================================================================
# RIMPERIO selbst aktualisieren
# =============================================================================
class AppServerUpdateIn(BaseModel):
    confirm: str
    backup: bool = True


@router.get("/server/update")
async def app_server_update_status(force: bool = Query(False), _=Depends(get_current_admin)):
    from .. import updater
    try:
        st = await updater.update_status(force=force)
    except Exception as exc:                                        # defensiv
        logger.warning("Update-Stand nicht lesbar: %s", exc)
        return {"installed": settings.app_version, "available": None, "update_available": False,
                "is_downgrade": False, "reason": f"Stand nicht lesbar: {exc}", "host_access": False,
                "run": None, "relation": "unknown", "unreleased": None, "target_ref": None,
                "oneliner": None, "notes": ""}
    local, remote = st.get("local") or {}, st.get("remote") or {}
    return {"installed": local.get("version"), "available": remote.get("version"),
            "update_available": bool(st.get("update_available")),
            "is_downgrade": bool(st.get("is_downgrade")), "reason": st.get("reason"),
            "host_access": bool(local.get("host_access")), "run": st.get("run"),
            "relation": st.get("relation"), "unreleased": st.get("unreleased"),
            "target_ref": st.get("target_ref"), "oneliner": st.get("oneliner"),
            "notes": (remote.get("notes") or remote.get("body") or "")[:4000] if isinstance(remote, dict) else ""}


@router.post("/server/update", status_code=202)
async def app_server_update(data: AppServerUpdateIn, request: Request, db: AsyncSession = Depends(get_db),
                            admin=Depends(get_current_admin)):
    """Wie RIMPERIO-Server → Updates in der Oberflaeche: erst sichern, dann
    einspielen. Die App ist waehrend des Updates kurz ohne Verbindung."""
    from .updates import UpdateRequest, apply_update
    from ..guard import BackupDecision
    req = UpdateRequest(confirm=data.confirm, database_backup=True,
                        backup=BackupDecision(create=bool(data.backup), note="Vor Update aus der App"))
    return await apply_update(req, request, db, admin)


@router.get("/server/update/log")
async def app_server_update_log(lines: int = Query(80, ge=10, le=500), _=Depends(get_current_admin)):
    from .. import updater
    return {"lines": await updater.read_run_log(lines=lines)}


# =============================================================================
# Fernzugriff: Befehle (Linux als Dienstkonto rimperio, Windows PowerShell), RDP
# =============================================================================
class AppExecIn(BaseModel):
    command: str = Field(..., min_length=1, max_length=8000)
    cwd: str = Field(default="", max_length=1000)
    timeout: int = Field(default=120, ge=5, le=3600)


_CWD_MARK = "__RIMPERIO_CWD__"


def _linux_wrap(command: str, cwd: str) -> str:
    """Laeuft als Dienstkonto rimperio - auch wenn der Agent (alte Installation)
    als root laeuft. Das Arbeitsverzeichnis wandert zwischen den Befehlen mit."""
    def q(v: str) -> str:
        return "'" + v.replace("'", "'\\''") + "'"
    inner = (f"cd {q(cwd)} 2>/dev/null || cd ~ 2>/dev/null; {command}\n"
             f"__rc=$?; printf '\\n{_CWD_MARK}%s\\n' \"$(pwd)\"; exit $__rc")
    return (f"if [ \"$(id -u)\" = 0 ] && id rimperio >/dev/null 2>&1; then "
            f"exec su -s /bin/sh rimperio -c {q(inner)}; else sh -c {q(inner)}; fi")


def _windows_wrap(command: str, cwd: str) -> str:
    esc = cwd.replace("'", "''")
    return ((f"Set-Location -LiteralPath '{esc}' -ErrorAction SilentlyContinue; " if cwd else "")
            + f"{command}\n\"`n{_CWD_MARK}$((Get-Location).Path)\"")


@router.post("/remote/{uid}/exec", status_code=201)
async def app_remote_exec(uid: str, data: AppExecIn, db: AsyncSession = Depends(get_db),
                          admin=Depends(get_current_admin)):
    from ..models import Command
    from .. import agentwake, journal
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    windows = (agent.platform or "").lower().startswith("win")
    cmd = Command(agent_id=agent.id, shell="powershell" if windows else "sh",
                  command=_windows_wrap(data.command, data.cwd) if windows else _linux_wrap(data.command, data.cwd),
                  timeout=data.timeout, kind="shell", source=f"App-Konsole: {data.command[:100]}",
                  created_by=f"{admin.username} (App)")
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    agentwake.notify(agent.id)
    journal.fire(category="shell", event="app.remote.exec", actor=admin.username, target=agent.hostname,
                 message=f"{admin.username} (App) → {agent.hostname}: {data.command[:200]}")
    return {"id": cmd.id, "status": cmd.status}


@router.get("/remote/commands/{cmd_id}")
async def app_remote_result(cmd_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    from ..models import Command
    cmd = await db.get(Command, cmd_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail="Unbekannter Befehl.")
    out, cwd = cmd.stdout or "", None
    if _CWD_MARK in out:
        head, _, tail = out.rpartition(_CWD_MARK)
        out, cwd = head.rstrip("\r\n"), tail.strip().splitlines()[0] if tail.strip() else None
    return {"id": cmd.id, "status": cmd.status, "exit_code": cmd.exit_code, "stdout": out[-60000:],
            "stderr": (cmd.stderr or "")[-20000:], "cwd": cwd,
            "finished_at": cmd.finished_at.isoformat() + "Z" if cmd.finished_at else None}


ENABLE_RDP_PS = r"""
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -Value 1
Enable-NetFirewallRule -DisplayGroup 'Remotedesktop' -ErrorAction SilentlyContinue
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic; Start-Service TermService
"RDP ist an (mit Netzwerkebenen-Authentifizierung)."
"""


@router.get("/remote/{uid}/rdp")
async def app_remote_rdp(uid: str, username: str = Query("", max_length=120), db: AsyncSession = Depends(get_db),
                         _=Depends(get_current_admin)):
    """Verbindungsdaten fuer eine RDP-App (Microsoft Remote Desktop / Windows App)."""
    from urllib.parse import quote
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    if not (agent.platform or "").lower().startswith("win"):
        raise HTTPException(status_code=400, detail="RDP gibt es nur für Windows-Rechner – Linux über die Konsole.")
    address = agent.ip_address or agent.hostname
    lines = [f"full address:s:{address}:3389", "prompt for credentials:i:1", "administrative session:i:1",
             "authentication level:i:2", "screen mode id:i:2", "smart sizing:i:1", "dynamic resolution:i:1"]
    if username:
        lines.append(f"username:s:{username}")
    uri = "rdp://" + "&".join(quote(x, safe=":") for x in lines[:1] + ([f"username:s:{username}"] if username else []))
    return {"host": agent.hostname, "address": address, "port": 3389, "rdp_file": "\r\n".join(lines) + "\r\n",
            "uri": uri.replace("%20", "%20"),
            "hint": "Das Handy muss den Rechner erreichen (gleiches Netz oder VPN). Gebraucht wird eine RDP-App, "
                    "z. B. „Windows App“ (Microsoft Remote Desktop)."}


@router.post("/remote/{uid}/enable-rdp", status_code=201)
async def app_remote_enable_rdp(uid: str, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    from ..models import Command
    from .. import agentwake, journal
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None or not (agent.platform or "").lower().startswith("win"):
        raise HTTPException(status_code=404, detail="Unbekannter Windows-Rechner.")
    cmd = Command(agent_id=agent.id, shell="powershell", command=ENABLE_RDP_PS, timeout=120, kind="shell",
                  source="RDP einschalten", created_by=f"{admin.username} (App)")
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    agentwake.notify(agent.id)
    journal.fire(category="rmm", event="app.remote.enable_rdp", actor=admin.username, target=agent.hostname,
                 message=f"{admin.username} (App): RDP auf {agent.hostname} eingeschaltet.")
    return {"id": cmd.id}
