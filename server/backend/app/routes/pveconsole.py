# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Proxmox: Konsolen (Knoten, LXC, VM), Updates des
#               Knotens, Befehle und Agent-Installation in LXC per pct exec
# ==============================================================================
"""
    GET  /api/pve/updates                             Updates aller Knoten (zwischengespeichert)
    GET  /api/pve/hosts/{id}/nodes                    Knoten eines Hosts
    GET  /api/pve/hosts/{id}/nodes/{node}/updates     verfuegbare Pakete
    POST /api/pve/hosts/{id}/nodes/{node}/updates/refresh   apt update        (Admin)
    POST /api/pve/hosts/{id}/nodes/{node}/upgrade     pveupgrade              (Admin)
    POST /api/pve/hosts/{id}/nodes/{node}/reboot      Knoten neu starten      (Admin)
    GET  /api/pve/jobs                                laufende/letzte Upgrades
    GET  /api/pve/jobs/{id}                           Ausgabe eines Upgrades
    GET  /api/pve/patching                            Zeitplaene, laufende und letzte Patch-Laeufe
    PUT  /api/pve/patching/hosts/{id}                 Zeitplan eines Hosts       (Admin)
    POST /api/pve/hosts/{id}/patch                    rollierender Patch-Lauf    (Admin)
    GET  /api/pve/patching/runs/{id}                  ein Patch-Lauf
    POST /api/pve/hosts/{id}/lxc/{vmid}/exec          Befehl per pct exec     (Admin)
    POST /api/pve/hosts/{id}/lxc/{vmid}/install-agent RIMPERIO-Agent in LXC   (Admin)
    WS   /api/pve/console/ws                          Konsole (Ticket-Zweck "console")
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, pve, pvepatch, pveterm, tenancy
from ..auth import admin_from_token, get_current_admin, get_current_user
from ..database import async_session, get_db
from ..limiter import client_ip as real_client_ip
from ..models import PveHost, User

logger = logging.getLogger("rimperio.routes.pveconsole")

router = APIRouter(prefix="/api/pve", tags=["Proxmox Konsole & Updates"])

_UPDATES_CACHE: Dict[str, tuple] = {}
CACHE_SECONDS = 600


async def _host(db: AsyncSession, host_id: int) -> PveHost:
    h = await db.get(PveHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Unbekannter Proxmox-Host.")
    db.expunge(h)
    return h


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


class ConfirmIn(BaseModel):
    confirm: str = ""


class PatchRunIn(BaseModel):
    confirm: str = ""
    reboot: Optional[str] = None          # never | kernel | always - sonst aus dem Zeitplan
    refresh: Optional[bool] = None
    only_security: Optional[bool] = None
    nodes: Optional[List[str]] = None     # leer/None = wie im Zeitplan (Standard: alle)


class ExecIn(BaseModel):
    node: str = Field(..., min_length=1, max_length=100)
    command: str = Field(..., min_length=1, max_length=8000)
    timeout: int = Field(300, ge=10, le=3600)


class LxcInstallIn(BaseModel):
    node: str = Field(..., min_length=1, max_length=100)
    secret: str = Field(..., min_length=8, max_length=200)
    site: str = Field("Standard", max_length=100)


@router.get("/hosts/{host_id}/nodes")
async def nodes(host_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    h = await _host(db, host_id)
    try:
        async with pve.PveClient(h, timeout=20.0) as client:
            rows = await client.get("/nodes") or []
    except pve.PveError as exc:
        raise _bad(exc) from exc
    return {"items": [{"node": n.get("node"), "status": n.get("status"), "cpu": n.get("cpu"),
                       "mem": n.get("mem"), "maxmem": n.get("maxmem"), "uptime": n.get("uptime")}
                      for n in rows]}


async def _collect_updates(h: PveHost, force: bool = False) -> dict:
    key = f"{h.id}"
    cached = _UPDATES_CACHE.get(key)
    if cached and not force and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    out = {"host_id": h.id, "host": h.name, "nodes": [], "error": None}
    try:
        async with pve.PveClient(h, timeout=20.0) as client:
            node_rows = await client.get("/nodes") or []
        for n in node_rows:
            if n.get("status") != "online":
                out["nodes"].append({"node": n.get("node"), "online": False, "count": 0, "items": []})
                continue
            try:
                info = await pveterm.node_updates(h, n["node"])
                info["online"] = True
            except pve.PveError as exc:
                info = {"node": n.get("node"), "online": True, "count": 0, "items": [], "error": str(exc)}
            out["nodes"].append(info)
    except pve.PveError as exc:
        out["error"] = str(exc)
    _UPDATES_CACHE[key] = (time.monotonic(), out)
    return out


@router.get("/updates")
async def all_updates(force: bool = Query(False), db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)):
    """Updates aller Proxmox-Knoten - fuer Patch-Center und App."""
    hosts = (await db.execute(select(PveHost).where(PveHost.enabled.is_(True)).order_by(PveHost.name))).scalars().all()
    for h in hosts:
        db.expunge(h)
    results = await asyncio.gather(*(_collect_updates(h, force) for h in hosts))
    visible = {h.id for h in hosts}
    jobs = [j for j in pveterm.jobs_for() if tenancy.current() is None or j.get("host_id") in visible]
    return {"hosts": list(results), "jobs": jobs,
            "total": sum(n.get("count", 0) for r in results for n in r["nodes"])}


@router.get("/hosts/{host_id}/nodes/{node}/updates")
async def node_updates(host_id: int, node: str, db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_user)):
    h = await _host(db, host_id)
    try:
        return await pveterm.node_updates(h, node)
    except pve.PveError as exc:
        raise _bad(exc) from exc


@router.post("/hosts/{host_id}/nodes/{node}/updates/refresh")
async def refresh_updates(host_id: int, node: str, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_admin)):
    h = await _host(db, host_id)
    try:
        upid = await pveterm.refresh_node_updates(h, node)
    except pve.PveError as exc:
        raise _bad(exc) from exc
    _UPDATES_CACHE.pop(f"{h.id}", None)
    return {"upid": upid, "message": "Paketlisten werden aufgefrischt – in etwa einer Minute neu laden."}


@router.post("/hosts/{host_id}/nodes/{node}/upgrade", status_code=202)
async def upgrade(host_id: int, node: str, data: ConfirmIn, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_admin)):
    if data.confirm != "UPGRADE":
        raise HTTPException(status_code=400, detail="Bestätigung fehlt: 'confirm' muss 'UPGRADE' lauten.")
    h = await _host(db, host_id)
    try:
        job = await pveterm.start_node_upgrade(h, node, user.username)
    except pve.PveError as exc:
        raise _bad(exc) from exc
    _UPDATES_CACHE.pop(f"{h.id}", None)
    journal.fire(category="update", event="pve.node.upgrade.started", level="warning", actor=user.username,
                 target=f"{h.name}/{node}", message=f"Upgrade des Proxmox-Knotens {node} ({h.name}) gestartet.")
    return job


@router.post("/hosts/{host_id}/nodes/{node}/reboot", status_code=202)
async def reboot(host_id: int, node: str, data: ConfirmIn, db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_admin)):
    if data.confirm != "REBOOT":
        raise HTTPException(status_code=400, detail="Bestätigung fehlt: 'confirm' muss 'REBOOT' lauten.")
    h = await _host(db, host_id)
    try:
        upid = await pveterm.reboot_node(h, node)
    except pve.PveError as exc:
        raise _bad(exc) from exc
    journal.fire(category="update", event="pve.node.reboot", level="warning", actor=user.username,
                 target=f"{h.name}/{node}", message=f"Neustart des Proxmox-Knotens {node} ({h.name}) angestoßen.")
    return {"upid": upid}


@router.get("/jobs")
async def jobs(host_id: Optional[int] = Query(None), db: AsyncSession = Depends(get_db),
               _: User = Depends(get_current_user)):
    items = pveterm.jobs_for(host_id)
    if tenancy.current() is not None:
        visible = await _visible_hosts(db)
        items = [j for j in items if j.get("host_id") in visible]
    return {"items": items}


async def _visible_hosts(db: AsyncSession) -> set:
    """Proxmox-Hosts, die der Anfragende sehen darf (Mandanten-Filter greift)."""
    return set((await db.execute(select(PveHost.id))).scalars().all())


@router.get("/jobs/{job_id}")
async def job(job_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    j = pveterm.JOBS.get(job_id)
    if j is not None and tenancy.current() is not None and j.get("host_id") not in await _visible_hosts(db):
        j = None
    if j is None:
        raise HTTPException(status_code=404, detail="Unbekannter Vorgang (nach einem Neustart des Servers vergessen).")
    return pveterm.public_job(j)


# -----------------------------------------------------------------------------
# Patchen der Knoten: rollierend, von Hand oder im Wartungsfenster (app/pvepatch.py)
# -----------------------------------------------------------------------------
@router.get("/patching")
async def patching(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    hosts = (await db.execute(select(PveHost).order_by(PveHost.name))).scalars().all()
    for h in hosts:
        db.expunge(h)
    return await pvepatch.overview(list(hosts))


@router.put("/patching/hosts/{host_id}")
async def save_patching(host_id: int, data: Dict[str, Any], db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)):
    h = await _host(db, host_id)
    try:
        cfg = await pvepatch.save_host_config(h.id, data)
    except (ValueError, TypeError) as exc:
        raise _bad(exc) from exc
    journal.fire(category="update", event="pve.patch.schedule", actor=user.username, target=h.name,
                 message=f"Zeitplan für das Patchen der Proxmox-Knoten auf {h.name} "
                         f"{'eingeschaltet' if cfg['enabled'] else 'ausgeschaltet'}.", detail=cfg)
    return cfg


@router.post("/hosts/{host_id}/patch", status_code=202)
async def patch_host(host_id: int, data: PatchRunIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)):
    if data.confirm != "UPGRADE":
        raise HTTPException(status_code=400, detail="Bestätigung fehlt: 'confirm' muss 'UPGRADE' lauten.")
    h = await _host(db, host_id)
    cfg = dict(await pvepatch.host_config(h.id))
    for key in ("reboot", "refresh", "only_security"):
        value = getattr(data, key)
        if value is not None:
            cfg[key] = value
    try:
        cfg = pvepatch.clean_host_config({**cfg, "enabled": False})
        run = pvepatch.start_run(h, cfg, user.username, trigger="manual",
                                 nodes=data.nodes if data.nodes else None)
    except (ValueError, pve.PveError) as exc:
        raise _bad(exc) from exc
    _UPDATES_CACHE.pop(f"{h.id}", None)
    return run


@router.get("/patching/runs/{run_id}")
async def patch_run(run_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    run = pvepatch.RUNS.get(run_id)
    visible = await _visible_hosts(db) if tenancy.current() is not None else None
    if run is None:
        old = next((r for r in await pvepatch.history() if r.get("id") == run_id), None)
        if old is None or (visible is not None and old.get("host_id") not in visible):
            raise HTTPException(status_code=404, detail="Unbekannter Patch-Lauf.")
        return old
    if visible is not None and run.get("host_id") not in visible:
        raise HTTPException(status_code=404, detail="Unbekannter Patch-Lauf.")
    return pvepatch.public_run(run)


@router.post("/hosts/{host_id}/lxc/{vmid}/exec")
async def lxc_exec(host_id: int, vmid: int, data: ExecIn, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_admin)):
    h = await _host(db, host_id)
    try:
        result = await pveterm.run_on_node(h, data.node, pveterm.pct_exec_command(vmid, data.command), data.timeout)
    except pve.PveError as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="pve.lxc.exec", actor=user.username, target=f"{h.name}/{data.node}/{vmid}",
                 message=f"Befehl in Container {vmid} ({h.name}/{data.node}): {data.command[:200]}",
                 ok=result.get("ok", False))
    return result


@router.post("/hosts/{host_id}/lxc/{vmid}/install-agent")
async def lxc_install(host_id: int, vmid: int, data: LxcInstallIn, request: Request,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)):
    """Installiert den RIMPERIO-Agent in einem LXC-Container - ueber `pct exec`
    in der Knoten-Shell, also ohne Anmeldung im Container und ohne SSH."""
    from .. import public_url
    h = await _host(db, host_id)
    server = public_url.base_url(request).rstrip("/")
    command = pve.install_command("linux", server, data.secret, data.site or "Standard")
    # Minimal-Container (Alpine, Debian-slim) haben oft weder curl noch wget.
    prep = ("command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || "
            "(command -v apt-get >/dev/null && apt-get update -qq && apt-get install -y -qq curl ca-certificates) || "
            "(command -v apk >/dev/null && apk add --no-cache curl bash) || "
            "(command -v dnf >/dev/null && dnf install -y curl) ; "
            "command -v bash >/dev/null 2>&1 || (command -v apk >/dev/null && apk add --no-cache bash); ")
    try:
        result = await pveterm.run_on_node(h, data.node, pveterm.pct_exec_command(vmid, prep + command), 900)
    except pve.PveError as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="pve.lxc.install", actor=user.username, target=f"{h.name}/{data.node}/{vmid}",
                 ok=result.get("ok", False),
                 message=f"RIMPERIO-Agent in Container {vmid} ({h.name}/{data.node}) per pct exec "
                         + ("installiert." if result.get("ok") else "– Installation fehlgeschlagen."))
    return result


@router.websocket("/console/ws")
async def console(websocket: WebSocket,
                  token: str = Query(..., description="Ticket aus /api/auth/ticket (Zweck: console)"),
                  host_id: int = Query(...), node: str = Query(..., min_length=1, max_length=100),
                  kind: str = Query("lxc", pattern="^(node|lxc|qemu)$"),
                  vmid: Optional[int] = Query(None, ge=1),
                  rows: int = Query(32, ge=4, le=300), cols: int = Query(120, ge=20, le=500)):
    """Proxmox-Konsole im Browser bzw. in der App - dasselbe Protokoll wie
    /api/shell/ws (input/resize/ping → output/ready/error/closed)."""
    user = await admin_from_token(token, purpose="console")
    await websocket.accept()
    if not user:
        await websocket.send_json({"type": "error", "message": "Nicht angemeldet oder keine Administratorrechte."})
        await websocket.close(code=1008)
        return
    async with async_session() as db:
        h = await db.get(PveHost, host_id)
        if h is not None:
            db.expunge(h)
    if h is None:
        await websocket.send_json({"type": "error", "message": "Unbekannter Proxmox-Host."})
        await websocket.close(code=1008)
        return
    if kind != "node" and not vmid:
        await websocket.send_json({"type": "error", "message": "Für LXC/VM fehlt die VMID."})
        await websocket.close(code=1008)
        return
    target = f"{h.name}/{node}" + (f"/{vmid}" if vmid else "")
    journal.fire(category="shell", event="pve.console.opened", actor=user.username, target=target,
                 source_ip=real_client_ip(websocket),
                 message=f"Proxmox-Konsole ({kind}) {target} geöffnet.")
    try:
        async with pveterm.open_terminal(h, kind, node, vmid, cmd="login" if kind == "node" else None,
                                         cols=cols, rows=rows) as term:
            await websocket.send_json({"type": "ready", "user": user.username, "target": target,
                                       "kind": kind})
            pending = getattr(term, "pending", "")
            if pending:
                await websocket.send_json({"type": "output", "data": pending})

            async def pump() -> None:
                while True:
                    chunk = await term.recv()
                    if chunk is None:
                        break
                    if chunk:
                        await websocket.send_json({"type": "output", "data": chunk})
                try:
                    await websocket.send_json({"type": "closed", "reason": "Die Konsole wurde beendet."})
                    await websocket.close()
                except Exception:
                    pass

            async def keepalive() -> None:
                while True:
                    await asyncio.sleep(25)
                    await term.ping()

            tasks = [asyncio.create_task(pump()), asyncio.create_task(keepalive())]
            try:
                while True:
                    raw = await websocket.receive_text()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await term.send(raw)
                        continue
                    t = msg.get("type")
                    if t == "input":
                        await term.send(str(msg.get("data", "")))
                    elif t == "resize":
                        await term.resize(int(msg.get("cols", 120)), int(msg.get("rows", 32)))
                    elif t == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif t == "close":
                        break
            except WebSocketDisconnect:
                pass
            finally:
                for task in tasks:
                    task.cancel()
    except pve.PveError as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1011)
        except Exception:
            pass
    except Exception as exc:                                        # defensiv
        logger.warning("Proxmox-Konsole %s: %s", target, exc)
        try:
            await websocket.close()
        except Exception:
            pass
