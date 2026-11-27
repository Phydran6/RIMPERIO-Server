# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API Lizenz, Geräte-Kontingent, Abrechnung, Abrechnungs-API
# ==============================================================================
"""
`/api/license/…`     Lizenz und Abrechnung in der Oberfläche (Administratoren;
                     `/summary` für jeden angemeldeten Benutzer).
`/api/license/v1/…`  Die Abrechnungs-API für externe Systeme (ERP,
                     Rechnungsprogramm, n8n). Anmeldung mit eigenem Schlüssel
                     `rbk_…` im Kopf `Authorization: Bearer`. Nur lesen.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing, journal, licensing as lic
from ..auth import admin_from_token, get_current_admin, get_current_user
from ..config import settings
from ..database import get_db
from ..limiter import client_ip, limiter
from ..models import LicenseDevice, User

router = APIRouter(prefix="/api/license", tags=["Lizenz & Abrechnung"])


# ==============================================================================
# Schemas
# ==============================================================================
class SourceIn(BaseModel):
    mode: str = Field(pattern="^(github|url|manual)$")
    github_repo: Optional[str] = None
    github_branch: Optional[str] = None
    github_path: Optional[str] = None
    url: Optional[str] = None
    url_token: Optional[str] = None
    clear_url_token: bool = False
    report_usage: Optional[bool] = None
    check_hours: Optional[int] = None


class UploadIn(BaseModel):
    license: Any


class ConfirmIn(BaseModel):
    confirm: str = ""


class DeviceIn(BaseModel):
    billable: Optional[bool] = None
    price_override: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    clear_price: bool = False
    tenant_override: Optional[str] = Field(default=None, max_length=128)
    class_override: Optional[str] = Field(default=None, max_length=32)
    note: Optional[str] = Field(default=None, max_length=1000)


class ApiKeyIn(BaseModel):
    name: str = Field(default="Abrechnung", max_length=100)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


async def _require(db: AsyncSession, feature: str) -> None:
    if not await lic.has_feature(db, feature):
        raise HTTPException(status_code=402, detail=(
            f"Enterprise-Funktion: {lic.FEATURES.get(feature, feature)}. Dafür braucht diese Instanz eine "
            "Lizenz (Lizenz & Abrechnung → Lizenz). Die Überwachung selbst bleibt lizenzfrei."))


def _month(value: Optional[str]) -> str:
    month = value or datetime.utcnow().strftime("%Y-%m")
    try:
        billing.month_bounds(month)
    except ValueError as exc:
        raise _bad(exc)
    return month


# ==============================================================================
# Lizenz
# ==============================================================================
@router.get("/summary")
async def license_summary(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """Kurzfassung - für Kopfzeile, Übersicht und App."""
    return await lic.summary(db)


@router.get("/status")
async def license_status(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    cur = await lic.current(db)
    state = await lic.get_setting(db, lic.STATE_KEY, {})
    src = await lic.get_source(db)
    return {**await lic.summary(db), "instance_id": cur["instance_id"], "license": cur["license"],
            "origin": state.get("origin") or "", "last_check_at": state.get("last_check_at"),
            "last_ok_at": state.get("last_ok_at"), "last_error": state.get("last_error") or "",
            "source": lic.public_source(src), "keys_configured": bool(lic.TRUSTED_KEYS),
            "counts": await lic.counts(db), "models": lic.MODELS, "feature_labels": lic.FEATURES,
            "version": settings.app_version}


@router.get("/request")
async def license_request(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    """Alles, was der Herausgeber zum Ausstellen braucht - zum Kopieren."""
    iid = await lic.instance_id(db)
    src = await lic.get_source(db)
    path = "/".join(p for p in (src.get("github_path") or "").split("/") if p)
    return {"instance_id": iid, "rimperio_version": settings.app_version,
            "license_file": f"{path + '/' if path else ''}{iid}.json",
            "usage": await lic.usage_snapshot(db)}


@router.put("/source")
async def put_source(data: SourceIn, request: Request, db: AsyncSession = Depends(get_db),
                     admin: User = Depends(get_current_admin)):
    current = await lic.get_source(db)
    try:
        cfg = lic.clean_source(data.model_dump(exclude_none=True), current)
    except ValueError as exc:
        raise _bad(exc)
    await lic.put_setting(db, lic.SOURCE_KEY, cfg, "Lizenz: woher die Lizenz kommt")
    await journal.record(db, category="license", event="license.source", level="notice",
                         message=f"Lizenzquelle geändert: {cfg['mode']}"
                                 + (f" ({cfg['github_repo']})" if cfg["mode"] == "github" else "")
                                 + (f" ({cfg['url']})" if cfg["mode"] == "url" else ""),
                         actor=admin.username, source_ip=client_ip(request), commit=False)
    await db.commit()
    return lic.public_source(cfg)


@router.post("/check")
async def check_license(db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Jetzt bei der Lizenzquelle nachfragen."""
    await lic.check_now(db, actor=admin.username)
    return await license_status(db, admin)


@router.post("/upload")
async def upload_license(data: UploadIn, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(get_current_admin)):
    """Lizenzdatei von Hand einspielen (ohne Internet)."""
    try:
        await lic.install_envelope(db, data.license, actor=admin.username)
    except ValueError as exc:
        raise _bad(exc)
    return await license_status(db, admin)


@router.post("/remove")
async def remove_license(data: ConfirmIn, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(get_current_admin)):
    if data.confirm != "ENTFERNEN":
        raise HTTPException(status_code=400, detail="Zur Bestätigung ENTFERNEN eingeben.")
    await lic.remove_license(db, actor=admin.username)
    return await license_status(db, admin)


@router.post("/instance/regenerate")
async def regenerate_instance(data: ConfirmIn, db: AsyncSession = Depends(get_db),
                              admin: User = Depends(get_current_admin)):
    """Neue Kennung - für einen Klon aus derselben Sicherung. Die Lizenz entfällt dabei."""
    if data.confirm != "NEU":
        raise HTTPException(status_code=400, detail="Zur Bestätigung NEU eingeben.")
    iid = await lic.regenerate_instance_id(db)
    await journal.record(db, category="license", event="license.instance.regenerated", level="warning",
                         message=f"Neue Instanz-Kennung {iid} - die bisherige Lizenz gilt hier nicht mehr.",
                         actor=admin.username)
    await lic.sync_devices(db)
    return await license_status(db, admin)


# ==============================================================================
# Geräte
# ==============================================================================
@router.post("/sync")
async def sync_now(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    """Geräte jetzt abgleichen (läuft sonst alle 5 Minuten von selbst)."""
    return {"counts": await lic.sync_devices(db)}


@router.get("/devices")
async def list_devices(state: Optional[str] = Query(None, max_length=16),
                       tenant: Optional[str] = Query(None, max_length=128),
                       q: Optional[str] = Query(None, max_length=100),
                       include_removed: bool = False,
                       db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    stmt = select(LicenseDevice)
    if state:
        stmt = stmt.where(LicenseDevice.state == state)
    elif not include_removed:
        stmt = stmt.where(LicenseDevice.state != "removed")
    if tenant:
        stmt = stmt.where(or_(LicenseDevice.tenant_override == tenant,
                              (LicenseDevice.tenant_override.is_(None)) & (LicenseDevice.tenant == tenant)))
    if q:
        stmt = stmt.where(LicenseDevice.name.ilike(f"%{q}%"))
    rows = (await db.execute(stmt.order_by(LicenseDevice.name))).scalars().all()
    cfg = await billing.get_config(db)
    out = []
    for d in rows:
        item = lic.device_public(d)
        item["unit_price"] = billing.unit_price(cfg, item["tenant"], item["device_class"], d.price_override)
        out.append(item)
    return {"devices": out, "classes": lic.DEVICE_CLASSES, "states": lic.STATES,
            "currency": cfg["currency"]}


@router.patch("/devices/{device_id}")
async def update_device(device_id: int, data: DeviceIn, db: AsyncSession = Depends(get_db),
                        admin: User = Depends(get_current_admin)):
    d = await db.get(LicenseDevice, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Gerät nicht gefunden.")
    changes = []
    if data.billable is not None and data.billable != d.billable:
        d.billable = data.billable
        changes.append("wird abgerechnet" if data.billable else "nicht abrechnen")
    if data.clear_price:
        d.price_override = None
        changes.append("eigener Preis entfernt")
    elif data.price_override is not None:
        d.price_override = round(data.price_override, 4)
        changes.append(f"eigener Preis {d.price_override}")
    if data.tenant_override is not None:
        d.tenant_override = data.tenant_override.strip() or None
        changes.append(f"Mandant {d.tenant_override or '(erkannt)'}")
    if data.class_override is not None:
        cls = data.class_override.strip() or None
        if cls and cls not in lic.DEVICE_CLASSES:
            raise HTTPException(status_code=400, detail="Unbekannte Geräteklasse.")
        d.class_override = cls
        changes.append(f"Klasse {lic.DEVICE_CLASSES.get(cls, '(erkannt)')}")
    if data.note is not None:
        d.note = data.note
    if changes:
        await journal.record(db, category="license", event="license.device", level="info",
                             message=f"{d.name}: " + ", ".join(changes), actor=admin.username,
                             target=d.device_key, commit=False)
    await db.commit()
    await lic.sync_devices(db)
    await db.refresh(d)
    return lic.device_public(d)


# ==============================================================================
# Abrechnung
# ==============================================================================
@router.get("/billing/config")
async def get_billing_config(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    cfg = await billing.get_config(db)
    rows = (await db.execute(select(LicenseDevice.tenant, LicenseDevice.tenant_override)
                             .where(LicenseDevice.state != "removed"))).all()
    known = sorted({(o or t or lic.DEFAULT_TENANT) for t, o in rows} | set(cfg["tenants"]))
    return {"config": cfg, "classes": lic.DEVICE_CLASSES, "methods": billing.METHODS,
            "known_tenants": known, "licensed": await lic.has_feature(db, "billing")}


@router.put("/billing/config")
async def put_billing_config(data: Dict[str, Any], db: AsyncSession = Depends(get_db),
                             admin: User = Depends(get_current_admin)):
    try:
        cfg = await billing.save_config(db, data, admin.username)
    except (ValueError, TypeError) as exc:
        raise _bad(exc)
    await lic.sync_devices(db)
    return {"config": cfg}


@router.get("/billing/report")
async def billing_report(month: Optional[str] = Query(None, max_length=7), live: bool = False,
                         db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    """Vorschau geht immer; Festschreiben und Export brauchen die Lizenz."""
    data = await billing.report(db, _month(month), live=live)
    data["licensed"] = await lic.has_feature(db, "billing")
    return data


@router.get("/billing/report.csv")
async def billing_report_csv(month: Optional[str] = Query(None, max_length=7), detail: bool = False,
                             live: bool = False, token: str = Query("", max_length=2000),
                             authorization: Optional[str] = Header(None),
                             db: AsyncSession = Depends(get_db)):
    """CSV für Excel/Rechnungsprogramm. Im Browser per Ticket (Zweck `license-export`)."""
    admin = None
    if token:
        admin = await admin_from_token(token, purpose="license-export")
    elif authorization and authorization.startswith("Bearer "):
        admin = await admin_from_token(authorization[7:])
    if admin is None:
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich")
    await _require(db, "billing")
    month = _month(month)
    data = await billing.report(db, month, live=live)
    name = f"rimperio-abrechnung-{month}{'-geraete' if detail else ''}.csv"
    return Response(content=billing.to_csv(data, detail=detail), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/billing/statements")
async def list_statements(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    return {"statements": await billing.statements(db)}


@router.post("/billing/statements/{month}")
async def close_statement(month: str, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    """Monat (neu) festschreiben."""
    await _require(db, "billing")
    try:
        return await billing.close_month(db, _month(month), admin.username)
    except ValueError as exc:
        raise _bad(exc)


@router.get("/api-keys")
async def list_api_keys(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    return {"keys": await billing.list_api_keys(db),
            "licensed": await lic.has_feature(db, "billing_api")}


@router.post("/api-keys")
async def create_api_key(data: ApiKeyIn, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(get_current_admin)):
    await _require(db, "billing_api")
    plain, entry = await billing.create_api_key(db, data.name, admin.username)
    return {"key": plain, "entry": entry,
            "hint": "Der Schlüssel wird nur jetzt angezeigt - bitte sofort sicher ablegen."}


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: str, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(get_current_admin)):
    if not await billing.revoke_api_key(db, key_id, admin.username):
        raise HTTPException(status_code=404, detail="Schlüssel nicht gefunden.")
    return {"ok": True}


# ==============================================================================
# Abrechnungs-API für externe Systeme
# ==============================================================================
async def _api_client(request: Request, db: AsyncSession, authorization: Optional[str]) -> dict:
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    entry = await billing.resolve_api_key(db, token)
    if entry is None:
        raise HTTPException(status_code=401, detail="Ungültiger Abrechnungs-Schlüssel",
                            headers={"WWW-Authenticate": "Bearer"})
    await _require(db, "billing_api")
    return entry


@router.get("/v1/status")
@limiter.limit("60/minute")
async def api_status(request: Request, authorization: Optional[str] = Header(None),
                     db: AsyncSession = Depends(get_db)):
    """Lizenz und Gerätezahlen dieser Instanz."""
    await _api_client(request, db, authorization)
    return {"instance_id": await lic.instance_id(db), **await lic.summary(db),
            "usage": await lic.usage_snapshot(db)}


@router.get("/v1/devices")
@limiter.limit("60/minute")
async def api_devices(request: Request, authorization: Optional[str] = Header(None),
                      tenant: Optional[str] = Query(None, max_length=128),
                      db: AsyncSession = Depends(get_db)):
    """Alle gezählten Geräte mit Mandant, Klasse, Zustand und Monatspreis."""
    await _api_client(request, db, authorization)
    cfg = await billing.get_config(db)
    rows = (await db.execute(select(LicenseDevice).where(LicenseDevice.state != "removed")
                             .order_by(LicenseDevice.name))).scalars().all()
    out = []
    for d in rows:
        item = lic.device_public(d)
        if tenant and item["tenant"] != tenant:
            continue
        item["unit_price"] = billing.unit_price(cfg, item["tenant"], item["device_class"], d.price_override)
        out.append(item)
    return {"currency": cfg["currency"], "devices": out}


@router.get("/v1/usage")
@limiter.limit("60/minute")
async def api_usage(request: Request, month: Optional[str] = Query(None, max_length=7),
                    tenant: Optional[str] = Query(None, max_length=128), live: bool = False,
                    format: str = Query("json", pattern="^(json|csv)$"), detail: bool = False,
                    authorization: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)):
    """Monatsabrechnung je Mandant (festgeschrieben, sonst Stand jetzt)."""
    await _api_client(request, db, authorization)
    data = await billing.report(db, _month(month), live=live)
    if tenant:
        data["tenants"] = [t for t in data["tenants"] if t["tenant"] == tenant]
    if format == "csv":
        return Response(content=billing.to_csv(data, detail=detail), media_type="text/csv; charset=utf-8")
    return data
