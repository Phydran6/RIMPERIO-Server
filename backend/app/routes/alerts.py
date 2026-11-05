"""
Datei:        routes/alerts.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Alarme ansehen und von Hand auflösen. Die meisten Alarme lösen
              sich selbst auf, sobald der Grund wegfällt (Agent meldet sich,
              Auslastung fällt). Von Hand auflösen ist für den Rest.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Agent, Alert, User
from ..schemas import AlertOut
from ..security import get_current_user, require_role
from ..service import resolve_alert

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AlertOut]:
    stmt = select(Alert).order_by(desc(Alert.created_at)).limit(limit)
    if active is not None:
        stmt = stmt.where(Alert.active.is_(active))
    rows = (await session.execute(stmt)).scalars().all()
    return [AlertOut.model_validate(a) for a in rows]


@router.post("/{alert_id}/resolve")
async def resolve(
    alert_id: int,
    _: User = Depends(require_role("admin", "operator")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    alert = (
        await session.execute(select(Alert).where(Alert.id == alert_id))
    ).scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alarm nicht gefunden.")
    if alert.agent_id is not None:
        await resolve_alert(session, alert.agent_id, alert.kind)
    else:
        alert.active = False
    await session.commit()
    return {"status": "ok"}
