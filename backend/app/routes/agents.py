"""
Datei:        routes/agents.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Die Agents aus Sicht der Oberfläche: auflisten, Einzelheiten
              zeigen, Beschreibung/Standort ändern, löschen, Messwerte der
              letzten Zeit abrufen und Befehle in die Warteschlange legen.
              Alles nur mit angemeldeter Benutzer-Sitzung.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Agent, Command, Telemetry, User
from ..schemas import (
    AgentDetailOut,
    AgentOut,
    AgentUpdate,
    CommandCreate,
    CommandOut,
)
from ..security import get_current_user, require_role
from ..service import get_offline_after, is_online, to_agent_out

router = APIRouter(prefix="/api/agents", tags=["agents"])


async def _get_agent(session: AsyncSession, uid: str) -> Agent:
    agent = (
        await session.execute(select(Agent).where(Agent.agent_uid == uid))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden.")
    return agent


@router.get("", response_model=list[AgentOut])
async def list_agents(
    site: str | None = Query(default=None),
    search: str | None = Query(default=None),
    online: bool | None = Query(default=None),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentOut]:
    stmt = select(Agent).order_by(Agent.hostname)
    if site:
        stmt = stmt.where(Agent.site == site)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(Agent.hostname.ilike(like))
    agents = (await session.execute(stmt)).scalars().all()

    offline_after = await get_offline_after(session)
    out = [to_agent_out(a, offline_after) for a in agents]
    if online is not None:
        out = [a for a in out if a.online == online]
    return out


@router.get("/{uid}", response_model=AgentDetailOut)
async def get_agent(
    uid: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentDetailOut:
    agent = await _get_agent(session, uid)
    offline_after = await get_offline_after(session)
    out = AgentDetailOut.model_validate(agent)
    out.online = is_online(agent.last_seen, offline_after)
    return out


@router.patch("/{uid}", response_model=AgentDetailOut)
async def update_agent(
    uid: str,
    data: AgentUpdate,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> AgentDetailOut:
    agent = await _get_agent(session, uid)
    if data.site is not None:
        agent.site = data.site
    if data.description is not None:
        agent.description = data.description
    await session.commit()
    await session.refresh(agent)
    out = AgentDetailOut.model_validate(agent)
    out.online = is_online(agent.last_seen, await get_offline_after(session))
    return out


@router.delete("/{uid}")
async def delete_agent(
    uid: str,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    agent = await _get_agent(session, uid)
    await session.delete(agent)
    await session.commit()
    return {"status": "ok", "detail": "Agent entfernt."}


@router.get("/{uid}/telemetry")
async def agent_telemetry(
    uid: str,
    limit: int = Query(default=120, ge=1, le=2000),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    agent = await _get_agent(session, uid)
    rows = (
        await session.execute(
            select(Telemetry)
            .where(Telemetry.agent_id == agent.id)
            .order_by(desc(Telemetry.ts))
            .limit(limit)
        )
    ).scalars().all()
    rows.reverse()  # älteste zuerst — bequem fürs Zeichnen
    return [
        {
            "ts": r.ts.isoformat(),
            "cpu_percent": r.cpu_percent,
            "mem_percent": r.mem_percent,
            "mem_used_mb": r.mem_used_mb,
            "mem_total_mb": r.mem_total_mb,
            "uptime_seconds": r.uptime_seconds,
            "disk": r.disk or [],
        }
        for r in rows
    ]


@router.post("/{uid}/commands", response_model=CommandOut, status_code=201)
async def queue_command(
    uid: str,
    data: CommandCreate,
    user: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> CommandOut:
    agent = await _get_agent(session, uid)
    if data.shell not in {"powershell", "cmd", "bash", "sh"}:
        raise HTTPException(status_code=400, detail="Unbekannte Shell.")
    cmd = Command(
        agent_id=agent.id,
        shell=data.shell,
        command=data.command,
        timeout=data.timeout,
        created_by=user.username,
    )
    session.add(cmd)
    await session.commit()
    await session.refresh(cmd)
    return CommandOut.model_validate(cmd)


@router.get("/{uid}/commands", response_model=list[CommandOut])
async def list_commands(
    uid: str,
    limit: int = Query(default=50, ge=1, le=500),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CommandOut]:
    agent = await _get_agent(session, uid)
    rows = (
        await session.execute(
            select(Command)
            .where(Command.agent_id == agent.id)
            .order_by(desc(Command.id))
            .limit(limit)
        )
    ).scalars().all()
    return [CommandOut.model_validate(c) for c in rows]
