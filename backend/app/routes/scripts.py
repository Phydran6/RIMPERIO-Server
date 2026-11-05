"""
Datei:        routes/scripts.py
Projekt:      LRMM
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.11.05.12.00.00

Beschreibung: Skript-Bibliothek. Wiederverwendbare Skripte pflegen und auf einem
              Agent ausführen — der Lauf wird zu einem ganz normalen Befehl in
              der Warteschlange des Agents.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Agent, Command, Script, User
from ..schemas import CommandOut, RunScript, ScriptIn, ScriptOut
from ..security import get_current_user, require_role

router = APIRouter(prefix="/api", tags=["scripts"])


@router.get("/scripts", response_model=list[ScriptOut])
async def list_scripts(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ScriptOut]:
    rows = (await session.execute(select(Script).order_by(Script.category, Script.name))).scalars().all()
    return [ScriptOut.model_validate(s) for s in rows]


@router.post("/scripts", response_model=ScriptOut, status_code=201)
async def create_script(
    data: ScriptIn,
    user: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> ScriptOut:
    if data.shell not in {"powershell", "cmd", "bash", "sh"}:
        raise HTTPException(status_code=400, detail="Unbekannte Shell.")
    script = Script(**data.model_dump(), created_by=user.username)
    session.add(script)
    await session.commit()
    await session.refresh(script)
    return ScriptOut.model_validate(script)


@router.get("/scripts/{script_id}", response_model=ScriptOut)
async def get_script(
    script_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScriptOut:
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Skript nicht gefunden.")
    return ScriptOut.model_validate(script)


@router.put("/scripts/{script_id}", response_model=ScriptOut)
async def update_script(
    script_id: int,
    data: ScriptIn,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> ScriptOut:
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Skript nicht gefunden.")
    for key, value in data.model_dump().items():
        setattr(script, key, value)
    await session.commit()
    await session.refresh(script)
    return ScriptOut.model_validate(script)


@router.delete("/scripts/{script_id}")
async def delete_script(
    script_id: int,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Skript nicht gefunden.")
    await session.delete(script)
    await session.commit()
    return {"status": "ok"}


@router.post("/agents/{uid}/run-script/{script_id}", response_model=CommandOut, status_code=201)
async def run_script(
    uid: str,
    script_id: int,
    data: RunScript,
    user: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> CommandOut:
    agent = (await session.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden.")
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Skript nicht gefunden.")

    cmd = Command(
        agent_id=agent.id,
        shell=script.shell,
        command=script.body,
        timeout=data.timeout or script.default_timeout,
        created_by=user.username,
        source=f"script:{script.name}",
    )
    session.add(cmd)
    await session.commit()
    await session.refresh(cmd)
    return CommandOut.model_validate(cmd)
