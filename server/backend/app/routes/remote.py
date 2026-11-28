# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API Fernzugriff: Live-Shell, RDP, Live-Sitzung (app/remote.py)
# ==============================================================================
"""
* ``POST /api/remote/{uid}/sessions``          Sitzung anlegen (Admin) - der Agent
                                               bekommt den Auftrag, den Tunnel aufzubauen.
* ``WS   /api/remote/sessions/{id}/shell``     Browser <-> Live-Shell (Ticket "console")
* ``WS   /api/remote/sessions/{id}/guac``      Browser (Guacamole-JS) <-> guacd <-> Tunnel
* ``WS   /api/rmm/agent/tunnel/{id}``          Agent (Agent-Token im Kopf)
* ``POST /api/remote/{uid}/rdp/enable``        Remotedesktop auf dem Rechner einschalten
* ``POST /api/remote/{uid}/live/prepare``      VNC-Dienst fuer die Live-Sitzung vorbereiten
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import agentwake, journal, remote, rmm
from ..auth import admin_from_token, get_current_admin
from ..database import async_session, get_db
from ..models import Agent, Command, User

logger = logging.getLogger("rimperio.remote")

router = APIRouter(prefix="/api/remote", tags=["Fernzugriff"])
agent_router = APIRouter(prefix="/api/rmm/agent", tags=["Fernzugriff"])


class SessionIn(BaseModel):
    kind: str = Field(pattern="^(shell|rdp|vnc)$")
    cols: int = Field(default=120, ge=20, le=500)
    rows: int = Field(default=32, ge=5, le=300)
    shell: str = Field(default="", pattern="^(|powershell|pwsh|cmd|bash|sh|zsh)$")
    # nur RDP - bleibt im Speicher dieser Sitzung, wird nach dem Verbinden verworfen
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=500)
    domain: str = Field(default="", max_length=200)
    security: str = Field(default="any", pattern="^(any|nla|tls|rdp)$")
    layout: str = Field(default="de-de-qwertz", max_length=40)


async def _agent(db: AsyncSession, uid: str) -> Agent:
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    return agent


def _is_windows(agent: Agent) -> bool:
    return (agent.platform or "").lower().startswith("win")


@router.post("/{uid}/sessions", status_code=201)
async def create_session(uid: str, data: SessionIn, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    if data.kind in ("rdp", "vnc") and not _is_windows(agent):
        raise HTTPException(status_code=422, detail="RDP und Live-Sitzung gibt es für Windows-Rechner. "
                                                    "Unter Linux: Live-Shell.")
    params = {}
    if data.kind == "rdp":
        params = {"username": data.username, "password": data.password, "domain": data.domain,
                  "security": data.security, "layout": data.layout}
    elif data.kind == "vnc":
        params = {"password": remote.vnc_password(agent.agent_uid)}
    try:
        s = remote.create(data.kind, agent.id, agent.hostname, user.username, params)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    payload = {"session": s.id, "kind": "shell" if data.kind == "shell" else "tcp",
               "port": {"rdp": 3389, "vnc": 5900}.get(data.kind, 0), "cols": data.cols, "rows": data.rows,
               "shell": data.shell}
    db.add(Command(agent_id=agent.id, shell="sh", command=json.dumps(payload), timeout=60, kind="tunnel",
                   source={"shell": "Live-Shell", "rdp": "Remotedesktop", "vnc": "Live-Sitzung"}[data.kind],
                   created_by=user.username))
    await db.commit()
    agentwake.notify(agent.id)
    journal.fire(category="shell", event=f"remote.{data.kind}", actor=user.username, target=agent.hostname,
                 message=f"{user.username} öffnet {({'shell': 'eine Live-Shell', 'rdp': 'Remotedesktop', 'vnc': 'eine Live-Sitzung'})[data.kind]} "
                         f"auf {agent.hostname}.")
    return {**remote.public(s), "ws": f"/api/remote/sessions/{s.id}/{'shell' if data.kind == 'shell' else 'guac'}",
            "online": rmm.is_online(agent, 300), "platform": agent.platform}


# -----------------------------------------------------------------------------
# Agent
# -----------------------------------------------------------------------------
@agent_router.websocket("/tunnel/{sid}")
async def agent_tunnel(websocket: WebSocket, sid: str, authorization: str = Header(default="")):
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    s = remote.get(sid)
    agent_id = None
    if token:
        async with async_session() as db:
            agent_id = (await db.execute(select(Agent.id).where(Agent.rmm_token_hash == rmm.hash_token(token))
                                         .execution_options(**_all_tenants()))).scalar_one_or_none()
    if s is None or agent_id is None or agent_id != s.agent_id or s.agent_ws is not None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    s.used = True
    await remote.serve_agent(s, websocket)


def _all_tenants() -> dict:
    from .. import tenancy
    return dict(tenancy.ALL_TENANTS)


# -----------------------------------------------------------------------------
# Browser: Live-Shell
# -----------------------------------------------------------------------------
async def _browser_session(websocket: WebSocket, sid: str, token: str):
    user = await admin_from_token(token, purpose="console")
    await websocket.accept()
    s = remote.get(sid)
    if not user or s is None or s.user != user.username:
        await websocket.send_json({"type": "error", "message": "Sitzung unbekannt oder abgelaufen."})
        await websocket.close(code=1008)
        return None
    s.used = True
    return s


@router.websocket("/sessions/{sid}/shell")
async def shell_ws(websocket: WebSocket, sid: str, token: str = Query(...)):
    s = await _browser_session(websocket, sid, token)
    if s is None:
        return
    await websocket.send_json({"type": "status", "message": f"Verbinde mit {s.hostname} …"})
    try:
        await remote.wait_agent(s)
    except RuntimeError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close()
        return
    await websocket.send_json({"type": "ready", "message": f"Verbunden mit {s.hostname}."})

    async def from_browser() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await s.to_agent.put(("data", msg["bytes"]))
            elif msg.get("text") is not None:
                try:
                    ctl = json.loads(msg["text"])
                except ValueError:
                    continue
                if ctl.get("type") == "input":
                    await s.to_agent.put(("data", str(ctl.get("data", "")).encode("utf-8")))
                elif ctl.get("type") == "resize":
                    await s.to_agent.put(("ctl", {"type": "resize", "cols": int(ctl.get("cols", 0)),
                                                  "rows": int(ctl.get("rows", 0))}))
        await s.to_agent.put(("ctl", {"type": "close"}))

    async def to_browser() -> None:
        while True:
            kind, payload = await s.from_agent.get()
            if kind == "eof":
                await websocket.send_json({"type": "closed", "message": "Verbindung beendet."})
                break
            if kind == "data":
                await websocket.send_bytes(payload)
            elif payload.get("type") == "exit":
                await websocket.send_json({"type": "closed", "message": f"Shell beendet (Code {payload.get('code', 0)})."})
                break

    tasks = [asyncio.create_task(from_browser()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await s.to_agent.put(("eof", None))
        s.closed.set()
        try:
            await websocket.close()
        except Exception:                                           # defensiv
            pass


# -----------------------------------------------------------------------------
# Browser: RDP / Live-Sitzung ueber Guacamole
# -----------------------------------------------------------------------------
@router.websocket("/sessions/{sid}/guac")
async def guac_ws(websocket: WebSocket, sid: str, token: str = Query(...),
                  width: int = Query(1280, ge=320, le=7680), height: int = Query(800, ge=240, le=4320),
                  dpi: int = Query(96, ge=48, le=300)):
    user = await admin_from_token(token, purpose="console")
    await websocket.accept(subprotocol="guacamole" if "guacamole" in (websocket.headers.get("sec-websocket-protocol") or "") else None)
    s = remote.get(sid)

    async def fail(text: str) -> None:
        # Guacamole-Fehler: der Client zeigt die Meldung an.
        await websocket.send_text(remote.encode("error", text, "519"))
        await websocket.close()

    if not user or s is None or s.user != user.username or s.kind not in ("rdp", "vnc"):
        await fail("Sitzung unbekannt oder abgelaufen.")
        return
    s.used = True
    try:
        await remote.wait_agent(s)
        g, server = await remote.open_guacd(s, width, height, dpi)
    except RuntimeError as exc:
        await fail(str(exc))
        return
    await websocket.send_text(remote.encode("", s.id))         # Tunnel-ID (interne Anweisung)

    async def from_browser() -> None:
        buf = ""
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            buf += msg.get("text") or (msg.get("bytes") or b"").decode("utf-8", "replace")
            ins, buf, raws = remote.parse(buf)
            out = []
            for elems, raw in zip(ins, raws):
                if elems and elems[0] == "":                  # interne Anweisung (ping)
                    if len(elems) > 1 and elems[1] == "ping":
                        await websocket.send_text(remote.encode("", "ping", *elems[2:]))
                    continue
                out.append(raw)
            if out:
                await g.send("".join(out))

    async def to_browser() -> None:
        buf = g.buf
        g.buf = ""
        while True:
            if buf:
                ins, buf, raws = remote.parse(buf)
                if raws:
                    await websocket.send_text("".join(raws))
            chunk = await g.reader.read(65536)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")

    tasks = [asyncio.create_task(from_browser()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        try:
            g.writer.close()
        except Exception:                                           # defensiv
            pass
        server.close()
        await s.to_agent.put(("ctl", {"type": "close"}))
        await s.to_agent.put(("eof", None))
        s.closed.set()
        try:
            await websocket.close()
        except Exception:                                           # defensiv
            pass


# -----------------------------------------------------------------------------
# Vorbereitung auf dem Rechner (Windows)
# -----------------------------------------------------------------------------
ENABLE_RDP_PS = r"""
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -Value 1
Set-Service -Name TermService -StartupType Automatic; Start-Service TermService
# Keine Firewall-Freigabe nach aussen: RIMPERIO erreicht RDP ueber den Agent-Tunnel (127.0.0.1).
$t = Test-NetConnection -ComputerName 127.0.0.1 -Port 3389 -WarningAction SilentlyContinue
if ($t.TcpTestSucceeded) { 'RDP_READY' } else { throw 'Port 3389 antwortet nicht.' }
"""


async def _queue_ps(db: AsyncSession, agent: Agent, user: User, script: str, source: str) -> int:
    cmd = Command(agent_id=agent.id, shell="powershell", command=script, timeout=900, kind="shell",
                  source=source, created_by=user.username)
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    agentwake.notify(agent.id)
    return cmd.id


@router.post("/{uid}/rdp/enable", status_code=201)
async def rdp_enable(uid: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    if not _is_windows(agent):
        raise HTTPException(status_code=422, detail="Nur für Windows.")
    cid = await _queue_ps(db, agent, user, ENABLE_RDP_PS, "Remotedesktop einschalten")
    journal.fire(category="shell", event="remote.rdp.enable", actor=user.username, target=agent.hostname,
                 message=f"Remotedesktop auf {agent.hostname} eingeschaltet (nur über den Tunnel erreichbar).")
    return {"command_id": cid}


@router.post("/{uid}/live/prepare", status_code=201)
async def live_prepare(uid: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    if not _is_windows(agent):
        raise HTTPException(status_code=422, detail="Die Live-Sitzung gibt es bisher für Windows.")
    cid = await _queue_ps(db, agent, user, remote.live_setup_script(agent.agent_uid), "Live-Sitzung vorbereiten (VNC)")
    journal.fire(category="shell", event="remote.live.prepare", actor=user.username, target=agent.hostname,
                 message=f"Live-Sitzung auf {agent.hostname} vorbereitet (VNC nur für localhost).")
    return {"command_id": cid}


@router.get("/commands/{cid}")
async def command_state(cid: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    cmd = await db.get(Command, cid)
    if cmd is None:
        raise HTTPException(status_code=404, detail="Unbekannter Auftrag.")
    out = (cmd.stdout or "") + (cmd.stderr or "")
    return {"status": cmd.status, "exit_code": cmd.exit_code,
            "ready": "RDP_READY" in out or "LIVE_READY" in out, "output": out[-3000:]}
