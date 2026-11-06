# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.13.20.58.33
# Beschreibung: RIMPERIO - Health API Endpoints
# ==============================================================================

import time
import os
import asyncio
from datetime import datetime, timedelta
import psutil
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from ..config import settings
from ..database import get_db
from ..models import Log, Agent
from ..schemas import HealthResponse, HealthDetailedResponse
from ..auth import get_current_user
from .. import diskguard, watchdog

router = APIRouter(prefix="/api/health", tags=["Health"])
SERVER_START = time.time()
_HEALTH_CACHE_TTL = int(os.getenv("HEALTH_CACHE_SECONDS", "5"))
_health_cache = None
_health_cache_expires = 0.0
_health_cache_lock = asyncio.Lock()


@router.get("", response_model=HealthResponse)
async def health_check():
    """Lebenszeichen - mit ehrlichem Zustand im Rumpf.

    Der Statuscode bleibt 200, solange der Webserver antwortet: das
    Update-Skript wartet genau darauf und wuerde sonst bei einer kurz
    traegen Datenbank zurueckrollen. Was wirklich los ist, steht in
    `status`/`problems` - frueher stand hier immer "healthy", auch als
    17 Tage lang nichts gespeichert wurde. Wer einen Statuscode braucht
    (Uptime Kuma & Co.), nimmt /api/health/ready.
    """
    state = watchdog.snapshot()
    problems = watchdog.problems()
    return HealthResponse(
        status="degraded" if problems else "healthy",
        version=settings.app_version,
        database=state.get("database"),
        last_log_at=state.get("last_log_at"),
        problems=problems,
    )


@router.get("/ready")
async def health_ready():
    """Fuer externe Monitore: 200 nur, wenn RIMPERIO wirklich sammelt, sonst 503."""
    state = watchdog.snapshot()
    problems = watchdog.problems()
    body = {"status": "degraded" if problems else "healthy", "problems": problems,
            "database": state.get("database"), "last_log_at": state.get("last_log_at"),
            "checked_at": state.get("checked_at"), "version": settings.app_version}
    return JSONResponse(status_code=503 if problems else 200, content=body)


@router.get("/detailed", response_model=HealthDetailedResponse)
async def health_detailed(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    global _health_cache, _health_cache_expires

    now = time.time()
    if _health_cache and now < _health_cache_expires:
        return _health_cache

    async with _health_cache_lock:
        now = time.time()
        if _health_cache and now < _health_cache_expires:
            return _health_cache

        db_ok = True
        logs_total = logs_24h = agents_total = agents_online = 0
        try:
            logs_total = (await db.execute(text(
                "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
            ))).scalar() or 0
            yesterday = datetime.utcnow() - timedelta(hours=24)
            logs_24h = (await db.execute(
                select(func.count(Log.id)).where(Log.timestamp >= yesterday)
            )).scalar() or 0
            agents_total = (await db.execute(select(func.count(Agent.id)))).scalar() or 0
            five_min = datetime.utcnow() - timedelta(minutes=5)
            agents_online = (await db.execute(
                select(func.count(Agent.id)).where(Agent.last_seen >= five_min)
            )).scalar() or 0
        except Exception:
            db_ok = False

        response = HealthDetailedResponse(
            status="healthy" if db_ok else "degraded",
            version=settings.app_version,
            uptime_seconds=time.time() - SERVER_START,
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=psutil.virtual_memory().percent,
            # Dieselbe Rechnung wie der Plattenwaechter (wie `df`, mit der
            # root-Reserve) - sonst zeigten beide verschiedene Zahlen.
            disk_percent=diskguard.usage()["percent"],
            database_connected=db_ok,
            logs_total=logs_total,
            logs_last_24h=logs_24h,
            agents_total=agents_total,
            agents_online=agents_online,
        )

        _health_cache = response
        _health_cache_expires = time.time() + _HEALTH_CACHE_TTL
        return response
