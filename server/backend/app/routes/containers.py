# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer die Container-Uebersicht und -Updates
# ==============================================================================
"""
Container ansehen und aktualisieren.

Alles hier ist Administratoren vorbehalten - wer einen Container neu erzeugen
kann, kann diesen Server anhalten.

Zwei Dinge sind bewusst getrennt:

* **Pruefen** (``/check``) fragt nur die Registry und veraendert nichts. Das
  darf und soll oft passieren.
* **Einspielen** (``/{component}/update``) holt ein Image und erzeugt genau
  einen Container neu. Das passiert nur auf Klick, verlangt vorher eine
  Antwort auf die Sicherungsfrage und landet im Systemtagebuch.

Es gibt hier keinen Schalter "automatisch aktualisieren". Das ist kein
Versehen: auf einem Log-Server will niemand, dass sich nachts die Datenbank
austauscht und am Morgen keiner weiss, warum.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import containers, journal
from ..auth import get_current_admin
from ..database import get_db
from ..events import STACK_CHANGED, bus
from ..guard import BackupDecision, ensure_backup_decision
from ..limiter import client_ip as real_client_ip

logger = logging.getLogger("rimperio.routes.containers")

router = APIRouter(prefix="/api/containers", tags=["Container"])


class UpdateRequest(BaseModel):
    """Ein Image holen und den Container damit neu erzeugen."""
    confirm: str = Field(..., description="Muss der Name des Containers sein")
    backup: Optional[BackupDecision] = None


class LifecycleRequest(BaseModel):
    action: str = Field(..., pattern="^(start|stop|restart)$")


@router.get("")
async def overview(include_foreign: bool = Query(True, description="Auch fremde Container zeigen"),
                   _=Depends(get_current_admin)):
    """Welche Container laufen hier — und wie wird welcher aktualisiert?"""
    return await containers.inventory(include_foreign=include_foreign)


@router.post("/check")
async def check(force: bool = Query(False, description="Zwischenspeicher übergehen"),
                request: Request = None,
                db: AsyncSession = Depends(get_db),
                admin=Depends(get_current_admin)):
    """Fragt die Registries: liegt für ein fremdes Image etwas Neueres bereit?

    Es wird nichts heruntergeladen und nichts ausgetauscht — nur nachgesehen.
    """
    data = await containers.check_updates(force=force)
    if data.get("available"):
        pending = data.get("updates_pending") or []
        await journal.record(
            db, category="container", event="containers.checked",
            level=("notice" if pending else "info"),
            message=(f"Image-Prüfung: {len(pending)} Update(s) verfügbar."
                     if pending else "Image-Prüfung: alles aktuell."),
            actor=admin.username,
            source_ip=real_client_ip(request) if request else "",
            detail={"pending": pending})
    return data


@router.get("/{name}/logs")
async def container_logs(name: str, lines: int = Query(200, ge=10, le=2000),
                         _=Depends(get_current_admin)):
    """Die letzten Protokollzeilen eines Containers."""
    try:
        return {"name": name, "lines": await containers.logs(name, lines=lines)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{component}/update")
async def update_container(component: str, data: UpdateRequest, request: Request,
                           db: AsyncSession = Depends(get_db),
                           admin=Depends(get_current_admin)):
    """Holt ein neueres Image und erzeugt diesen einen Container damit neu."""
    if data.confirm.strip() != component:
        raise HTTPException(
            status_code=400,
            detail=(f"Bestätigung fehlt: 'confirm' muss '{component}' lauten. "
                    f"Ein Container wird nicht aus Versehen ausgetauscht."))

    inventory = await containers.inventory()
    if not inventory.get("available"):
        raise HTTPException(status_code=409, detail=inventory.get("reason", "Kein Host-Zugriff."))

    target = next((item for item in inventory["containers"]
                   if item["component"] == component), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Container '{component}' gibt es hier nicht.")
    if target["update_path"] != "image":
        raise HTTPException(
            status_code=409,
            detail=("Dieser Container wird aus dem Quellcode gebaut — es gibt kein Image "
                    "zu holen. Aktualisiert wird er unter RIMPERIO-Server → Updates."))

    # Erst sichern, dann anfassen. Besonders bei PostgreSQL ist das keine
    # Formalie: ein Major-Upgrade ohne Sicherung ist ein Wagnis.
    pre = await ensure_backup_decision(
        db, data.backup, f"Container-Update ({component})", created_by=admin.username)

    try:
        result = await containers.pull_and_recreate(component)
    except (ValueError, RuntimeError) as exc:
        await journal.record(
            db, category="container", event="container.update.failed", level="error",
            message=f"Update von '{component}' fehlgeschlagen: {exc}",
            actor=admin.username, source_ip=real_client_ip(request), target=component, ok=False)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await journal.record(
        db, category="container", event="container.updated", level="warning",
        message=f"Container '{component}' mit einem neueren Image neu erzeugt.",
        actor=admin.username, source_ip=real_client_ip(request), target=component,
        detail={"image": target["image"], "backup": pre.get("created")})
    bus.publish(STACK_CHANGED, {"component": component, "action": "updated"}, sticky=False)

    result["pre_backup"] = pre
    return result


@router.post("/{component}/lifecycle")
async def container_lifecycle(component: str, data: LifecycleRequest, request: Request,
                              db: AsyncSession = Depends(get_db),
                              admin=Depends(get_current_admin)):
    """Startet, stoppt oder startet einen Container neu."""
    try:
        result = await containers.lifecycle(component, data.action)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await journal.record(
        db, category="container", event=f"container.{data.action}", level="warning",
        message=f"Container '{component}': {data.action}.",
        actor=admin.username, source_ip=real_client_ip(request), target=component)
    bus.publish(STACK_CHANGED, {"component": component, "action": data.action}, sticky=False)
    return result


@router.post("/prune-images")
async def prune_images(request: Request, db: AsyncSession = Depends(get_db),
                       admin=Depends(get_current_admin)):
    """Räumt Images weg, die kein Container mehr benutzt.

    Nach ein paar Updates liegen die alten Stände sonst als Karteileichen herum
    und fressen genau den Platz, den der Plattenwächter gerade freigeräumt hat.
    """
    try:
        result = await containers.prune_images()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await journal.record(
        db, category="container", event="images.pruned",
        message=f"Ungenutzte Images weggeräumt. {result['message']}",
        actor=admin.username, source_ip=real_client_ip(request),
        detail={"reclaimed": result.get("reclaimed", "")})
    return result
