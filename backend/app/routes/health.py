"""
Datei:        routes/health.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Gesundheitsprüfung. Offen und ohne Anmeldung — für Reverse Proxy,
              Monitoring und den Installer, um zu sehen, ob der Server lebt.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.app_version,
        "name": settings.app_name,
        "database": "ok" if db_ok else "nicht erreichbar",
        "time": datetime.now(timezone.utc).isoformat(),
    }
