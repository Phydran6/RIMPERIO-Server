# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API Backup-Bereich (Orte, Auftraege, Laeufe, Upload)
# ==============================================================================
"""
`/api/backups/…` - der Backup-Bereich. Lesen: angemeldete Benutzer; Orte,
Auftraege, Starten, Herunterladen und Wiederherstellen: Administratoren.

`PUT /api/backup-upload/{run_id}` nimmt den Datenstrom eines Agents an - ohne
Benutzeranmeldung, nur mit dem Einmal-Schluessel des Laufs (Header
``X-Upload-Token``). Der Schluessel gilt fuer genau einen Upload.
"""

import hashlib
import hmac
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import backupjobs as bj
from .. import journal, vault
from ..auth import admin_from_token, get_current_admin, get_current_user
from ..database import get_db
from ..models import (Agent, BackupJob, BackupRun, BackupTarget, NasDevice, PveHost, StoredSecret, User)

router = APIRouter(prefix="/api/backups", tags=["Backup"])
upload_router = APIRouter(prefix="/api/backup-upload", tags=["Backup"])


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern="^(local|smb|sftp|ftp|ftps)$")
    host: str = ""
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    share: str = ""
    path: str = ""
    username: str = ""
    password: str = Field(default="", description="Leer = unverändert")
    nas_id: Optional[int] = None
    enabled: bool = True


class JobIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern="^(rimperio|files|nas_config|nas_task|pve)$")
    source: Dict[str, Any] = Field(default_factory=dict)
    target_id: Optional[int] = None
    encrypt: bool = True
    own_key: bool = Field(default=False, description="Eigenen Schlüssel statt des Standard-Backup-Schlüssels")
    schedule: Dict[str, Any] = Field(default_factory=lambda: {"type": "daily", "time": "02:30", "tz": "Europe/Berlin"})
    keep_last: int = Field(default=14, ge=1, le=1000)
    keep_days: int = Field(default=0, ge=0, le=3650)
    max_age_hours: int = Field(default=0, ge=0, le=24 * 90)
    enabled: bool = True
    note: str = ""


# =============================================================================
# Uebersicht
# =============================================================================
@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    jobs = (await db.execute(select(BackupJob))).scalars().all()
    targets = (await db.execute(select(BackupTarget))).scalars().all()
    pj = [bj.public_job(j) for j in jobs]
    total = (await db.execute(select(func.sum(BackupRun.size_bytes)).where(BackupRun.status == "done"))).scalar() or 0
    key_id = await bj.default_key(db, create=False)
    key = await db.get(StoredSecret, key_id) if key_id else None
    return {"jobs": len(jobs), "enabled": sum(1 for j in jobs if j.enabled),
            "ok": sum(1 for j in jobs if j.last_status == "done"),
            "failed": sum(1 for j in jobs if j.last_status == "error"),
            "overdue": sum(1 for j in pj if j["overdue"]), "targets": len(targets),
            "stored_bytes": total, "kinds": bj.KINDS, "target_kinds": bj.TARGET_KINDS,
            "key": vault.public_secret(key), "vault_active": await vault.is_active(db)}


# =============================================================================
# Orte
# =============================================================================
@router.get("/targets")
async def targets(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(BackupTarget).order_by(BackupTarget.name))).scalars().all()
    out = []
    for t in rows:
        secret = await db.get(StoredSecret, t.secret_id) if t.secret_id else None
        used = (await db.execute(select(func.count()).select_from(BackupJob).where(BackupJob.target_id == t.id))).scalar()
        out.append({**bj.public_target(t, secret), "jobs": used})
    return out


def _check_target(data: TargetIn) -> None:
    if data.kind != "local" and not data.host.strip():
        raise HTTPException(status_code=422, detail="Bitte Server bzw. NAS-Adresse angeben.")
    if data.kind == "smb" and not data.share.strip():
        raise HTTPException(status_code=422, detail="Für SMB bitte die Freigabe angeben (z. B. Backup).")
    if data.kind == "local" and data.path and (".." in data.path or not data.path.startswith("/")):
        raise HTTPException(status_code=422, detail="Lokaler Pfad bitte absolut (z. B. /backups/extern) oder leer.")


async def _save_target(db: AsyncSession, t: BackupTarget, data: TargetIn) -> BackupTarget:
    t.name, t.kind, t.host, t.port = data.name.strip(), data.kind, data.host.strip(), data.port
    t.share, t.path, t.nas_id, t.enabled = data.share.strip(), data.path.strip(), data.nas_id, data.enabled
    if data.kind != "local" and (data.username or data.password or t.secret_id):
        if data.password or not t.secret_id:
            secret = await vault.put(db, secret_id=t.secret_id, kind="backup_target", name=f"Backup-Ort {t.name}",
                                     username=data.username, password=data.password if data.password else None,
                                     uri=f"{data.kind}://{t.host}" + (f"/{t.share}" if t.share else ""))
            t.secret_id = secret.id
        else:
            secret = await vault.put(db, secret_id=t.secret_id, kind="backup_target", name=f"Backup-Ort {t.name}",
                                     username=data.username, password=None,
                                     uri=f"{data.kind}://{t.host}" + (f"/{t.share}" if t.share else ""))
    await db.commit()
    await db.refresh(t)
    return t


@router.post("/targets", status_code=201)
async def create_target(data: TargetIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    _check_target(data)
    t = BackupTarget()
    db.add(t)
    try:
        t = await _save_target(db, t, data)
    except vault.VaultError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    journal.fire(category="backup", event="backup.target.created", actor=user.username,
                 message=f"Backup-Ort „{t.name}“ ({t.kind}) angelegt.")
    return bj.public_target(t)


@router.put("/targets/{target_id}")
async def update_target(target_id: int, data: TargetIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    t = await db.get(BackupTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Unbekannter Ort.")
    _check_target(data)
    try:
        t = await _save_target(db, t, data)
    except vault.VaultError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return bj.public_target(t)


@router.delete("/targets/{target_id}")
async def delete_target(target_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    t = await db.get(BackupTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Unbekannter Ort.")
    used = (await db.execute(select(func.count()).select_from(BackupJob).where(BackupJob.target_id == t.id))).scalar()
    if used:
        raise HTTPException(status_code=409, detail=f"Der Ort wird noch von {used} Auftrag/Aufträgen benutzt.")
    sid = t.secret_id
    await db.delete(t)
    await db.commit()
    await vault.remove(db, sid)
    journal.fire(category="backup", event="backup.target.deleted", actor=user.username,
                 message=f"Backup-Ort „{t.name}“ entfernt (Dateien am Ort bleiben).")
    return {"status": "ok"}


@router.post("/targets/{target_id}/test")
async def test_target(target_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    import asyncio
    t = await db.get(BackupTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Unbekannter Ort.")
    try:
        store = await bj.store_for(db, t)
        res = await asyncio.to_thread(store.test)
    except Exception as exc:
        t.last_error = str(exc)[:500]
        await db.commit()
        return {"ok": False, "message": f"Nicht beschreibbar: {exc}"}
    t.last_ok_at, t.last_error, t.free_bytes = datetime.utcnow(), None, res.get("free_bytes")
    await db.commit()
    return {"ok": res["ok"], "message": f"Schreiben, Lesen, Löschen ok: {res['where']}", **res}


# =============================================================================
# Auftraege
# =============================================================================
async def _validate_job(db: AsyncSession, data: JobIn) -> None:
    src = data.source
    if data.kind not in ("nas_task", "pve") and not data.target_id:
        raise HTTPException(status_code=422, detail="Bitte einen Ort wählen, wohin gesichert wird.")
    if data.target_id and await db.get(BackupTarget, data.target_id) is None:
        raise HTTPException(status_code=422, detail="Unbekannter Ort.")
    if data.kind == "files":
        if not src.get("agent_uid") or not (await db.execute(select(Agent).where(Agent.agent_uid == src["agent_uid"]))).scalars().first():
            raise HTTPException(status_code=422, detail="Bitte einen Rechner wählen.")
        paths = [p.strip() for p in src.get("paths") or [] if str(p).strip()]
        if not paths:
            raise HTTPException(status_code=422, detail="Bitte mindestens einen Ordner angeben.")
        src["paths"] = paths
        src["excludes"] = [e.strip() for e in src.get("excludes") or [] if str(e).strip()]
    if data.kind in ("nas_config", "nas_task"):
        n = await db.get(NasDevice, src.get("nas_id")) if src.get("nas_id") else None
        if n is None:
            raise HTTPException(status_code=422, detail="Bitte ein NAS wählen.")
        if n.vendor != "synology":
            raise HTTPException(status_code=422, detail="Das geht bisher nur mit Synology.")
        if data.kind == "nas_task" and src.get("task_id") in (None, ""):
            raise HTTPException(status_code=422, detail="Bitte die Hyper-Backup-Aufgabe wählen.")
    if data.kind == "pve":
        if not src.get("host_id") or await db.get(PveHost, src["host_id"]) is None or not src.get("vmid") or not src.get("node"):
            raise HTTPException(status_code=422, detail="Bitte Proxmox-Host und VM/Container wählen.")
    st = data.schedule or {}
    if st.get("type", "daily") not in ("daily", "weekly", "hourly", "manual"):
        raise HTTPException(status_code=422, detail="Zeitplan: daily, weekly, hourly oder manual.")


async def _jobs_out(db: AsyncSession, jobs: List[BackupJob]) -> List[dict]:
    out = []
    for j in jobs:
        target = await db.get(BackupTarget, j.target_id) if j.target_id else None
        last = (await db.execute(select(BackupRun).where(BackupRun.job_id == j.id)
                                 .order_by(desc(BackupRun.started_at)).limit(1))).scalars().first()
        out.append({**bj.public_job(j, target, last, await bj.describe_source(db, j)), "running": bj.running(j.id)})
    return out


@router.get("/jobs")
async def jobs(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(BackupJob).order_by(BackupJob.name))).scalars().all()
    return await _jobs_out(db, rows)


async def _apply_job(db: AsyncSession, job: BackupJob, data: JobIn) -> None:
    job.name, job.kind, job.source, job.target_id = data.name.strip(), data.kind, dict(data.source), data.target_id
    job.encrypt = data.encrypt and data.kind not in ("nas_task", "pve")
    job.schedule = {"tz": "Europe/Berlin", **(data.schedule or {})}
    job.keep_last, job.keep_days, job.max_age_hours = data.keep_last, data.keep_days, data.max_age_hours
    job.enabled, job.note = data.enabled, data.note
    if job.encrypt:
        if data.own_key and not job.key_secret_id:
            import secrets as _s
            key = await vault.put(db, secret_id=None, kind="backup_key", name=f"Backup-Schlüssel {job.name}",
                                  username="rimperio-backup", password=_s.token_urlsafe(32),
                                  notes=f"Passwort der Sicherungen des Auftrags „{job.name}“ (RIMPBAK1). Nicht löschen!")
            job.key_secret_id = key.id
        elif not data.own_key:
            job.key_secret_id = None
            await bj.default_key(db)
    job.next_run_at = bj.next_run(job.schedule, datetime.utcnow())


@router.post("/jobs", status_code=201)
async def create_job(data: JobIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    await _validate_job(db, data)
    job = BackupJob(created_by=user.username)
    db.add(job)
    try:
        await _apply_job(db, job, data)
    except vault.VaultError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(job)
    journal.fire(category="backup", event="backup.job.created", actor=user.username,
                 message=f"Backup-Auftrag „{job.name}“ ({bj.KINDS.get(job.kind)}) angelegt.")
    return (await _jobs_out(db, [job]))[0]


@router.put("/jobs/{job_id}")
async def update_job(job_id: int, data: JobIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    job = await db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unbekannter Auftrag.")
    await _validate_job(db, data)
    try:
        await _apply_job(db, job, data)
    except vault.VaultError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return (await _jobs_out(db, [job]))[0]


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, delete_files: bool = Query(False), db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    job = await db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unbekannter Auftrag.")
    removed = 0
    if delete_files:
        for r in (await db.execute(select(BackupRun).where(BackupRun.job_id == job.id, BackupRun.status == "done"))).scalars():
            try:
                await bj.delete_run_file(db, r)
                removed += 1
            except Exception:                                       # defensiv
                pass
    name = job.name
    await db.delete(job)
    await db.commit()
    journal.fire(category="backup", event="backup.job.deleted", actor=user.username,
                 message=f"Backup-Auftrag „{name}“ entfernt" + (f", {removed} Dateien gelöscht." if delete_files else
                                                                 " (Dateien bleiben am Ort)."))
    return {"status": "ok", "removed": removed}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: int, user: User = Depends(get_current_admin)) -> dict:
    try:
        run_id = await bj.start(job_id, user.username, trigger="manual")
    except bj.BackupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id}


# =============================================================================
# Laeufe
# =============================================================================
@router.get("/runs")
async def runs(job_id: Optional[int] = None, status: Optional[str] = None, limit: int = Query(100, ge=1, le=1000),
               db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    q = select(BackupRun).order_by(desc(BackupRun.started_at)).limit(limit)
    if job_id:
        q = q.where(BackupRun.job_id == job_id)
    if status:
        q = q.where(BackupRun.status == status)
    rows = (await db.execute(q)).scalars().all()
    names = {j.id: j.name for j in (await db.execute(select(BackupJob))).scalars()}
    return [{**bj.public_run(r), "job_name": names.get(r.job_id, "")} for r in rows]


async def _run(db: AsyncSession, run_id: int) -> BackupRun:
    r = await db.get(BackupRun, run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Unbekannter Lauf.")
    return r


@router.get("/runs/{run_id}")
async def run_detail(run_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return bj.public_run(await _run(db, run_id))


@router.get("/runs/{run_id}/download")
async def download(run_id: int, background: BackgroundTasks, decrypt: bool = Query(True),
                   token: str = Query("", description="Download-Ticket (/api/auth/ticket, Zweck backup-download)"),
                   authorization: str = Header(""), db: AsyncSession = Depends(get_db)) -> FileResponse:
    """Datei holen (entschluesselt, wenn nicht ``decrypt=false``). Anmeldung per
    Kopf oder - fuer den Link in der Oberflaeche - per kurzlebigem Ticket."""
    if token.strip():
        user = await admin_from_token(token.strip(), purpose="backup-download")
    elif authorization.startswith("Bearer "):
        user = await admin_from_token(authorization[7:].strip())
    else:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet oder keine Administratorrechte.")
    r = await _run(db, run_id)
    try:
        path, name = await bj.fetch(db, r, decrypt=decrypt)
    except (bj.BackupError, vault.VaultError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background.add_task(path.unlink, missing_ok=True)
    journal.fire(category="backup", event="backup.download", actor=user.username,
                 message=f"Sicherung {name} heruntergeladen ({'entschlüsselt' if decrypt and r.encrypted else 'wie gespeichert'}).")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.post("/runs/{run_id}/verify")
async def verify(run_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    r = await _run(db, run_id)
    try:
        return {"ok": True, **(await bj.verify(db, r))}
    except (bj.BackupError, vault.VaultError, OSError) as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/runs/{run_id}/to-restore")
async def to_restore(run_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    r = await _run(db, run_id)
    try:
        name = await bj.to_restore(db, r)
    except (bj.BackupError, vault.VaultError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    journal.fire(category="backup", event="backup.prepare_restore", actor=user.username,
                 message=f"Sicherung aus Lauf {run_id} zum Zurückspielen bereitgelegt: {name}.")
    return {"name": name}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    r = await _run(db, run_id)
    if r.status in ("running", "waiting"):
        raise HTTPException(status_code=409, detail="Der Lauf ist noch nicht fertig.")
    try:
        await bj.delete_run_file(db, r)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Löschen am Ort fehlgeschlagen: {exc}") from exc
    await db.commit()
    return {"status": "ok"}


# =============================================================================
# Upload vom Agent
# =============================================================================
@upload_router.put("/{run_id}")
@upload_router.post("/{run_id}")
async def upload(run_id: int, request: Request, x_upload_token: str = Header(default=""),
                 db: AsyncSession = Depends(get_db)) -> dict:
    r = await db.get(BackupRun, run_id)
    want = r.upload_token_hash if r else ""
    got = hashlib.sha256(x_upload_token.encode()).hexdigest() if x_upload_token else ""
    if not r or not want or r.status != "waiting" or not hmac.compare_digest(want, got):
        raise HTTPException(status_code=403, detail="Upload nicht erlaubt (Schlüssel falsch oder schon verbraucht).")
    try:
        await bj.receive_upload(db, r, request.stream())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sicherung nicht abgelegt: {exc}") from exc
    return {"status": "ok", "size": r.size_bytes}
