# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API NAS & Spezialsysteme (Geraete -> NAS)
# ==============================================================================
"""
`/api/nas/…` - lesen darf jeder angemeldete Benutzer, einrichten, Updates und
SNMP nur Administratoren. Anmeldedaten werden nie ausgeliefert.

Einrichten in zwei Schritten, wie in der Oberflaeche:

1. ``GET /api/nas/discovered`` - was die Netzwerksuche als NAS erkannt hat
   (``?live=1`` fragt jedes System sofort nach DSM), oder ``POST /api/nas/probe``
   fuer eine einzelne IP.
2. ``POST /api/nas/connect`` - anmelden. Fehlt ein Zwei-Faktor-Code, kommt
   409 mit ``detail = "need_otp: …"``; dieselbe Anfrage mit ``otp`` wiederholen.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, nas, vault
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import NasDevice, NasJob, User

router = APIRouter(prefix="/api/nas", tags=["NAS"])


class ProbeIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)


class ConnectIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    scheme: Optional[str] = Field(default=None, pattern="^(https|http)$")
    name: str = ""
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=512)
    otp: str = Field(default="", max_length=12, description="Zwei-Faktor-Code (6 Ziffern)")
    totp_secret: str = Field(default="", max_length=512, description="Optional: TOTP-Geheimnis für Notfall-Anmeldung")
    verify_tls: bool = False
    enable_snmp: bool = True
    site: str = "Standard"
    nas_id: Optional[int] = None
    vendor: Optional[str] = Field(default=None, pattern="^(synology|truenas|generic)$")


class NasIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    site: str = "Standard"
    verify_tls: bool = False
    enabled: bool = True
    poll_interval_sec: int = Field(default=600, ge=60, le=86400)
    automation: Dict[str, Any] = Field(default_factory=dict)


class UpdateIn(BaseModel):
    what: str = Field(pattern="^(dsm|packages)$")
    packages: Optional[List[str]] = None
    confirm: str = Field(default="", description="Bei DSM-Update: 'UPDATE'")


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, nas.NasNeedsOtp):
        # Text mit festem Anfang: die Oberflaeche erkennt daran, dass ein Code fehlt.
        return HTTPException(status_code=409, detail=f"need_otp: {exc}")
    return HTTPException(status_code=422, detail=str(exc))


async def _get(db: AsyncSession, nas_id: int) -> NasDevice:
    n = await db.get(NasDevice, nas_id)
    if n is None:
        raise HTTPException(status_code=404, detail="Unbekanntes NAS.")
    return n


@router.get("")
async def list_nas(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(NasDevice).order_by(NasDevice.name))).scalars().all()
    return [{**nas.public(n), "busy": nas.busy(n.id)} for n in rows]


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    rows = (await db.execute(select(NasDevice))).scalars().all()
    by: Dict[str, int] = {}
    for n in rows:
        by[n.status] = by.get(n.status, 0) + 1
    return {"total": len(rows), "by_status": by,
            "updates": sum(1 for n in rows if nas._upd_summary(n.updates or {})["dsm"] or
                           nas._upd_summary(n.updates or {})["packages"])}


@router.get("/discovered")
async def discovered(live: bool = Query(False), db: AsyncSession = Depends(get_db),
                     _: User = Depends(get_current_admin)) -> List[dict]:
    return await nas.discovered(db, live=live)


@router.get("/special")
async def special(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return await nas.special_systems(db)


@router.post("/probe")
async def probe(data: ProbeIn, _: User = Depends(get_current_admin)) -> dict:
    info = await nas.probe(data.host.strip(), data.port)
    if info is None:
        return {"found": False, "message": f"Unter {data.host} antwortet kein bekanntes NAS (Synology: 5000/5001)."}
    cert = None
    if info["scheme"] == "https":
        import asyncio
        cert = await asyncio.to_thread(nas.cert_info, info["host"], info["port"])
    return {"found": True, **info, "certificate": cert}


@router.post("/connect")
async def connect(data: ConnectIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    username, password = data.username, data.password
    if data.nas_id and not password:
        # Neu pruefen mit gespeicherter Anmeldung (z. B. neuer TOTP-Code)
        existing = await _get(db, data.nas_id)
        try:
            cred = await vault.get(db, existing.secret_id)
        except vault.VaultError as exc:
            raise _bad(exc) from exc
        username, password = username or cred.get("username", ""), cred.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=422, detail="Benutzer und Passwort (TrueNAS: API-Schlüssel) angeben.")
    try:
        return await nas.connect(db, actor=user.username, host=data.host.strip(), username=username,
                                 password=password, otp=data.otp, totp_secret=data.totp_secret, name=data.name,
                                 port=data.port, scheme=data.scheme, verify_tls=data.verify_tls,
                                 enable_snmp_now=data.enable_snmp, site=data.site, nas_id=data.nas_id,
                                 vendor=data.vendor)
    except (nas.NasError, vault.VaultError) as exc:
        raise _bad(exc) from exc


@router.get("/{nas_id}")
async def detail(nas_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    n = await _get(db, nas_id)
    jobs = (await db.execute(select(NasJob).where(NasJob.nas_id == nas_id).order_by(desc(NasJob.started_at))
                             .limit(20))).scalars().all()
    secret = None
    if n.secret_id:
        from ..models import StoredSecret
        secret = vault.public_secret(await db.get(StoredSecret, n.secret_id))
    return {**nas.public(n, detail=True), "busy": nas.busy(n.id), "secret": secret,
            "jobs": [_job(j) for j in jobs]}


def _job(j: NasJob) -> dict:
    return {"id": j.id, "kind": j.kind, "trigger": j.trigger, "status": j.status, "target": j.target or {},
            "log": j.log or [], "error": j.error, "created_by": j.created_by,
            "started_at": nas._iso(j.started_at), "finished_at": nas._iso(j.finished_at)}


@router.put("/{nas_id}")
async def update(nas_id: int, data: NasIn, db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_admin)) -> dict:
    n = await _get(db, nas_id)
    auto = {**nas.DEFAULT_AUTOMATION, **(n.automation or {}), **data.automation}
    for k in ("dsm", "packages"):
        if auto.get(k) not in ("off", "notify", "auto"):
            raise HTTPException(status_code=422, detail=f"automation.{k}: off, notify oder auto")
    auto["days"] = sorted({int(d) for d in auto.get("days") or [] if 0 <= int(d) <= 6})
    n.name, n.site, n.verify_tls, n.enabled = data.name.strip(), data.site, data.verify_tls, data.enabled
    n.poll_interval_sec, n.automation = data.poll_interval_sec, auto
    await db.commit()
    journal.fire(category="rmm", event="nas.updated", actor=user.username, message=f"NAS „{n.name}“ geändert.")
    return nas.public(n, detail=True)


@router.delete("/{nas_id}")
async def delete(nas_id: int, keep_secret: bool = Query(False), db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_admin)) -> dict:
    n = await _get(db, nas_id)
    sid = n.secret_id
    name = n.name
    await db.delete(n)
    await db.commit()
    if sid and not keep_secret:
        await vault.remove(db, sid)
    journal.fire(category="rmm", event="nas.deleted", actor=user.username, message=f"NAS „{name}“ entfernt.")
    return {"status": "ok"}


@router.post("/{nas_id}/refresh")
async def refresh(nas_id: int, updates: bool = Query(False), db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_admin)) -> dict:
    n = await _get(db, nas_id)
    return await nas.refresh(db, n, with_updates=updates)


@router.post("/{nas_id}/snmp")
async def snmp_enable(nas_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    n = await _get(db, nas_id)
    try:
        result = await nas.enable_snmp(db, n, user.username)
    except (nas.NasError, vault.VaultError) as exc:
        raise _bad(exc) from exc
    return {"result": result, "snmp_device_id": n.snmp_device_id}


@router.post("/{nas_id}/update")
async def run_update(nas_id: int, data: UpdateIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    n = await _get(db, nas_id)
    if data.what == "dsm" and data.confirm != "UPDATE":
        raise HTTPException(status_code=422, detail="Ein DSM-Update startet das NAS neu – bitte mit 'UPDATE' bestätigen.")
    try:
        job_id = await nas.run_update(n.id, data.what, data.packages, user.username)
    except nas.NasError as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="nas.update.start", actor=user.username,
                 message=f"{'DSM-Update' if data.what == 'dsm' else 'Paket-Updates'} auf NAS „{n.name}“ gestartet.")
    return {"job_id": job_id}


@router.get("/{nas_id}/jobs/{job_id}")
async def job(nas_id: int, job_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    j = await db.get(NasJob, job_id)
    if j is None or j.nas_id != nas_id:
        raise HTTPException(status_code=404, detail="Unbekannter Vorgang.")
    return _job(j)


@router.get("/{nas_id}/backup-tasks")
async def backup_tasks(nas_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> List[dict]:
    n = await _get(db, nas_id)
    return (n.state or {}).get("backup_tasks") or []


@router.post("/{nas_id}/backup-tasks/{task_id}/run")
async def backup_task_run(nas_id: int, task_id: str, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_admin)) -> dict:
    n = await _get(db, nas_id)
    try:
        await nas.hyperbackup_run(db, n, int(task_id) if task_id.isdigit() else task_id)
    except (nas.NasError, vault.VaultError) as exc:
        raise _bad(exc) from exc
    journal.fire(category="backup", event="nas.hyperbackup.run", actor=user.username,
                 message=f"Hyper-Backup-Aufgabe {task_id} auf „{n.name}“ gestartet.")
    return {"status": "started"}
