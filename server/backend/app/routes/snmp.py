# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API SNMP-Ueberwachung (Geraete, Zugangsdaten, Vorlagen)
# ==============================================================================
"""
`/api/snmp/…` - Lesen darf jeder angemeldete Benutzer, einrichten nur
Administratoren. Community und v3-Passwoerter werden nie ausgeliefert.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, snmpmon
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import (Agent, DiscoveredDevice, RmmAlert, SnmpCredential, SnmpDevice, SnmpMetric,
                      SnmpSample, SnmpTemplate, User)

router = APIRouter(prefix="/api/snmp", tags=["SNMP"])

AUTH_PROTOS = {"none", "md5", "sha", "sha224", "sha256", "sha384", "sha512"}
PRIV_PROTOS = {"none", "des", "aes", "aes192", "aes256", "aes192c", "aes256c"}


class CredentialIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="2c", pattern="^(1|2c|3)$")
    community: str = Field(default="", description="Leer lassen = unverändert")
    username: str = ""
    auth_proto: str = "none"
    auth_pass: str = Field(default="", description="Leer lassen = unverändert")
    priv_proto: str = "none"
    priv_pass: str = Field(default="", description="Leer lassen = unverändert")
    context: str = ""
    port: int = Field(default=161, ge=1, le=65535)


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    credential_id: Optional[int] = None
    poller: str = "server"
    site: str = "Standard"
    templates: List[str] = Field(default_factory=lambda: ["system", "interfaces"])
    custom: List[Dict[str, Any]] = Field(default_factory=list)
    overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    interval_sec: int = Field(default=300, ge=30, le=86400)
    enabled: bool = True
    discovered_device_id: Optional[int] = None


class ProbeIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    credential_id: Optional[int] = None


class TemplateIn(BaseModel):
    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=128)
    vendor: str = ""
    description: str = ""
    probe: Optional[str] = None
    metrics: List[Dict[str, Any]] = Field(min_length=1)


def _check_cred(data: CredentialIn) -> None:
    if data.version == "3":
        if not data.username:
            raise HTTPException(status_code=422, detail="Für SNMPv3 fehlt der Benutzername.")
        if data.auth_proto not in AUTH_PROTOS or data.priv_proto not in PRIV_PROTOS:
            raise HTTPException(status_code=422, detail="Unbekanntes Verfahren für Authentisierung/Verschlüsselung.")
        if data.priv_proto != "none" and data.auth_proto == "none":
            raise HTTPException(status_code=422, detail="Verschlüsselung geht nur mit Authentisierung.")


async def _poller_ok(db: AsyncSession, poller: str) -> None:
    if poller == "server":
        return
    if (await db.execute(select(Agent.id).where(Agent.agent_uid == poller))).first() is None:
        raise HTTPException(status_code=422, detail="Poller: 'server' oder die Kennung eines RIMPERIO-Agents.")


async def _validate_device(db: AsyncSession, data: DeviceIn) -> List[dict]:
    await _poller_ok(db, data.poller)
    if data.credential_id and await db.get(SnmpCredential, data.credential_id) is None:
        raise HTTPException(status_code=422, detail="Unbekannte Zugangsdaten.")
    catalog = await snmpmon.all_templates(db)
    unknown = [k for k in data.templates if k not in catalog]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unbekannte Vorlage(n): {', '.join(unknown)}")
    try:
        return [snmpmon.validate_metric(m, "custom.") for m in data.custom]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ==============================================================================
# Übersicht und Geräte
# ==============================================================================
@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    by_status = dict((await db.execute(select(SnmpDevice.status, func.count())
                                       .group_by(SnmpDevice.status))).all())
    alerts = (await db.execute(select(func.count()).select_from(RmmAlert).where(
        RmmAlert.agent_id.is_(None), RmmAlert.kind.like("snmp:%"), RmmAlert.active.is_(True)))).scalar_one()
    return {"devices": sum(by_status.values()), "by_status": by_status, "alerts_active": alerts}


@router.get("/devices")
async def list_devices(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    devices = (await db.execute(select(SnmpDevice).order_by(SnmpDevice.site, SnmpDevice.name))).scalars().all()
    metrics: Dict[int, List[SnmpMetric]] = {}
    for m in (await db.execute(select(SnmpMetric).where(SnmpMetric.status.in_(("warning", "critical")))
                               .order_by(SnmpMetric.device_id))).scalars():
        metrics.setdefault(m.device_id, []).append(m)
    out = []
    for d in devices:
        row = snmpmon.public_device(d)
        row["problems"] = [snmpmon.public_metric(m) for m in metrics.get(d.id, [])][:6]
        out.append(row)
    return out


@router.get("/devices/{device_id}")
async def get_device(device_id: int, db: AsyncSession = Depends(get_db),
                     _: User = Depends(get_current_user)) -> dict:
    d = await db.get(SnmpDevice, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Gerät.")
    metrics = (await db.execute(select(SnmpMetric).where(SnmpMetric.device_id == d.id)
                                .order_by(SnmpMetric.group, SnmpMetric.key))).scalars().all()
    alerts = (await db.execute(select(RmmAlert).where(
        RmmAlert.agent_id.is_(None), RmmAlert.kind.like(f"snmp:{d.id}:%"))
        .order_by(desc(RmmAlert.created_at)).limit(50))).scalars().all()
    out = snmpmon.public_device(d, metrics)
    out["alerts"] = [{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message,
                      "active": a.active, "created_at": snmpmon._iso(a.created_at),
                      "resolved_at": snmpmon._iso(a.resolved_at)} for a in alerts]
    return out


@router.get("/devices/{device_id}/history")
async def history(device_id: int, key: str, hours: int = Query(24, ge=1, le=24 * 14),
                  db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (await db.execute(select(SnmpSample.ts, SnmpSample.value).where(
        SnmpSample.device_id == device_id, SnmpSample.key == key, SnmpSample.ts >= since)
        .order_by(SnmpSample.ts))).all()
    return [{"ts": snmpmon._iso(ts), "value": v} for ts, v in rows]


@router.post("/devices", status_code=201)
async def create_device(data: DeviceIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    custom = await _validate_device(db, data)
    d = SnmpDevice(**{**data.model_dump(), "custom": custom}, status="unknown", state={}, system={})
    db.add(d)
    await db.commit()
    await db.refresh(d)
    journal.fire(category="rmm", event="snmp.device.created", actor=user.username, target=d.name,
                 message=f"SNMP-Gerät {d.name} ({d.host}) angelegt, Poller {d.poller}.")
    if d.poller == "server":
        await snmpmon.poll_device(db, d, await snmpmon.all_templates(db))
    return snmpmon.public_device(d)


@router.put("/devices/{device_id}")
async def update_device(device_id: int, data: DeviceIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    d = await db.get(SnmpDevice, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Gerät.")
    custom = await _validate_device(db, data)
    for k, v in {**data.model_dump(), "custom": custom}.items():
        setattr(d, k, v)
    await db.commit()
    journal.fire(category="rmm", event="snmp.device.updated", actor=user.username, target=d.name,
                 message=f"SNMP-Gerät {d.name} geändert.")
    return snmpmon.public_device(d)


@router.delete("/devices/{device_id}")
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    d = await db.get(SnmpDevice, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Gerät.")
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(RmmAlert).where(RmmAlert.agent_id.is_(None),
                                                RmmAlert.kind.like(f"snmp:{d.id}:%")))
    await db.delete(d)
    await db.commit()
    journal.fire(category="rmm", event="snmp.device.deleted", actor=user.username, target=d.name,
                 message=f"SNMP-Gerät {d.name} entfernt.")
    return {"status": "ok"}


@router.post("/devices/{device_id}/poll")
async def poll_now(device_id: int, db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_admin)) -> dict:
    d = await db.get(SnmpDevice, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Gerät.")
    if d.poller != "server":
        d.state = {**(d.state or {}), "_force": datetime.utcnow().isoformat()}
        await db.commit()
        return {"queued": True, "message": "Der Agent fragt beim nächsten Check-in ab (≤ 1 Minute)."}
    raw = await snmpmon.poll_device(db, d, await snmpmon.all_templates(db))
    return {"ok": raw.get("ok"), "error": raw.get("error"), "rtt_ms": raw.get("rtt_ms")}


@router.post("/probe")
async def probe(data: ProbeIn, db: AsyncSession = Depends(get_db),
                _: User = Depends(get_current_admin)) -> dict:
    """Vom Server aus testen: erreichbar, was für ein Gerät, welche Vorlagen passen."""
    cred = await db.get(SnmpCredential, data.credential_id) if data.credential_id else None
    try:
        kw = snmpmon.client_kwargs(cred, data.port)
        return await snmpmon.probe(data.host, kw, await snmpmon.all_templates(db))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/discovered")
async def discovered(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> List[dict]:
    """Systeme aus der Netzwerksuche, die SNMP sprechen und noch nicht überwacht werden."""
    known = {h for (h,) in (await db.execute(select(SnmpDevice.host))).all()}
    rows = (await db.execute(select(DiscoveredDevice).where(DiscoveredDevice.ignored.is_(False))
                             .order_by(DiscoveredDevice.ip_address))).scalars().all()
    out = []
    for r in rows:
        details = r.details or {}
        has_snmp = bool(details.get("snmp")) or 161 in (r.open_ports or [])
        if r.ip_address in known or not has_snmp:
            continue
        out.append({"id": r.id, "ip": r.ip_address, "hostname": r.hostname, "kind": r.kind,
                    "os_guess": r.os_guess, "snmp": details.get("snmp")})
    return out


# ==============================================================================
# Zugangsdaten
# ==============================================================================
@router.get("/credentials")
async def list_credentials(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    return [snmpmon.public_credential(c) for c in
            (await db.execute(select(SnmpCredential).order_by(SnmpCredential.name))).scalars()]


@router.post("/credentials", status_code=201)
async def create_credential(data: CredentialIn, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_admin)) -> dict:
    _check_cred(data)
    c = SnmpCredential(**data.model_dump())
    if c.version != "3" and not c.community:
        c.community = "public"
    db.add(c)
    await db.commit()
    await db.refresh(c)
    journal.fire(category="rmm", event="snmp.credential.created", actor=user.username,
                 message=f"SNMP-Zugangsdaten „{c.name}“ (v{c.version}) angelegt.")
    return snmpmon.public_credential(c)


@router.put("/credentials/{cred_id}")
async def update_credential(cred_id: int, data: CredentialIn, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_admin)) -> dict:
    c = await db.get(SnmpCredential, cred_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Unbekannte Zugangsdaten.")
    _check_cred(data)
    for k, v in data.model_dump().items():
        if k in ("community", "auth_pass", "priv_pass") and not v:
            continue
        setattr(c, k, v)
    await db.commit()
    return snmpmon.public_credential(c)


@router.delete("/credentials/{cred_id}")
async def delete_credential(cred_id: int, db: AsyncSession = Depends(get_db),
                            _: User = Depends(get_current_admin)) -> dict:
    c = await db.get(SnmpCredential, cred_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Unbekannte Zugangsdaten.")
    used = (await db.execute(select(func.count()).select_from(SnmpDevice)
                             .where(SnmpDevice.credential_id == cred_id))).scalar_one()
    if used:
        raise HTTPException(status_code=409, detail=f"Noch {used} Gerät(e) nutzen diese Zugangsdaten.")
    await db.delete(c)
    await db.commit()
    return {"status": "ok"}


# ==============================================================================
# Vorlagen
# ==============================================================================
@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    return list((await snmpmon.all_templates(db)).values())


@router.post("/templates", status_code=201)
async def create_template(data: TemplateIn, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_admin)) -> dict:
    catalog = await snmpmon.all_templates(db)
    if data.key in catalog:
        raise HTTPException(status_code=409, detail="Diesen Schlüssel gibt es schon.")
    try:
        metrics = [snmpmon.validate_metric(m) for m in data.metrics]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    t = SnmpTemplate(key=data.key, name=data.name, vendor=data.vendor, description=data.description,
                     match=[data.probe] if data.probe else [], metrics=metrics)
    db.add(t)
    await db.commit()
    journal.fire(category="rmm", event="snmp.template.created", actor=user.username,
                 message=f"SNMP-Vorlage „{t.name}“ angelegt.")
    return (await snmpmon.all_templates(db))[t.key]


@router.put("/templates/{key}")
async def update_template(key: str, data: TemplateIn, db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_admin)) -> dict:
    t = (await db.execute(select(SnmpTemplate).where(SnmpTemplate.key == key))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Eigene Vorlage nicht gefunden (mitgelieferte sind fest — zum Anpassen kopieren).")
    try:
        t.metrics = [snmpmon.validate_metric(m) for m in data.metrics]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    t.name, t.vendor, t.description = data.name, data.vendor, data.description
    t.match = [data.probe] if data.probe else []
    await db.commit()
    return (await snmpmon.all_templates(db))[t.key]


@router.delete("/templates/{key}")
async def delete_template(key: str, db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_admin)) -> dict:
    t = (await db.execute(select(SnmpTemplate).where(SnmpTemplate.key == key))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Eigene Vorlage nicht gefunden.")
    await db.delete(t)
    await db.commit()
    return {"status": "ok"}
