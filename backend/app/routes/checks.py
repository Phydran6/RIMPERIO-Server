"""
Datei:        routes/checks.py
Projekt:      LRMM
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.11.05.12.00.00

Beschreibung: Geplante Checks (Überwachung). Definieren, welche Prüfung wo gilt
              (global / je Standort / je Agent) und wie oft sie läuft. Die Agents
              führen sie selbst aus; hier werden sie verwaltet und die letzten
              Ergebnisse gezeigt.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Agent, Check, CheckResult, User
from ..schemas import CheckIn, CheckOut, CheckResultOut
from ..security import get_current_user, require_role

router = APIRouter(prefix="/api/checks", tags=["checks"])

CHECK_TYPES = {"cpu", "mem", "disk", "service", "port", "ping", "script"}
SCOPES = {"global", "site", "agent"}


def _validate(data: CheckIn) -> None:
    if data.type not in CHECK_TYPES:
        raise HTTPException(status_code=400, detail=f"Typ muss eine von {sorted(CHECK_TYPES)} sein.")
    if data.scope_type not in SCOPES:
        raise HTTPException(status_code=400, detail="scope_type: global | site | agent.")
    if data.scope_type != "global" and not data.scope_value:
        raise HTTPException(status_code=400, detail="Für site/agent fehlt scope_value.")


@router.get("", response_model=list[CheckOut])
async def list_checks(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CheckOut]:
    rows = (await session.execute(select(Check).order_by(Check.name))).scalars().all()
    return [CheckOut.model_validate(c) for c in rows]


@router.post("", response_model=CheckOut, status_code=201)
async def create_check(
    data: CheckIn,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> CheckOut:
    _validate(data)
    check = Check(**data.model_dump())
    session.add(check)
    await session.commit()
    await session.refresh(check)
    return CheckOut.model_validate(check)


@router.put("/{check_id}", response_model=CheckOut)
async def update_check(
    check_id: int,
    data: CheckIn,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> CheckOut:
    _validate(data)
    check = await session.get(Check, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Check nicht gefunden.")
    for key, value in data.model_dump().items():
        setattr(check, key, value)
    await session.commit()
    await session.refresh(check)
    return CheckOut.model_validate(check)


@router.delete("/{check_id}")
async def delete_check(
    check_id: int,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    check = await session.get(Check, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Check nicht gefunden.")
    await session.delete(check)
    await session.commit()
    return {"status": "ok"}


@router.get("/results", response_model=list[CheckResultOut])
async def list_results(
    status: str | None = Query(default=None),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CheckResultOut]:
    stmt = (
        select(CheckResult, Check, Agent)
        .join(Check, Check.id == CheckResult.check_id)
        .join(Agent, Agent.id == CheckResult.agent_id)
        .order_by(CheckResult.status.desc(), Agent.hostname)
    )
    if status:
        stmt = stmt.where(CheckResult.status == status)
    rows = (await session.execute(stmt)).all()
    return [
        CheckResultOut(
            check_id=cr.check_id,
            agent_uid=ag.agent_uid,
            hostname=ag.hostname,
            name=ck.name,
            type=ck.type,
            status=cr.status,
            value=cr.value,
            output=cr.output,
            ts=cr.ts,
        )
        for cr, ck, ag in rows
    ]
