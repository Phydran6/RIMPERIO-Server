# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API Patchmanagement, Proxmox, Update-Quellen, Eingang
# ==============================================================================
"""
`/api/patching/…` - Patch-Center, Richtlinien, Proxmox-Anbindung, Update-Quellen.
`/api/notifications/…` - der Eingang (Glocke in der Oberflaeche, Liste in der App).

Lesen darf jeder angemeldete Benutzer, entscheiden und einstellen nur
Administratoren. Jede Entscheidung steht im Systemtagebuch.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import advisories, journal, notify, patchmgmt as pm, pve
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import (Advisory, Agent, Notification, PatchItem, PatchPolicy, PatchRun,
                      PveHost, PveLink, UpdateSource, User)
from ..rmm import is_online

router = APIRouter(prefix="/api/patching", tags=["Patchmanagement"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["Eingang"])


# ==============================================================================
# Schemas
# ==============================================================================
class DecisionIn(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=2000)
    decision: str = Field(pattern="^(approve|reject|defer|reset)$")
    defer_days: int = Field(default=7, ge=1, le=90)


class IdsIn(BaseModel):
    ids: List[int] = Field(default_factory=list, max_length=2000)


class InstallIn(BaseModel):
    item_ids: List[int] = Field(default_factory=list, max_length=2000)
    snapshot: Optional[bool] = None


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    scope_type: str = Field(default="global", pattern="^(global|site|agent)$")
    scope_value: str = ""
    settings: Dict[str, Any] = Field(default_factory=dict)


class PveHostIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=255)
    auth_type: str = Field(default="token", pattern="^(token|password)$")
    token_id: str = ""
    username: str = ""
    secret: str = Field(default="", description="Leer lassen = unverändert")
    verify_tls: bool = False
    enabled: bool = True
    default_storage: str = ""


class PveLinkIn(BaseModel):
    host_id: int
    node: str = Field(min_length=1)
    vmid: int = Field(ge=1)
    guest_type: str = Field(default="qemu", pattern="^(qemu|lxc)$")
    guest_name: str = ""


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="rss", pattern="^(rss|kev|github)$")
    url: str = Field(min_length=8, max_length=1024)
    platforms: List[str] = Field(default_factory=lambda: ["windows", "linux", "docker"])
    enabled: bool = True
    interval_minutes: int = Field(default=360, ge=15, le=10080)
    language: str = "en"
    note: str = ""


class SourcePatch(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=15, le=10080)
    name: Optional[str] = None
    url: Optional[str] = None
    platforms: Optional[List[str]] = None
    note: Optional[str] = None


class SearchTestIn(BaseModel):
    query: str = Field(min_length=2, max_length=300)


# ==============================================================================
# Helfer
# ==============================================================================
async def _agent(db: AsyncSession, uid: str) -> Agent:
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    return agent


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ==============================================================================
# Übersicht
# ==============================================================================
@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    agents = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None))
                               .order_by(Agent.hostname))).scalars().all()
    pols = await pm.policies(db)
    counts: Dict[int, Dict[str, int]] = {}
    for agent_id, status, n in (await db.execute(
            select(PatchItem.agent_id, PatchItem.status, func.count())
            .group_by(PatchItem.agent_id, PatchItem.status))).all():
        counts.setdefault(agent_id, {})[status] = n
    active = {r.agent_id: r for r in (await db.execute(
        select(PatchRun).where(PatchRun.status.in_(pm.ACTIVE_RUN)))).scalars()}
    last = {}
    for r in (await db.execute(select(PatchRun).order_by(desc(PatchRun.id)).limit(500))).scalars():
        last.setdefault(r.agent_id, r)
    links = {l.agent_id: l for l in (await db.execute(select(PveLink))).scalars()}
    rows = []
    totals = {"needs_approval": 0, "auto": 0, "approved": 0, "failed": 0, "review": 0}
    for a in agents:
        policy = pm.pick_policy(a, pols)
        settings = pm.effective_settings(policy)
        is_open, begin, end = pm.window_state(settings)
        c = counts.get(a.id, {})
        for k in totals:
            totals[k] += c.get(k, 0)
        lr = last.get(a.id)
        rows.append({
            "agent_uid": a.agent_uid, "hostname": a.hostname, "site": a.site or "Standard",
            "platform": a.platform, "os": a.os, "online": is_online(a, 300),
            "policy": {"id": policy.id, "name": policy.name} if policy else None,
            "counts": c, "scan_queued_at": pm._iso(a.patch_scan_queued_at),
            "window_open": is_open, "window_next": pm._iso(begin if not is_open else None),
            "window_end": pm._iso(end), "reboot_pending": bool((a.extra_data or {}).get("reboot_pending")),
            "docker": bool((a.docker_inventory or {}).get("available")),
            "docker_scanned_at": pm._iso(a.docker_scanned_at),
            "pve": pve.public_link(links[a.id]) if a.id in links else None,
            "active_run": pm.public_run(active[a.id]) if a.id in active else None,
            "last_run": {"id": lr.id, "status": lr.status, "finished_at": pm._iso(lr.finished_at)} if lr else None,
        })
    return {"agents": rows, "totals": totals}


@router.get("/items")
async def list_items(status: Optional[str] = None, agent_uid: Optional[str] = None,
                     kind: Optional[str] = None, platform: Optional[str] = None,
                     q: Optional[str] = None, limit: int = Query(500, ge=1, le=5000),
                     db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    query = select(PatchItem, Agent).join(Agent, Agent.id == PatchItem.agent_id)
    if status:
        query = query.where(PatchItem.status.in_(status.split(",")))
    if agent_uid:
        query = query.where(Agent.agent_uid == agent_uid)
    if kind:
        query = query.where(PatchItem.kind == kind)
    if platform:
        query = query.where(PatchItem.platform == platform)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(PatchItem.title.ilike(like), PatchItem.kb.ilike(like),
                                PatchItem.key.ilike(like), Agent.hostname.ilike(like)))
    query = query.order_by(desc(PatchItem.urgent), PatchItem.status, Agent.hostname, PatchItem.title).limit(limit)
    return [pm.public_item(i, a) for i, a in (await db.execute(query)).all()]


@router.get("/items/{item_id}")
async def get_item(item_id: int, db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_user)) -> dict:
    it = await db.get(PatchItem, item_id)
    if it is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Update.")
    return pm.public_item(it, await db.get(Agent, it.agent_id))


@router.post("/items/decision")
async def decision(data: DecisionIn, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_admin)) -> dict:
    try:
        n = await pm.decide(db, data.ids, data.decision, user.username, data.defer_days)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"changed": n}


@router.post("/items/reassess")
async def reassess(data: IdsIn, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_admin)) -> dict:
    """KI-Einschätzung verwerfen und neu anstoßen."""
    rows = (await db.execute(select(PatchItem).where(PatchItem.id.in_(data.ids)))).scalars().all()
    for it in rows:
        if it.status in ("auto", "needs_approval", "review"):
            it.data = {**(it.data or {}), "_planned": it.status if it.status != "review" else (it.data or {}).get("_planned", "auto")}
            it.ai, it.status = None, "review"
    await db.commit()
    return {"queued": len(rows)}


@router.post("/agents/{uid}/scan", status_code=201)
async def scan(uid: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    _, settings = await pm.policy_for(db, agent)
    ids = await pm.queue_scan(db, agent, settings, user.username)
    await db.commit()
    return {"commands": ids}


@router.post("/scan-all", status_code=201)
async def scan_all(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    agents = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
    pols = await pm.policies(db)
    n = 0
    for a in agents:
        n += len(await pm.queue_scan(db, a, pm.effective_settings(pm.pick_policy(a, pols)), user.username))
    await db.commit()
    return {"commands": n, "agents": len(agents)}


@router.post("/agents/{uid}/install", status_code=201)
async def install(uid: str, data: InstallIn, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    try:
        run = await pm.install_now(db, agent, data.item_ids, user.username, data.snapshot)
    except ValueError as exc:
        raise _bad(exc) from exc
    return pm.public_run(run, agent)


@router.get("/agents/{uid}/docker")
async def docker_inventory(uid: str, db: AsyncSession = Depends(get_db),
                           _: User = Depends(get_current_user)) -> dict:
    agent = await _agent(db, uid)
    return {"scanned_at": pm._iso(agent.docker_scanned_at), **(agent.docker_inventory or {"available": False})}


# ==============================================================================
# Läufe
# ==============================================================================
@router.get("/runs")
async def runs(agent_uid: Optional[str] = None, status: Optional[str] = None,
               limit: int = Query(100, ge=1, le=1000), db: AsyncSession = Depends(get_db),
               _: User = Depends(get_current_user)) -> List[dict]:
    query = select(PatchRun, Agent).join(Agent, Agent.id == PatchRun.agent_id)
    if agent_uid:
        query = query.where(Agent.agent_uid == agent_uid)
    if status:
        query = query.where(PatchRun.status.in_(status.split(",")))
    rows = (await db.execute(query.order_by(desc(PatchRun.id)).limit(limit))).all()
    return [pm.public_run(r, a) for r, a in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)) -> dict:
    run = await db.get(PatchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unbekannter Lauf.")
    agent = await db.get(Agent, run.agent_id)
    items = (await db.execute(select(PatchItem).where(PatchItem.id.in_(run.items or [])))).scalars().all()
    out = pm.public_run(run, agent)
    out["item_details"] = [pm.public_item(i, agent) for i in items]
    out["baseline"], out["after"] = run.baseline, run.after
    return out


@router.post("/runs/{run_id}/rollback")
async def run_rollback(run_id: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    try:
        run = await pm.rollback(db, run_id, user.username)
    except (ValueError, pve.PveError) as exc:
        raise _bad(exc) from exc
    return pm.public_run(run)


@router.post("/runs/{run_id}/snapshot/delete")
async def run_snapshot_delete(run_id: int, db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_admin)) -> dict:
    try:
        run = await pm.delete_snapshot_now(db, run_id, user.username)
    except (ValueError, pve.PveError) as exc:
        raise _bad(exc) from exc
    return pm.public_run(run)


@router.post("/runs/{run_id}/snapshot/keep")
async def run_snapshot_keep(run_id: int, keep: bool = True, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_admin)) -> dict:
    try:
        run = await pm.keep_snapshot(db, run_id, user.username, keep)
    except ValueError as exc:
        raise _bad(exc) from exc
    return pm.public_run(run)


@router.post("/runs/{run_id}/cancel")
async def run_cancel(run_id: int, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    run = await db.get(PatchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unbekannter Lauf.")
    if run.status not in ("pending", "snapshotting", "baseline"):
        raise HTTPException(status_code=409, detail="Nur vor der Installation abbrechbar.")
    pm._stage(run, "cancelled", f"{user.username}: abgebrochen.", "warning")
    run.finished_at = datetime.utcnow()
    for it in (await db.execute(select(PatchItem).where(PatchItem.id.in_(run.items or [])))).scalars():
        it.run_id = None
    await db.commit()
    return pm.public_run(run)


# ==============================================================================
# Richtlinien
# ==============================================================================
@router.get("/policies")
async def list_policies(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    return [pm.public_policy(p) for p in await pm.policies(db)]


@router.get("/policies/defaults")
async def policy_defaults(_: User = Depends(get_current_user)) -> dict:
    return {"settings": pm.DEFAULT_POLICY, "status_labels": pm.STATUS_LABEL,
            "rule_fields": ["category", "title", "package", "image", "container", "kind",
                            "severity", "kb", "security", "major", "reboot"],
            "rule_ops": ["equals", "contains", "in", "glob", "regex", "is_true", "is_false"],
            "rule_actions": ["auto", "approve", "ignore"]}


@router.post("/policies", status_code=201)
async def create_policy(data: PolicyIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    try:
        settings = pm.validate_settings(data.settings)
    except ValueError as exc:
        raise _bad(exc) from exc
    p = PatchPolicy(**{**data.model_dump(), "settings": settings})
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await pm.reclassify(db)
    journal.fire(category="rmm", event="patch.policy.created", actor=user.username,
                 message=f"Patch-Richtlinie „{p.name}“ angelegt.")
    return pm.public_policy(p)


@router.put("/policies/{policy_id}")
async def update_policy(policy_id: int, data: PolicyIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    p = await db.get(PatchPolicy, policy_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Unbekannte Richtlinie.")
    try:
        settings = pm.validate_settings(data.settings)
    except ValueError as exc:
        raise _bad(exc) from exc
    for key, value in data.model_dump().items():
        if p.builtin and key in ("scope_type", "scope_value"):
            continue                      # die Standardrichtlinie gilt immer global
        setattr(p, key, value)
    p.settings = settings
    if p.builtin:
        p.enabled = True
    await db.commit()
    changed = await pm.reclassify(db)
    journal.fire(category="rmm", event="patch.policy.updated", actor=user.username,
                 message=f"Patch-Richtlinie „{p.name}“ geändert ({changed} Updates neu eingestuft).")
    return pm.public_policy(p)


@router.delete("/policies/{policy_id}")
async def delete_policy(policy_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    p = await db.get(PatchPolicy, policy_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Unbekannte Richtlinie.")
    if p.builtin:
        raise HTTPException(status_code=409, detail="Die Standardrichtlinie lässt sich nur ändern, nicht löschen.")
    await db.delete(p)
    await db.commit()
    await pm.reclassify(db)
    journal.fire(category="rmm", event="patch.policy.deleted", actor=user.username,
                 message=f"Patch-Richtlinie „{p.name}“ gelöscht.")
    return {"status": "ok"}


@router.get("/policies/{policy_id}/preview")
async def policy_preview(policy_id: int, db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_user)) -> dict:
    """Für welche Rechner gilt die Richtlinie, und wann ist das nächste Fenster?"""
    p = await db.get(PatchPolicy, policy_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Unbekannte Richtlinie.")
    pols = await pm.policies(db)
    agents = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
    applies = [a.hostname for a in agents if (pm.pick_policy(a, pols) or PatchPolicy(id=-1)).id == p.id]
    settings = pm.effective_settings(p)
    is_open, begin, end = pm.window_state(settings)
    return {"agents": applies, "window_open": is_open, "window_next": pm._iso(begin),
            "window_end": pm._iso(end)}


# ==============================================================================
# Proxmox
# ==============================================================================
@router.get("/pve/hosts")
async def pve_hosts(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    return [pve.public_host(h) for h in (await db.execute(select(PveHost).order_by(PveHost.name))).scalars()]


def _check_pve(data: PveHostIn, existing: Optional[PveHost] = None) -> None:
    if not data.url.startswith("https://"):
        raise HTTPException(status_code=422, detail="Die Adresse muss mit https:// beginnen (z. B. https://pve:8006).")
    if data.auth_type == "token" and "!" not in data.token_id:
        raise HTTPException(status_code=422, detail="Token-ID bitte als benutzer@realm!tokenname.")
    if data.auth_type == "password" and "@" not in data.username:
        raise HTTPException(status_code=422, detail="Benutzer bitte mit Realm, z. B. root@pam.")
    if not data.secret and not (existing and existing.secret):
        raise HTTPException(status_code=422, detail="Geheimnis bzw. Passwort fehlt.")


@router.post("/pve/hosts", status_code=201)
async def pve_create(data: PveHostIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    _check_pve(data)
    h = PveHost(**data.model_dump())
    db.add(h)
    await db.commit()
    await db.refresh(h)
    journal.fire(category="rmm", event="pve.created", actor=user.username,
                 message=f"Proxmox-Host „{h.name}“ ({h.url}) angelegt.")
    try:
        await pve.refresh_guests(db, h)
        await pve.automatch(db)
    except pve.PveError:
        pass
    return pve.public_host(h)


@router.put("/pve/hosts/{host_id}")
async def pve_update(host_id: int, data: PveHostIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    h = await db.get(PveHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Unbekannter Proxmox-Host.")
    _check_pve(data, h)
    for key, value in data.model_dump().items():
        if key == "secret" and not value:
            continue
        setattr(h, key, value)
    await db.commit()
    journal.fire(category="rmm", event="pve.updated", actor=user.username,
                 message=f"Proxmox-Host „{h.name}“ geändert.")
    return pve.public_host(h)


@router.delete("/pve/hosts/{host_id}")
async def pve_delete(host_id: int, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    h = await db.get(PveHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Unbekannter Proxmox-Host.")
    await db.delete(h)
    await db.commit()
    journal.fire(category="rmm", event="pve.deleted", actor=user.username,
                 message=f"Proxmox-Host „{h.name}“ entfernt.")
    return {"status": "ok"}


@router.post("/pve/hosts/{host_id}/test")
async def pve_test(host_id: int, db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_admin)) -> dict:
    h = await db.get(PveHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Unbekannter Proxmox-Host.")
    try:
        info = await pve.test(h)
    except pve.PveError as exc:
        h.last_error = str(exc)
        await db.commit()
        return {"ok": False, "message": str(exc)}
    h.last_ok_at, h.last_error = datetime.utcnow(), None
    await db.commit()
    return {"ok": True, **info, "message": f"Verbunden: Proxmox VE {info.get('version')}, Knoten {', '.join(info.get('nodes') or [])}."}


@router.post("/pve/hosts/{host_id}/refresh")
async def pve_refresh(host_id: int, db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_admin)) -> dict:
    h = await db.get(PveHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Unbekannter Proxmox-Host.")
    try:
        guests = await pve.refresh_guests(db, h)
    except pve.PveError as exc:
        raise _bad(exc) from exc
    matched = await pve.automatch(db)
    return {"guests": guests, "matched": matched}


@router.get("/pve/links")
async def pve_links(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(PveLink, Agent).join(Agent, Agent.id == PveLink.agent_id))).all()
    return [{**pve.public_link(l), "agent_uid": a.agent_uid, "hostname": a.hostname} for l, a in rows]


@router.post("/pve/automatch")
async def pve_automatch(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    return {"matched": await pve.automatch(db)}


@router.put("/pve/links/{uid}")
async def pve_link_set(uid: str, data: PveLinkIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    if await db.get(PveHost, data.host_id) is None:
        raise HTTPException(status_code=404, detail="Unbekannter Proxmox-Host.")
    link = (await db.execute(select(PveLink).where(PveLink.agent_id == agent.id))).scalar_one_or_none()
    if link is None:
        link = PveLink(agent_id=agent.id, **data.model_dump())
        db.add(link)
    else:
        for k, v in data.model_dump().items():
            setattr(link, k, v)
    link.matched_by = "manual"
    await db.commit()
    journal.fire(category="rmm", event="pve.linked", actor=user.username, target=agent.hostname,
                 message=f"{agent.hostname} → Proxmox VM {data.vmid} auf {data.node}.")
    return pve.public_link(link)


@router.delete("/pve/links/{uid}")
async def pve_link_delete(uid: str, db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    link = (await db.execute(select(PveLink).where(PveLink.agent_id == agent.id))).scalar_one_or_none()
    if link:
        await db.delete(link)
        await db.commit()
    return {"status": "ok"}


@router.get("/pve/snapshots")
async def pve_snapshots(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    """Alle Sicherungen, die RIMPERIO angelegt und noch nicht gelöscht hat."""
    rows = (await db.execute(select(PatchRun, Agent).join(Agent, Agent.id == PatchRun.agent_id)
                             .order_by(desc(PatchRun.id)).limit(500))).all()
    out = []
    for r, a in rows:
        s = r.snapshot or {}
        if (s.get("name") or s.get("volid")) and not s.get("deleted_at"):
            out.append({"run_id": r.id, "status": r.status, "hostname": a.hostname,
                        "agent_uid": a.agent_uid, **s})
    return out


# ==============================================================================
# Update-Quellen
# ==============================================================================
@router.get("/sources")
async def sources(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    await advisories.ensure_builtin(db)
    rows = (await db.execute(select(UpdateSource).order_by(desc(UpdateSource.builtin), UpdateSource.name))).scalars()
    cfg = await advisories.load_config(db)
    return {"sources": [advisories.public_source(s) for s in rows], "config": advisories.public_config(cfg)}


@router.post("/sources", status_code=201)
async def create_source(data: SourceIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    if not data.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Die Adresse muss mit http:// oder https:// beginnen.")
    s = UpdateSource(**data.model_dump(), builtin=False)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    added = await advisories.refresh_source(db, s)
    journal.fire(category="rmm", event="source.created", actor=user.username,
                 message=f"Update-Quelle „{s.name}“ angelegt ({added} Meldungen).")
    return advisories.public_source(s)


@router.patch("/sources/{source_id}")
async def patch_source(source_id: int, data: SourcePatch, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    s = await db.get(UpdateSource, source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unbekannte Quelle.")
    for key, value in data.model_dump(exclude_none=True).items():
        if s.builtin and key in ("url", "name"):
            continue
        setattr(s, key, value)
    await db.commit()
    return advisories.public_source(s)


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    s = await db.get(UpdateSource, source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unbekannte Quelle.")
    if s.builtin:
        raise HTTPException(status_code=409, detail="Mitgelieferte Quellen lassen sich nur abschalten.")
    await db.delete(s)
    await db.commit()
    journal.fire(category="rmm", event="source.deleted", actor=user.username,
                 message=f"Update-Quelle „{s.name}“ gelöscht.")
    return {"status": "ok"}


@router.post("/sources/{source_id}/refresh")
async def refresh_source(source_id: int, db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_admin)) -> dict:
    s = await db.get(UpdateSource, source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unbekannte Quelle.")
    added = await advisories.refresh_source(db, s)
    return {"added": added, "source": advisories.public_source(s)}


@router.get("/advisories")
async def list_advisories(source_id: Optional[int] = None, q: Optional[str] = None,
                          limit: int = Query(100, ge=1, le=1000), db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_user)) -> List[dict]:
    query = select(Advisory, UpdateSource.name).join(UpdateSource, UpdateSource.id == Advisory.source_id)
    if source_id:
        query = query.where(Advisory.source_id == source_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Advisory.title.ilike(like), Advisory.summary.ilike(like)))
    rows = (await db.execute(query.order_by(desc(Advisory.published_at)).limit(limit))).all()
    return [advisories.public_advisory(a, n) for a, n in rows]


@router.put("/sources/config")
async def put_sources_config(data: Dict[str, Any], db: AsyncSession = Depends(get_db),
                             user: User = Depends(get_current_admin)) -> dict:
    try:
        cfg = await advisories.save_config(db, data)
    except (ValueError, TypeError) as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="source.config", actor=user.username,
                 message="Einstellungen der Update-Quellen geändert.")
    return cfg


@router.post("/sources/websearch-test")
async def websearch_test(data: SearchTestIn, db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_admin)) -> dict:
    cfg = await advisories.load_config(db)
    if cfg["websearch"]["provider"] == "ai":
        return {"results": [], "note": "Die Websuche läuft über die KI (Claude) direkt bei der Einschätzung."}
    return {"results": await advisories.web_search(cfg, data.query)}


# ==============================================================================
# Eingang
# ==============================================================================
@notifications_router.get("")
async def list_notifications(unread: bool = False, open_only: bool = False,
                             since_id: Optional[int] = None, category: Optional[str] = None,
                             limit: int = Query(50, ge=1, le=500), db: AsyncSession = Depends(get_db),
                             _: User = Depends(get_current_user)) -> dict:
    query = select(Notification)
    if unread:
        query = query.where(Notification.read_at.is_(None))
    if open_only:
        query = query.where(Notification.action != "", Notification.resolved_at.is_(None))
    if since_id:
        query = query.where(Notification.id > since_id)
    if category:
        query = query.where(Notification.category == category)
    rows = (await db.execute(query.order_by(desc(Notification.id)).limit(limit))).scalars().all()
    unread_count = (await db.execute(select(func.count()).select_from(Notification)
                                     .where(Notification.read_at.is_(None)))).scalar_one()
    return {"items": [notify.public(n) for n in rows], "unread": unread_count,
            "newest_id": rows[0].id if rows else since_id}


@notifications_router.post("/read")
async def read_notifications(data: IdsIn, db: AsyncSession = Depends(get_db),
                             _: User = Depends(get_current_user)) -> dict:
    return {"marked": await notify.mark_read(db, data.ids or None)}
