# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer Sicherung und Wiederherstellung
# ==============================================================================
"""
Alles unter *RIMPERIO-Server -> Sicherung*. Nur fuer Administratoren.

Bewusst zweistufig beim Zurueckspielen: erst `/inspect` (was steckt drin, passt
die Version?), dann `/restore` mit dem, was man davon wirklich will. So sieht
man vor dem Eingriff, was passiert - und nicht danach.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import backup as backup_module
from ..auth import admin_from_token, get_current_admin
from ..config import settings
from ..database import get_db
from ..guard import BackupDecision, ensure_backup_decision

logger = logging.getLogger("rimperio.routes.backup")

router = APIRouter(prefix="/api/backup", tags=["Backup"])

# Groesstmoegliche hochgeladene Sicherung. Ohne Grenze legt ein einziger
# Upload den Server lahm.
MAX_UPLOAD_BYTES = int(1.5 * 1024 * 1024 * 1024)   # 1,5 GB


class CreateRequest(BaseModel):
    scopes: Optional[List[str]] = Field(default=None,
                                        description="Bereiche; leer = Standardumfang")
    passphrase: str = Field(default="", description="Optional: Sicherung verschlüsseln")
    note: str = Field(default="", description="Freier Vermerk")


class RestoreRequest(BaseModel):
    name: str
    scopes: Optional[List[str]] = Field(default=None,
                                        description="Bereiche; leer = alles aus der Sicherung")
    passphrase: str = ""
    mode: str = Field(default="replace", description="replace oder merge")
    force_version_mismatch: bool = Field(
        default=False,
        description="Sicherung trotz neuerer Version zurückspielen (bewusste Entscheidung)")
    confirm: str = Field(..., description="Muss 'RESTORE' lauten")
    # Vor dem Zurueckspielen zaehlt dieselbe Regel wie vor einem Update.
    backup: Optional[BackupDecision] = None


# =============================================================================
# Uebersicht
# =============================================================================
@router.get("/overview")
async def overview(_=Depends(get_current_admin)):
    """Alles, was die Sicherungsseite beim Öffnen braucht."""
    return {
        "server_version": settings.app_version,
        "scopes": backup_module.scope_catalog(),
        "default_scopes": backup_module.DEFAULT_SCOPES,
        "backups": backup_module.list_backups(),
        "storage": backup_module.disk_usage(),
        "format_version": backup_module.FORMAT_VERSION,
    }


@router.get("")
async def list_all(_=Depends(get_current_admin)):
    """Nur die Liste der Sicherungen (neueste zuerst)."""
    return {"backups": backup_module.list_backups()}


# =============================================================================
# Erstellen
# =============================================================================
@router.post("/create")
async def create(data: CreateRequest, db: AsyncSession = Depends(get_db),
                 admin=Depends(get_current_admin)):
    """Legt eine Sicherung an."""
    try:
        manifest = await backup_module.create_backup(
            db,
            scopes=data.scopes,
            passphrase=data.passphrase,
            note=data.note,
            trigger="manual",
            created_by=admin.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Sicherung fehlgeschlagen")
        raise HTTPException(status_code=500, detail=f"Sicherung fehlgeschlagen: {exc}") from exc

    backup_module.prune()
    return manifest


# =============================================================================
# Herunterladen / Hochladen / Loeschen
# =============================================================================
@router.get("/{name}/download")
async def download(name: str,
                   token: str = Query("", description="Download-Ticket (/api/auth/ticket), falls kein Kopf möglich"),
                   authorization: str = Header("")):
    """Sicherung als Datei herunterladen.

    Zwei Wege zur Anmeldung, weil ein Download meist ein schlichter Link ist und
    der Browser dabei keinen `Authorization`-Kopf mitschickt: entweder der Kopf
    (für Aufrufe aus Skripten) oder `?token=` (für den Link in der Oberfläche).
    Geprüft wird in beiden Fällen dasselbe.
    """
    # Link aus der Oberflaeche: nur ein kurzlebiges Ticket, nie der volle
    # Sitzungs-Token (der stuende sonst im Browserverlauf und im Proxy-Log).
    # Skripte schicken weiterhin den normalen Token im Kopf.
    if token.strip():
        admin = await admin_from_token(token.strip(), purpose="backup-download")
    elif authorization.startswith("Bearer "):
        admin = await admin_from_token(authorization[7:].strip())
    else:
        admin = None
    if not admin:
        raise HTTPException(status_code=401,
                            detail="Nicht angemeldet oder keine Administratorrechte.")

    try:
        path = backup_module.backup_path(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/upload")
async def upload(file: UploadFile = File(...), _=Depends(get_current_admin)):
    """Sicherung von einem anderen Server hochladen (noch nicht zurückspielen)."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu groß (max. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    try:
        return backup_module.store_uploaded(content, original_name=file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{name}")
async def remove(name: str, _=Depends(get_current_admin)):
    """Sicherung löschen."""
    try:
        deleted = backup_module.delete_backup(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sicherung '{name}' gibt es nicht.")
    return {"deleted": True, "name": name}


@router.post("/prune")
async def prune(keep: int = Query(0, ge=0, le=200), _=Depends(get_current_admin)):
    """Alte Sicherungen aufräumen (0 = Vorgabe aus RIMPERIO_KEEP_BACKUPS)."""
    return {"removed": backup_module.prune(keep), "storage": backup_module.disk_usage()}


# =============================================================================
# Vorschau und Zurueckspielen
# =============================================================================
@router.get("/{name}/inspect")
async def inspect(name: str, _=Depends(get_current_admin)):
    """Was steckt in der Sicherung — und passt sie zu diesem Server?"""
    try:
        return backup_module.inspect(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/restore")
async def restore(data: RestoreRequest, db: AsyncSession = Depends(get_db),
                  admin=Depends(get_current_admin)):
    """Spielt ausgewählte Bereiche einer Sicherung zurück.

    Vorher läuft — wie bei jedem Systemeingriff — die Sicherungsfrage. Wer
    Benutzer zurückspielt und sich dabei selbst aussperrt, hat dann wenigstens
    den Stand von vorher noch liegen.
    """
    if data.confirm != "RESTORE":
        raise HTTPException(status_code=400,
                            detail="Bestätigung fehlt: 'confirm' muss 'RESTORE' lauten.")

    pre = await ensure_backup_decision(db, data.backup, "Zurückspielen einer Sicherung",
                                       created_by=admin.username)

    try:
        result = await backup_module.restore_backup(
            db,
            name=data.name,
            scopes=data.scopes,
            passphrase=data.passphrase,
            mode=data.mode,
            force_version_mismatch=data.force_version_mismatch,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Zurückspielen fehlgeschlagen")
        raise HTTPException(status_code=500,
                            detail=f"Zurückspielen fehlgeschlagen: {exc}") from exc

    result["pre_backup"] = pre
    result["note"] = ("Betrifft das Zurückspielen Benutzer oder Einstellungen, ist eine "
                      "neue Anmeldung nötig.")
    return result
