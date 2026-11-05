"""
Datei:        routes/patches.py
Projekt:      LRMM
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.11.05.12.00.00

Beschreibung: Patchmanagement. Suche nach verfügbaren Updates anstoßen, Updates
              einspielen und den letzten Stand je Agent zeigen. Suche und
              Einspielen laufen über die normale Befehls-Warteschlange — mit
              einer besonderen Art (kind), die der Agent nativ behandelt.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Agent, Command, PatchReport, User
from ..schemas import CommandOut, PatchReportOut
from ..security import get_current_user, require_role

router = APIRouter(prefix="/api", tags=["patches"])


async def _agent(session: AsyncSession, uid: str) -> Agent:
    agent = (await session.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden.")
    return agent


@router.get("/agents/{uid}/patches", response_model=PatchReportOut)
async def agent_patches(
    uid: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PatchReportOut:
    agent = await _agent(session, uid)
    report = (
        await session.execute(select(PatchReport).where(PatchReport.agent_id == agent.id))
    ).scalar_one_or_none()
    if report is None:
        return PatchReportOut(count=0, updates=[])
    return PatchReportOut.model_validate(report)


@router.post("/agents/{uid}/patches/scan", response_model=CommandOut, status_code=201)
async def scan_patches(
    uid: str,
    user: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> CommandOut:
    agent = await _agent(session, uid)
    cmd = Command(
        agent_id=agent.id, shell="", command="", kind="patch_scan",
        timeout=900, created_by=user.username, source="patch",
    )
    session.add(cmd)
    await session.commit()
    await session.refresh(cmd)
    return CommandOut.model_validate(cmd)


@router.post("/agents/{uid}/patches/apply", response_model=CommandOut, status_code=201)
async def apply_patches(
    uid: str,
    user: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> CommandOut:
    agent = await _agent(session, uid)
    cmd = Command(
        agent_id=agent.id, shell="", command="", kind="patch_apply",
        timeout=7200, created_by=user.username, source="patch",
    )
    session.add(cmd)
    await session.commit()
    await session.refresh(cmd)
    return CommandOut.model_validate(cmd)


@router.get("/patches")
async def patches_overview(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            select(PatchReport, Agent).join(Agent, Agent.id == PatchReport.agent_id)
        )
    ).all()
    return [
        {
            "agent_uid": ag.agent_uid,
            "hostname": ag.hostname,
            "platform": ag.platform,
            "count": rep.count,
            "scanned_at": rep.scanned_at.isoformat() if rep.scanned_at else None,
        }
        for rep, ag in rows
    ]
