# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.10.08.22.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Netzwerksuche API ("Systeme im Netz")
# ==============================================================================
"""
Endpunkte der Netzwerksuche. Alles nur fuer Administratoren: eine Suche
klopft an jede Adresse im Netz, und die Ergebnisliste ist eine Landkarte
des Netzes - beides gehoert nicht in die Hand eines Lesekontos.
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from .. import discovery, journal, onboard
from ..auth import get_current_admin
from ..config import settings
from ..database import get_db
from ..limiter import client_ip as real_client_ip
from ..models import DiscoveredDevice

router = APIRouter(prefix="/api/discovery", tags=["Discovery"])


class RouterEntry(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    community: str = Field("", max_length=100)


class DiscoveryConfig(BaseModel):
    auto_scan: bool = True
    interval_hours: int = Field(24, ge=0, le=24 * 30)
    scan_local: bool = True
    query_gateway: bool = True
    scan_router_subnets: bool = True
    routers: List[RouterEntry] = []
    snmp_community: str = Field("public", max_length=100)
    snmp_probe_hosts: bool = True
    extra_ranges: List[str] = []
    max_hosts: int = Field(4096, ge=1, le=65536)

    @field_validator("extra_ranges")
    @classmethod
    def _check_ranges(cls, value: List[str]) -> List[str]:
        cleaned = []
        for text in value:
            text = (text or "").strip()
            if not text:
                continue
            try:
                discovery.range_size(text)
            except ValueError as exc:
                raise ValueError(f"Bereich „{text}“ ist ungültig: {exc}") from exc
            cleaned.append(text)
        return cleaned


class DeviceUpdate(BaseModel):
    ignored: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=2000)


@router.get("/devices")
async def list_devices(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    nets = await discovery.host_networks()
    return {"items": await discovery.list_devices(db, nets)}


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Laufende Suche, letzter Lauf und was der Server selbst ueber sein Netz weiss."""
    nets = await discovery.host_networks()
    return {
        "scan": discovery.STATE.as_dict(),
        "last_run": await discovery.load_state(db),
        "config": await discovery.load_config(db),
        "host": {
            "host_access": nets.host_access,
            "interfaces": nets.interfaces,
            "gateways": nets.gateways,
            "routes": nets.routes,
        },
        # Fuer die Anleitungen in der Oberflaeche: unter welchem Namen die
        # Agents den Server erreichen.
        "site_url": settings.site_url or None,
        "kinds": discovery.KIND_LABELS,
    }


@router.post("/scan", status_code=202)
async def start_scan(request: Request, admin=Depends(get_current_admin)):
    if not await discovery.start_scan(trigger="manual", actor=admin.username):
        raise HTTPException(status_code=409, detail="Es läuft bereits eine Suche.")
    journal.fire(category="discovery", event="discovery.scan.started",
                 message="Netzwerksuche von Hand gestartet.", actor=admin.username,
                 source_ip=real_client_ip(request))
    return {"started": True, "scan": discovery.STATE.as_dict()}


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    return await discovery.load_config(db)


@router.put("/config")
async def put_config(data: DiscoveryConfig, request: Request,
                     db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    saved = await discovery.save_config(db, data.model_dump())
    # Die Community ist ein (Lese-)Passwort - im Tagebuch steht nur, dass es eine gibt.
    await journal.record(
        db, category="settings", event="discovery.config",
        message="Einstellungen der Netzwerksuche geändert.",
        actor=admin.username, source_ip=real_client_ip(request),
        detail={"auto_scan": saved["auto_scan"], "interval_hours": saved["interval_hours"],
                "extra_ranges": saved["extra_ranges"],
                "routers": [r["host"] for r in saved["routers"]]})
    return saved


@router.put("/devices/{device_id}")
async def update_device(device_id: int, data: DeviceUpdate,
                        db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    device = await db.get(DiscoveredDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="System nicht gefunden")
    if data.ignored is not None:
        device.ignored = data.ignored
    if data.note is not None:
        device.note = data.note.strip() or None
    await db.commit()
    return {"ok": True}


@router.delete("/devices/{device_id}", status_code=204)
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db),
                        _=Depends(get_current_admin)):
    device = await db.get(DiscoveredDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="System nicht gefunden")
    await db.delete(device)
    await db.commit()


class OnboardRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    sudo_password: str = Field("", max_length=256)
    # Der Fingerabdruck, den der Administrator zuvor bestätigt hat.
    fingerprint: str = Field(..., min_length=10, max_length=120)


@router.get("/devices/{device_id}/fingerprint")
async def device_fingerprint(device_id: int, db: AsyncSession = Depends(get_db),
                             _=Depends(get_current_admin)):
    """Holt den SSH-Hostschlüssel zum Bestätigen — noch ohne Anmeldung."""
    device = await db.get(DiscoveredDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="System nicht gefunden")
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(
            None, onboard.read_host_fingerprint, device.ip_address, 22)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Kein SSH-Zugang zu {device.ip_address}: {exc}") from exc
    return {"host": device.ip_address, **info}


@router.post("/devices/{device_id}/onboard", status_code=202)
async def onboard_device(device_id: int, data: OnboardRequest, request: Request,
                         db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    """Bindet ein Linux-System per SSH ein. Zugangsdaten bleiben nur für diesen Vorgang."""
    device = await db.get(DiscoveredDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="System nicht gefunden")

    creds = onboard.Credentials(
        username=data.username.strip(), password=data.password,
        sudo_password=data.sudo_password)
    started = await onboard.start(
        device_id, creds, data.fingerprint.strip(),
        actor=admin.username, source_ip=real_client_ip(request))
    if not started:
        raise HTTPException(status_code=409, detail="Für dieses System läuft bereits ein Einbinden.")
    return {"started": True, "state": onboard.state_for(device_id)}


@router.get("/devices/{device_id}/onboard")
async def onboard_status(device_id: int, _=Depends(get_current_admin)):
    state = onboard.state_for(device_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Für dieses System läuft kein Einbinden.")
    return state


@router.post("/forget")
async def forget_missing(request: Request, days: int = 30,
                         db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    """Systeme entfernen, die seit `days` Tagen keine Suche mehr gefunden hat."""
    if days < 1:
        raise HTTPException(status_code=400, detail="Mindestens ein Tag")
    removed = await discovery.forget_missing(db, days)
    if removed:
        await journal.record(
            db, category="discovery", event="discovery.forget",
            message=f"{removed} nicht mehr gesehene Systeme aus der Liste entfernt (> {days} Tage).",
            actor=admin.username, source_ip=real_client_ip(request))
    return {"removed": removed}
