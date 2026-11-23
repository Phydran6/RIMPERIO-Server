# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer Datenbank-Updates mit Probelauf
# ==============================================================================
"""
    GET  /api/db-updates                    Datenbank-Container und Vorgaenge
    GET  /api/db-updates/{id}               ein Vorgang mit Bericht und Verlauf
    POST /api/db-updates                    neu: Analyse → Sicherung → Probelauf   (Admin)
    POST /api/db-updates/{id}/dryrun        Probelauf (mit Anpassungen) wiederholen (Admin)
    POST /api/db-updates/{id}/adopt-ai      KI-Vorschlaege als Anpassungen uebernehmen (Admin)
    POST /api/db-updates/{id}/approve       freigeben → Ausfuehrung               (Admin)
    POST /api/db-updates/{id}/rollback      Rueckweg (Hauptversion)                (Admin)
    POST /api/db-updates/{id}/cancel        abbrechen (nur vor der Ausfuehrung)    (Admin)
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import dbmigrate
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import Agent, DbMigration, User

router = APIRouter(prefix="/api/db-updates", tags=["Datenbank-Updates"])


class CreateIn(BaseModel):
    agent_uid: str
    container: str = Field(..., min_length=1, max_length=255)
    target_image: str = Field("", max_length=400)
    snapshot: bool = True
    auto_execute_minor: bool = False


class Fixup(BaseModel):
    code: str = Field(..., max_length=20000)
    database: str = Field("", max_length=64)
    note: str = Field("", max_length=300)


class DryrunIn(BaseModel):
    fixups: Optional[List[Fixup]] = None


class ApproveIn(BaseModel):
    force: bool = False
    confirm: str = ""


async def _get(db: AsyncSession, mid: int) -> DbMigration:
    m = await db.get(DbMigration, mid)
    if m is None:
        raise HTTPException(status_code=404, detail="Unbekanntes DB-Update.")
    return m


@router.get("")
async def overview(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_user)):
    rows = (await db.execute(select(DbMigration, Agent).join(Agent, Agent.id == DbMigration.agent_id)
                             .order_by(desc(DbMigration.id)).limit(limit))).all()
    return {"candidates": await dbmigrate.candidates(db),
            "items": [dbmigrate.public(m, a, full=False) for m, a in rows],
            "engines": sorted(set(dbmigrate.ENGINES))}


@router.get("/{mid}")
async def detail(mid: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    m = await _get(db, mid)
    return dbmigrate.public(m, await db.get(Agent, m.agent_id))


@router.post("", status_code=201)
async def create(data: CreateIn, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == data.agent_uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Rechner.")
    try:
        m = await dbmigrate.create(db, agent, data.container.strip(), admin.username,
                                   target_image=data.target_image.strip(), snapshot=data.snapshot,
                                   auto_execute_minor=data.auto_execute_minor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return dbmigrate.public(m, agent)


@router.post("/{mid}/dryrun")
async def dryrun(mid: int, data: DryrunIn, db: AsyncSession = Depends(get_db),
                 admin: User = Depends(get_current_admin)):
    m = await _get(db, mid)
    try:
        await dbmigrate.rerun_dryrun(db, m, admin.username,
                                     [f.model_dump() for f in data.fixups] if data.fixups is not None else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dbmigrate.public(m, await db.get(Agent, m.agent_id))


@router.post("/{mid}/adopt-ai")
async def adopt_ai(mid: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    m = await _get(db, mid)
    fixes = ((m.report or {}).get("ai") or {}).get("fixups") or []
    if not fixes:
        raise HTTPException(status_code=400, detail="Die KI hat keine Anpassungen vorgeschlagen.")
    current = list((m.plan or {}).get("fixups") or [])
    try:
        await dbmigrate.rerun_dryrun(db, m, admin.username, current + fixes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dbmigrate.public(m, await db.get(Agent, m.agent_id))


@router.post("/{mid}/approve")
async def approve(mid: int, data: ApproveIn, db: AsyncSession = Depends(get_db),
                  admin: User = Depends(get_current_admin)):
    m = await _get(db, mid)
    if (m.plan or {}).get("path") == "major" and data.confirm != "MIGRATE":
        raise HTTPException(status_code=400, detail="Hauptversions-Update: zur Bestätigung 'MIGRATE' mitschicken.")
    try:
        await dbmigrate.approve(db, m, admin.username, force=data.force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dbmigrate.public(m, await db.get(Agent, m.agent_id))


@router.post("/{mid}/rollback")
async def rollback(mid: int, data: ApproveIn, db: AsyncSession = Depends(get_db),
                   admin: User = Depends(get_current_admin)):
    if data.confirm != "ROLLBACK":
        raise HTTPException(status_code=400, detail="Bestätigung fehlt: 'confirm' muss 'ROLLBACK' lauten.")
    m = await _get(db, mid)
    try:
        await dbmigrate.rollback(db, m, admin.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dbmigrate.public(m, await db.get(Agent, m.agent_id))


@router.post("/{mid}/cancel")
async def cancel(mid: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    m = await _get(db, mid)
    try:
        await dbmigrate.cancel(db, m, admin.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dbmigrate.public(m, await db.get(Agent, m.agent_id))
