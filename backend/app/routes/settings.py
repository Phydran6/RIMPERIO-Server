"""
Datei:        routes/settings.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Laufzeiteinstellungen im Browser — keine Datei anfassen. Schwellen
              für Alarme, Check-in-Takt und das Enrollment-Geheimnis für neue
              Agents (jederzeit wechselbar).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings as cfg
from ..database import get_session
from ..models import User
from ..schemas import SettingsOut, ThresholdSettings
from ..security import get_current_user, require_role
from ..service import get_setting, get_thresholds, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class EnrollSecretUpdate(BaseModel):
    secret: str = Field(min_length=8, max_length=256)


@router.get("", response_model=SettingsOut)
async def read_settings(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    th = await get_thresholds(session)
    return SettingsOut(thresholds=th, checkin_interval=cfg.rimperio_checkin_interval, version=cfg.app_version)


@router.put("/thresholds", response_model=ThresholdSettings)
async def update_thresholds(
    data: ThresholdSettings,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ThresholdSettings:
    await set_setting(session, "thresholds", data.model_dump())
    await session.commit()
    return data


@router.get("/enroll-secret")
async def read_enroll_secret(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Das aktuelle Enrollment-Geheimnis — damit die Oberfläche den Installer-
    Befehl für neue Agents zusammenbauen kann."""
    secret = await get_setting(session, "enroll_secret", cfg.rimperio_enroll_secret)
    return {"secret": secret}


@router.post("/enroll-secret")
async def rotate_enroll_secret(
    data: EnrollSecretUpdate,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await set_setting(session, "enroll_secret", data.secret)
    await session.commit()
    return {"status": "ok", "detail": "Enrollment-Geheimnis gewechselt. Bestehende Agents bleiben verbunden."}
