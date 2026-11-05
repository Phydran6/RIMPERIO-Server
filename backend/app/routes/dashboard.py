"""
Datei:        routes/dashboard.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Die Zahlen für die Startseite in einem Aufruf: wie viele Agents,
              wie viele online/offline, offene Alarme, wartende Befehle, und die
              Verteilung nach Plattform und Standort.
"""
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Agent, Alert, Command, User
from ..schemas import DashboardSummary
from ..security import get_current_user
from ..service import get_offline_after, is_online

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    agents = (await session.execute(select(Agent))).scalars().all()
    offline_after = await get_offline_after(session)

    online = sum(1 for a in agents if is_online(a.last_seen, offline_after))
    by_platform = Counter(a.platform or "unbekannt" for a in agents)
    by_site = Counter(a.site or "Standard" for a in agents)

    alerts_active = (
        await session.execute(select(func.count()).select_from(Alert).where(Alert.active.is_(True)))
    ).scalar_one()
    commands_pending = (
        await session.execute(
            select(func.count()).select_from(Command).where(Command.status == "pending")
        )
    ).scalar_one()

    return DashboardSummary(
        agents_total=len(agents),
        agents_online=online,
        agents_offline=len(agents) - online,
        alerts_active=int(alerts_active),
        commands_pending=int(commands_pending),
        by_platform=dict(by_platform),
        by_site=dict(by_site),
    )
