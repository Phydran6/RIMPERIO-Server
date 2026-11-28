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

NAS an einem anderen Standort: ``via_agent_id`` bei probe/connect (und PUT)
- dann fragt ein Agent dort, nicht der Server. ``GET /api/nas/relay-agents``
nennt die Agents, die dafuer in Frage kommen (auch den Agent als Paket auf dem
NAS selbst).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import dsmpackage, journal, nas, relay, tokens, vault
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import Agent, NasDevice, NasJob, User

router = APIRouter(prefix="/api/nas", tags=["NAS"])


class ProbeIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    via_agent_id: Optional[int] = Field(default=None, description="vom Agent am Standort aus fragen")


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
    via_agent_id: Optional[int] = Field(default=None, description="NAS an einem anderen Standort: über diesen Agent")


class NasIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    site: str = "Standard"
    verify_tls: bool = False
    enabled: bool = True
    poll_interval_sec: int = Field(default=600, ge=60, le=86400)
    automation: Dict[str, Any] = Field(default_factory=dict)
    # Nur wenn angegeben: Weg aendern (null = direkt vom Server)
    via_agent_id: Optional[int] = None


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


def _appliance(a: Agent) -> dict:
    return ((a.inventory or {}).get("appliance") or {}) if isinstance(a.inventory, dict) else {}


def _agent_brief(a: Optional[Agent]) -> Optional[dict]:
    if a is None:
        return None
    app = _appliance(a)
    return {"id": a.id, "agent_uid": a.agent_uid, "hostname": a.hostname, "site": a.site or "Standard",
            "version": a.agent_version, "on_nas": app.get("vendor") == "synology",
            "appliance": app or None, **relay.status(a.id)}


async def _check_agent(db: AsyncSession, agent_id: Optional[int]) -> Optional[Agent]:
    """Agent fuer die Weiterleitung - nur einer, den dieser Benutzer sieht (Mandant)."""
    if not agent_id:
        return None
    a = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalars().first()
    if a is None or not a.agent_uid:
        raise HTTPException(status_code=422, detail="Unbekannter Agent für die Weiterleitung.")
    return a


async def _with_agents(db: AsyncSession, items: List[dict]) -> List[dict]:
    ids = {i["via_agent_id"] for i in items if i.get("via_agent_id")}
    agents = {a.id: a for a in (await db.execute(select(Agent).where(Agent.id.in_(ids)))).scalars().all()} if ids else {}
    for i in items:
        i["via_agent"] = _agent_brief(agents.get(i.get("via_agent_id")))
    return items


@router.get("")
async def list_nas(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(NasDevice).order_by(NasDevice.name))).scalars().all()
    return await _with_agents(db, [{**nas.public(n), "busy": nas.busy(n.id)} for n in rows])


@router.get("/relay-agents")
async def relay_agents(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> List[dict]:
    """Agents, über die ein NAS an einem anderen Standort angebunden werden kann.
    Zuerst die, die gerade verbunden sind und weiterleiten können; ``on_nas``:
    der Agent läuft als Paket auf dem NAS selbst (dann Adresse 127.0.0.1)."""
    rows = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
    out = [_agent_brief(a) for a in rows]
    out.sort(key=lambda x: (not x["ready"], not x["on_nas"], (x["site"] or "").lower(), x["hostname"].lower()))
    return out


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


class DsmPackageIn(BaseModel):
    server: str = Field(default="", max_length=300, description="Leer = Adresse, unter der RIMPERIO gerade erreicht wird")
    site: str = Field(default="Standard", max_length=100)
    insecure: bool = False


async def _package_for(db: AsyncSession, request: Request, data: DsmPackageIn, user: User,
                       nas_name: str = "") -> dict:
    from .. import public_url
    server = (data.server or "").strip().rstrip("/") or public_url.base_url(request).rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Server-Adresse mit http:// oder https:// angeben.")
    _, secret = await tokens.create(db, name=f"DSM-Paket {nas_name or ''}".strip()[:100], kind="enroll",
                                    expires_in_hours=30 * 24, max_uses=5, created_by=user.username,
                                    note="Für das RIMPERIO-Paket im Synology-Paketzentrum erzeugt.")
    await db.commit()
    return await dsmpackage.build_spk(server, secret, data.site or "Standard", data.insecure)


@router.post("/dsm-package")
async def dsm_package(data: DsmPackageIn, request: Request, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_admin)) -> Response:
    """RIMPERIO-Agent als Synology-Paket (.spk) mit vorbelegter Server-Adresse und Einladung."""
    spk = await _package_for(db, request, data, user)
    journal.fire(category="rmm", event="nas.dsm_package", actor=user.username,
                 message=f"DSM-Paket {spk['version']} erzeugt (Programme: {', '.join(spk['binaries']) or 'Download beim Start'}).")
    return Response(content=spk["data"], media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{spk["filename"]}"',
                             "X-Rimperio-Binaries": ",".join(spk["binaries"]), "Cache-Control": "no-store"})


@router.post("/{nas_id}/install-package")
async def install_package(nas_id: int, data: DsmPackageIn, request: Request, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_admin)) -> dict:
    """Paket direkt ueber die DSM-API hochladen und installieren (Synology, DSM 7)."""
    n = await _get(db, nas_id)
    if n.vendor != "synology":
        raise HTTPException(status_code=422, detail="Das Paket gibt es für Synology DSM.")
    spk = await _package_for(db, request, data, user, n.name)
    try:
        result = await nas.install_spk(db, n, spk["data"], spk["filename"])
    except (nas.NasError, vault.VaultError) as exc:
        raise _bad(exc) from exc
    journal.fire(category="rmm", event="nas.package.install", actor=user.username,
                 message=f"RIMPERIO-Paket auf NAS „{n.name}“ installiert.")
    return result


@router.post("/probe")
async def probe(data: ProbeIn, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    via = await _check_agent(db, data.via_agent_id)
    try:
        info = await nas.probe(data.host.strip(), data.port, via_agent=via.id if via else None)
    except nas.NasError as exc:
        return {"found": False, "message": str(exc)}
    if info is None:
        return {"found": False, "message": f"Unter {data.host} antwortet kein bekanntes NAS (Synology: 5000/5001)"
                + (f" – gefragt vom Agent {via.hostname}." if via else ".")}
    cert = None
    if info["scheme"] == "https":
        cert = await nas.certificate(info["host"], info["port"], via.id if via else None)
    return {"found": True, **info, "certificate": cert, "via_agent": _agent_brief(via)}


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
    via = await _check_agent(db, data.via_agent_id)
    try:
        return await nas.connect(db, actor=user.username, host=data.host.strip(), username=username,
                                 password=password, otp=data.otp, totp_secret=data.totp_secret, name=data.name,
                                 port=data.port, scheme=data.scheme, verify_tls=data.verify_tls,
                                 enable_snmp_now=data.enable_snmp, site=data.site, nas_id=data.nas_id,
                                 vendor=data.vendor, via_agent_id=via.id if via else None)
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
    out = {**nas.public(n, detail=True), "busy": nas.busy(n.id), "secret": secret,
           "jobs": [_job(j) for j in jobs]}
    return (await _with_agents(db, [out]))[0]


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
    if "via_agent_id" in data.model_fields_set and data.via_agent_id != n.via_agent_id:
        via = await _check_agent(db, data.via_agent_id)
        n.via_agent_id = via.id if via else None
        # SNMP fragt, wer das NAS auch sonst erreicht: der Agent am Standort oder der Server.
        if n.snmp_device_id:
            from ..models import SnmpDevice
            dev = await db.get(SnmpDevice, n.snmp_device_id)
            if dev is not None:
                dev.poller = via.agent_uid if via else "server"
        journal.fire(category="rmm", event="nas.route", actor=user.username,
                     message=f"NAS „{n.name}“: Weg jetzt {'über den Agent ' + via.hostname if via else 'direkt vom Server'}.")
    await db.commit()
    journal.fire(category="rmm", event="nas.updated", actor=user.username, message=f"NAS „{n.name}“ geändert.")
    return (await _with_agents(db, [nas.public(n, detail=True)]))[0]


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
