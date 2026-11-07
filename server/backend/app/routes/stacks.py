# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer die Zusatzdienste
# ==============================================================================
"""
RIMPERIO-Server -> Zusatzdienste. Ausschliesslich fuer Administratoren.

Ein Dienst ein- oder auszuschalten aendert die laufende Installation. Deshalb
gilt hier dieselbe Regel wie beim Update: vorher wird die Sicherungsfrage
gestellt und beantwortet.

Die Zugangsdaten liegen hinter einem eigenen Endpunkt und nicht in der
Uebersicht - so steht nicht bei jedem Seitenaufruf ein Passwort in der Antwort,
sondern nur, wenn es jemand ausdruecklich sehen will.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, stacks
from ..auth import get_current_admin
from ..database import get_db
from ..events import STACK_CHANGED, bus
from ..guard import BackupDecision, ensure_backup_decision

logger = logging.getLogger("rimperio.routes.stacks")

router = APIRouter(prefix="/api/stacks", tags=["Zusatzdienste"])


class ToggleRequest(BaseModel):
    enable: bool
    confirm: str = Field(..., description="Muss 'CHANGE' lauten")
    backup: Optional[BackupDecision] = None


class PasswordRequest(BaseModel):
    password: str = Field(default="", description="Leer = neues Passwort auswürfeln")


class CapacityRequest(BaseModel):
    stacks: List[str] = Field(default_factory=list)


def _client_host(request: Request) -> str:
    """Der Name, unter dem der Benutzer den Server gerade erreicht.

    Damit stimmen die Links auf die Zusatzdienste: wer über einen FQDN kommt,
    soll nicht auf eine interne IP verwiesen werden.
    """
    forwarded = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or ""
    return forwarded.split(":")[0].strip()


@router.get("")
async def overview(request: Request, _=Depends(get_current_admin)):
    """Welche Zusatzdienste gibt es, was läuft, wie kommt man hin?"""
    return await stacks.overview(host_hint=_client_host(request))


@router.post("/capacity")
async def capacity(data: CapacityRequest, _=Depends(get_current_admin)):
    """Reicht die Maschine für diese Auswahl?"""
    return await stacks.capacity_check(data.stacks)


@router.get("/{stack}/credentials")
async def credentials(stack: str, admin=Depends(get_current_admin)):
    """Zugangsdaten eines Zusatzdienstes — für Administratoren jederzeit einsehbar."""
    try:
        result = await stacks.credentials(stack)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("Zugangsdaten für '%s' von %s eingesehen", stack, admin.username)
    return result


@router.post("/{stack}/password")
async def set_password(stack: str, data: PasswordRequest, admin=Depends(get_current_admin)):
    """Setzt ein neues Passwort für einen Zusatzdienst."""
    try:
        result = await stacks.set_password(stack, data.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.warning("Passwort für '%s' geändert von %s", stack, admin.username)
    return result


@router.get("/{stack}/logs")
async def logs(stack: str, lines: int = Query(200, ge=10, le=2000),
               _=Depends(get_current_admin)):
    """Die letzten Protokollzeilen eines Zusatzdienstes."""
    try:
        return {"lines": await stacks.logs(stack, lines)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{stack}/toggle")
async def toggle(stack: str, data: ToggleRequest, db: AsyncSession = Depends(get_db),
                 admin=Depends(get_current_admin)):
    """Schaltet einen Zusatzdienst ein oder aus.

    Das verändert den laufenden Stack — deshalb vorher die Sicherungsfrage.
    """
    if data.confirm != "CHANGE":
        raise HTTPException(status_code=400,
                            detail="Bestätigung fehlt: 'confirm' muss 'CHANGE' lauten.")

    label = stacks.STACKS.get(stack, {}).get("label", stack)
    pre = await ensure_backup_decision(
        db, data.backup,
        f"{label} {'einschalten' if data.enable else 'ausschalten'}",
        created_by=admin.username)

    try:
        result = await stacks.toggle(stack, data.enable)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result["pre_backup"] = pre
    await journal.record(
        db, category="container", event="stack.toggled", level="warning",
        message=f"Zusatzdienst '{label}' {'eingeschaltet' if data.enable else 'ausgeschaltet'}.",
        actor=admin.username, target=stack,
        detail={"enabled": data.enable, "backup": pre.get("created")})
    bus.publish(STACK_CHANGED, {"stack": stack, "enabled": data.enable}, sticky=False)
    return result


@router.post("/{stack}/restart")
async def restart(stack: str, _=Depends(get_current_admin)):
    """Startet einen Zusatzdienst neu — etwa nach einer Passwortänderung."""
    try:
        return await stacks.restart(stack)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
