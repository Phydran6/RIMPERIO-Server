# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer Meldungen (Mail, Telegram, Webhook/n8n)
# ==============================================================================
"""
Einstellungen -> Benachrichtigungen. Nur fuer Administratoren - hier stehen
Bot-Token und Webhook-Geheimnis.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .. import alerts, diskguard, journal, watchdog
from ..auth import get_current_admin
from ..database import get_db
from ..limiter import client_ip as real_client_ip

logger = logging.getLogger("rimperio.routes.alerts")

router = APIRouter(prefix="/api/alerts", tags=["Benachrichtigungen"])


class ConfigRequest(BaseModel):
    channels: Optional[Dict[str, Dict[str, Any]]] = None
    events: Optional[Dict[str, bool]] = None
    silence_minutes: Optional[int] = None
    flood_per_minute: Optional[int] = None
    agent_offline_minutes: Optional[int] = None
    cooldown_minutes: Optional[int] = None


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Einstellung (ohne Geheimnisse), Ereignisliste und der aktuelle Zustand."""
    config = await alerts.load_config(db)
    await alerts.refresh(db)
    return {
        "config": alerts.public_config(config),
        "events": alerts.describe(),
        "mail_recipients": alerts.recipients_preview(),
        "state": watchdog.snapshot(),
        "disk": {
            "percent": diskguard.usage()["percent"],
            "ingest_stop": diskguard.INGEST_STOP,
            "reserve_present": diskguard.reserve_present(),
        },
    }


@router.put("/config")
async def put_config(data: ConfigRequest, request: Request,
                     db: AsyncSession = Depends(get_db),
                     admin=Depends(get_current_admin)):
    try:
        config = await alerts.save_config(db, data.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await journal.record(
        db, category="settings", event="alerts.changed",
        message="Benachrichtigungen geändert.", actor=admin.username,
        source_ip=real_client_ip(request),
        detail={"channels": {name: part.get("enabled")
                             for name, part in config["channels"].items()}})
    return {"config": alerts.public_config(config)}


@router.post("/test/{channel}")
async def test(channel: str, db: AsyncSession = Depends(get_db),
               admin=Depends(get_current_admin)):
    """Probemeldung ueber genau einen Weg - mit dem gespeicherten Stand."""
    await alerts.refresh(db)
    try:
        result = await alerts.send_test(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    outcome = result.get(channel, "nicht verschickt")
    if outcome != "ok":
        raise HTTPException(status_code=502, detail=outcome)
    logger.info("Probemeldung über %s von %s", channel, admin.username)
    return {"sent": True, "channel": channel}


@router.post("/check")
async def check_now(_=Depends(get_current_admin)):
    """Wachhund sofort laufen lassen (statt bis zur naechsten Minute zu warten)."""
    return {"state": await watchdog.check_once()}
