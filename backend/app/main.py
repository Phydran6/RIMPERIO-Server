"""
Datei:        main.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Einstiegspunkt des Backends. Baut die FastAPI-App zusammen, legt
              beim ersten Start Tabellen und Erst-Admin an, startet einen
              Hintergrunddienst (Offline-Erkennung + Aufräumen alter Messwerte)
              und hängt alle Routen ein.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, select

from .config import settings
from .database import SessionLocal, init_db
from .models import Telemetry, User
from .routes import (
    agents,
    alerts,
    auth,
    checks,
    dashboard,
    health,
    ingest,
    integrations,
    patches,
    scripts,
    settings as settings_route,
    users,
)
from .security import hash_password
from .service import sweep_offline

TELEMETRY_RETENTION_DAYS = 14
SWEEP_INTERVAL_SECONDS = 60


async def ensure_admin() -> None:
    async with SessionLocal() as session:
        exists = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if exists is None:
            session.add(
                User(
                    username=settings.rimperio_admin_user,
                    password_hash=hash_password(settings.rimperio_admin_password),
                    role="admin",
                )
            )
            await session.commit()


async def _background_loop() -> None:
    """Läuft, solange der Server läuft: Offline-Erkennung und Datenpflege."""
    while True:
        try:
            async with SessionLocal() as session:
                await sweep_offline(session)
                cutoff = datetime.now(timezone.utc) - timedelta(days=TELEMETRY_RETENTION_DAYS)
                await session.execute(delete(Telemetry).where(Telemetry.ts < cutoff))
                await session.commit()
        except Exception:
            # Ein Fehler im Hintergrund darf den Dienst nicht beenden.
            pass
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_admin()
    task = asyncio.create_task(_background_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="RIMPERIO API",
    version=settings.app_version,
    description="Schlankes, selbst-gehostetes Remote Monitoring & Management.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Hinter Caddy läuft alles unter einer Herkunft; CORS ist nur für die lokale
# Entwicklung (Vite auf 5173) offen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    health, auth, ingest, agents, dashboard, alerts, users, settings_route,
    scripts, checks, patches, integrations,
):
    app.include_router(module.router)


@app.get("/api", tags=["meta"])
async def api_root() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/api/docs",
        "health": "/api/health",
    }
