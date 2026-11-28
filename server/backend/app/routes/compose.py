# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API Docker-Compose-Stacks auf Rechnern (app/compose.py)
# ==============================================================================
"""`/api/rmm/agents/{uid}/stacks/…` - Compose-Projekte eines Rechners lesen,
bearbeiten (YAML, .env), pruefen, ausrollen. Lesen und Schreiben nur fuer
Administratoren: wer die Compose-Datei aendert, bestimmt, was auf dem Rechner laeuft."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import compose, journal
from ..auth import get_current_admin
from ..database import get_db
from ..models import Agent, User

router = APIRouter(prefix="/api/rmm", tags=["Docker-Stacks"])


class SaveIn(BaseModel):
    path: str = Field(min_length=2, max_length=1000)
    content: str = Field(max_length=compose.MAX_FILE)
    deploy: bool = False
    pull: bool = False
    rollback: bool = True


class ActionIn(BaseModel):
    action: str = Field(pattern="^(up|pull_up|restart|stop|down)$")


async def _agent(db: AsyncSession, uid: str) -> Agent:
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    return agent


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/docker-hosts")
async def docker_hosts(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> List[dict]:
    """Rechner, auf denen der letzte Docker-Scan Docker gefunden hat."""
    rows = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)).order_by(Agent.hostname))).scalars().all()
    out = []
    for a in rows:
        inv = a.docker_inventory or {}
        if not inv.get("available"):
            continue
        cont = inv.get("containers") or []
        projects = sorted({c.get("compose_project") for c in cont if c.get("compose_project")})
        out.append({"agent_uid": a.agent_uid, "hostname": a.hostname, "platform": a.platform,
                    "compose": bool(inv.get("compose")), "containers": len(cont), "projects": projects,
                    "updates": sum(1 for c in cont if c.get("update_available"))})
    return out


@router.get("/agents/{uid}/stacks")
async def list_stacks(uid: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)):
    try:
        return await compose.list_stacks(db, await _agent(db, uid), user.username)
    except compose.ComposeError as exc:
        raise _bad(exc) from exc


@router.get("/agents/{uid}/stacks/{project}")
async def read_stack(uid: str, project: str, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)):
    try:
        return await compose.read_stack(db, await _agent(db, uid), user.username, project)
    except compose.ComposeError as exc:
        raise _bad(exc) from exc


@router.put("/agents/{uid}/stacks/{project}")
async def save_stack(uid: str, project: str, data: SaveIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)):
    agent = await _agent(db, uid)
    try:
        res = await compose.save_stack(db, agent, user.username, project, data.path, data.content,
                                       deploy=data.deploy, pull=data.pull, rollback=data.rollback)
    except compose.ComposeError as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="compose.saved", actor=user.username, target=agent.hostname,
                 ok=res["ok"], message=f"Stack {project} auf {agent.hostname}: {data.path} "
                 + ("gespeichert und ausgerollt" if res.get("deployed") else "gespeichert" if res.get("saved") else "nicht gespeichert")
                 + (" – alter Stand zurückgeholt" if res.get("rolled_back") else ""))
    return res


@router.post("/agents/{uid}/stacks/{project}/action")
async def stack_action(uid: str, project: str, data: ActionIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)):
    agent = await _agent(db, uid)
    try:
        res = await compose.stack_action(db, agent, user.username, project, data.action)
    except compose.ComposeError as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="compose.action", actor=user.username, target=agent.hostname, ok=res["ok"],
                 message=f"Stack {project} auf {agent.hostname}: {data.action}")
    return res


@router.get("/agents/{uid}/stacks/{project}/backups")
async def stack_backups(uid: str, project: str, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)):
    try:
        return await compose.backups(db, await _agent(db, uid), user.username, project)
    except compose.ComposeError as exc:
        raise _bad(exc) from exc
