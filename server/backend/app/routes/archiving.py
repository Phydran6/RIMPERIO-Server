# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.16.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Archivierung: Einstellungen, Test, Lauf, Historie
# ==============================================================================

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import archiving
from ..auth import get_current_admin
from ..database import get_db
from ..models import User

router = APIRouter(prefix="/api/archiving", tags=["Archivierung"])

PROTOCOLS = ("ftp", "ftps", "sftp", "smb", "local")


class ArchivingConfigUpdate(BaseModel):
    enabled: bool = False
    protocol: str = "sftp"
    host: str = ""
    port: int = Field(default=0, ge=0, le=65535)
    username: str = ""
    # Leer lassen = gespeichertes Passwort behalten.
    password: str = ""
    remote_path: str = "/rimperio"
    share: str = ""
    domain: str = ""
    age_days: int = Field(default=90, ge=1, le=3650)
    delete_after: bool = False
    schedule_hour: int = Field(default=3, ge=-1, le=23)
    verify_cert: bool = True


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db), _admin: User = Depends(get_current_admin)):
    """GET /api/archiving/config - Einstellungen ohne Passwort."""
    return archiving.public_config(await archiving.load_config(db))


@router.put("/config")
async def update_config(
    data: ArchivingConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """PUT /api/archiving/config - Einstellungen speichern."""
    if data.protocol not in PROTOCOLS:
        raise HTTPException(400, f"Unbekanntes Protokoll. Erlaubt: {', '.join(PROTOCOLS)}")

    current = await archiving.load_config(db)
    config = {**archiving.DEFAULT_CONFIG, **current, **data.model_dump()}
    if not data.password:
        config["password"] = current.get("password", "")

    if config["enabled"]:
        if config["protocol"] != "local" and not config["host"].strip():
            raise HTTPException(400, "Zielserver fehlt")
        if config["protocol"] == "smb" and not config["share"].strip():
            raise HTTPException(400, "Für SMB muss die Freigabe angegeben werden")
        if not config["remote_path"].strip():
            raise HTTPException(400, "Zielordner fehlt")

    await archiving.save_config(db, config)
    return archiving.public_config(config)


@router.post("/test")
async def test_connection(db: AsyncSession = Depends(get_db), _admin: User = Depends(get_current_admin)):
    """POST /api/archiving/test - legt eine Testdatei am Ziel ab (vorher speichern)."""
    return await archiving.test_target(await archiving.load_config(db))


@router.post("/run")
async def run_now(db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """POST /api/archiving/run - Archivierung sofort ausführen.

    Läuft bewusst im Vordergrund: der Aufrufer soll das Ergebnis sehen. Bei sehr
    großen Datenmengen kann das dauern - der Zeitplan ist dafür der bessere Weg.
    """
    config = await archiving.load_config(db)
    return await archiving.run_archiving(db, config, triggered_by=f"manuell ({admin.username})")


@router.get("/history")
async def history(db: AsyncSession = Depends(get_db), _admin: User = Depends(get_current_admin)):
    """GET /api/archiving/history - die letzten Läufe."""
    return {"items": await archiving.load_history(db)}
