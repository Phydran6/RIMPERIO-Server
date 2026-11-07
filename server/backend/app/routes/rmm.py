# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - RMM-API: Agent-Schnittstelle und Verwaltung
# ==============================================================================
"""
Zwei Router:

* ``agent_router`` (/api/rmm/agent/…) — spricht der RIMPERIO-Agent. Anmeldung
  mit einem Zugangsschlüssel (Generalschlüssel oder Einladung, wie bei den
  Log-Agents), danach mit seinem persönlichen Token.
* ``router`` (/api/rmm/…) — die Oberfläche. Lesen darf jeder angemeldete
  Benutzer, Eingriffe (Befehle, Skripte, Checks, Patches) nur Administratoren.
  Jeder Eingriff steht im Systemtagebuch.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import patchmgmt, rmm, snmpmon, tokens
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..limiter import client_ip
from ..models import (Agent, AgentToken, Check, CheckResult, Command, Log, PatchItem,
                      PatchReport, PatchRun, PveLink, RmmAlert, Script, Telemetry, User)

router = APIRouter(prefix="/api/rmm", tags=["RMM"])
agent_router = APIRouter(prefix="/api/rmm/agent", tags=["RMM-Agent"])

SHELLS = {"powershell", "cmd", "bash", "sh"}
CHECK_TYPES = {"cpu", "mem", "disk", "service", "port", "ping", "script"}
CHECK_STATUSES = {"pass", "fail", "warn", "unknown"}
# Obergrenze fuer eine Inventur. Ein echter Rechner liegt weit darunter; ohne
# Grenze koennte ein uebernommener Agent die Datenbank mit Muell fuellen.
MAX_INVENTORY_BYTES = 4 * 1024 * 1024


def _now() -> datetime:
    return datetime.utcnow()


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# ==============================================================================
# Schemas
# ==============================================================================

class EnrollIn(BaseModel):
    enroll_secret: str
    hostname: str
    os: str = ""
    platform: str = ""
    arch: str = ""
    version: str = ""
    local_ip: Optional[str] = None
    site: str = "Standard"
    description: str = ""


class DiskIn(BaseModel):
    mount: str
    percent: float = 0.0
    used_gb: float = 0.0
    total_gb: float = 0.0


class ProcIn(BaseModel):
    name: str
    cpu: float = 0.0
    mem: float = 0.0


class TelemetryIn(BaseModel):
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    mem_used_mb: float = 0.0
    mem_total_mb: float = 0.0
    uptime_seconds: int = 0
    boot_time: Optional[datetime] = None
    disk: List[DiskIn] = []
    top_procs: List[ProcIn] = []


class CheckinIn(BaseModel):
    version: Optional[str] = None
    local_ip: Optional[str] = None
    public_ip: Optional[str] = None
    telemetry: Optional[TelemetryIn] = None


class CommandResultIn(BaseModel):
    id: int
    status: str = "done"
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


class CheckResultIn(BaseModel):
    check_id: int
    status: str = "unknown"
    value: str = ""
    output: Optional[str] = None


class AgentUpdate(BaseModel):
    site: Optional[str] = None
    description: Optional[str] = None


class CommandIn(BaseModel):
    shell: str = "powershell"
    command: str = Field(min_length=1)
    timeout: int = Field(default=300, ge=1, le=86400)


class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    shell: str = "powershell"
    body: str = Field(min_length=1)
    default_timeout: int = Field(default=300, ge=1, le=86400)
    platform: str = "any"
    category: str = "Allgemein"


class CheckIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str
    params: Dict[str, Any] = {}
    interval_sec: int = Field(default=300, ge=30, le=86400)
    scope_type: str = "global"
    scope_value: str = ""
    severity: str = "warning"
    enabled: bool = True


class ThresholdsIn(BaseModel):
    cpu_percent: float = Field(default=90, ge=1, le=100)
    mem_percent: float = Field(default=90, ge=1, le=100)
    disk_percent: float = Field(default=90, ge=1, le=100)
    offline_after: int = Field(default=300, ge=60, le=86400)


# ==============================================================================
# Ausgabe
# ==============================================================================

def _command(c: Command) -> dict:
    return {"id": c.id, "agent_id": c.agent_id, "shell": c.shell, "command": c.command,
            "timeout": c.timeout, "kind": c.kind, "source": c.source, "status": c.status,
            "created_by": c.created_by, "created_at": rmm.iso(c.created_at),
            "picked_at": rmm.iso(c.picked_at), "finished_at": rmm.iso(c.finished_at),
            "exit_code": c.exit_code, "stdout": c.stdout, "stderr": c.stderr}


def _script(s: Script) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description, "shell": s.shell,
            "body": s.body, "default_timeout": s.default_timeout, "platform": s.platform,
            "category": s.category, "created_by": s.created_by,
            "created_at": rmm.iso(s.created_at), "updated_at": rmm.iso(s.updated_at)}


def _check(c: Check) -> dict:
    return {"id": c.id, "name": c.name, "type": c.type, "params": c.params or {},
            "interval_sec": c.interval_sec, "scope_type": c.scope_type,
            "scope_value": c.scope_value, "severity": c.severity, "enabled": c.enabled,
            "created_at": rmm.iso(c.created_at)}


def _alert(a: RmmAlert, hostname: str = "") -> dict:
    return {"id": a.id, "agent_id": a.agent_id, "hostname": hostname, "kind": a.kind,
            "severity": a.severity, "message": a.message, "active": a.active,
            "created_at": rmm.iso(a.created_at), "resolved_at": rmm.iso(a.resolved_at)}


# ==============================================================================
# Entfernen
# ==============================================================================

_RMM_COLUMNS = ("agent_uid", "rmm_token_hash", "enrolled_at", "rmm_last_seen", "boot_time",
                "last_cpu", "last_mem", "last_disk", "inventory", "agent_version")


async def _remove_rmm(db: AsyncSession, agent: Agent) -> bool:
    """Nimmt den RMM-Agent vom Gerät: Messwerte, Befehle, Check-Ergebnisse,
    Patch-Stand und Alarme gehen. Gehört dem Gerät sonst nichts (keine Logs,
    kein eigener Log-Agent-Schlüssel), verschwindet es ganz; sonst bleibt es
    mit seinen Logs unter Geräte stehen. Liefert True, wenn es ganz weg ist."""
    for model in (Telemetry, Command, CheckResult, PatchReport, RmmAlert, PatchItem, PatchRun,
                  PveLink):
        await db.execute(delete(model).where(model.agent_id == agent.id))
    has_logs = (await db.execute(
        select(Log.id).where(Log.agent_id == agent.id).limit(1))).first() is not None
    has_key = (await db.execute(
        select(AgentToken.id).where(AgentToken.agent_id == agent.id).limit(1))).first() is not None
    if has_logs or has_key:
        for col in _RMM_COLUMNS:
            setattr(agent, col, None)
    else:
        await db.delete(agent)
    await db.commit()
    return not (has_logs or has_key)


async def _uninstall_pending(db: AsyncSession) -> set:
    """Geräte, bei denen eine Deinstallation eingereiht ist oder gerade läuft."""
    return set((await db.execute(select(Command.agent_id).where(
        Command.kind == "uninstall", Command.status.in_(("pending", "running"))))).scalars())


# ==============================================================================
# Agent-Schnittstelle
# ==============================================================================

async def current_agent(authorization: str = Header(default=""),
                        db: AsyncSession = Depends(get_db)) -> Agent:
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Agent-Token fehlt.")
    agent = (await db.execute(
        select(Agent).where(Agent.rmm_token_hash == rmm.hash_token(token))
    )).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=401, detail="Agent-Token unbekannt.")
    return agent


@agent_router.post("/enroll")
async def enroll(data: EnrollIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Erst-Anmeldung. Der Zugangsschlüssel (Generalschlüssel oder Einladung)
    ist der Vertrauensanker. Ein Gerät mit gleichem Hostnamen — etwa eines,
    das schon Logs schickt — wird weiterverwendet, statt ein zweites anzulegen."""
    ip = client_ip(request)
    key, reason = await tokens.resolve(db, data.enroll_secret, ip)
    if key is None or not tokens.may_enroll(key):
        rmm.record("enroll.denied", f"RMM-Anmeldung von {data.hostname} abgelehnt: "
                   f"{reason or 'Schlüssel darf keine Geräte anmelden.'}",
                   actor=data.hostname, target=data.hostname, source_ip=ip)
        raise HTTPException(status_code=401, detail="Zugangsschlüssel ungültig.")
    tokens.note_use(key, ip)

    hostname = data.hostname.strip()[:255]
    agent = (await db.execute(
        select(Agent).where(func.lower(Agent.hostname) == hostname.lower()).order_by(Agent.id)
    )).scalars().first()
    # Uebernahme-Schutz: Ein Geraet, das schon als RMM-Agent angemeldet ist,
    # bekommt nicht einfach einen neuen Token, nur weil jemand denselben
    # Hostnamen nennt. Sonst koennte jeder mit einer Einladung ein fremdes
    # Geraet "uebernehmen": er bekaeme dessen Befehle (samt evtl. Geheimnissen
    # darin), koennte Ergebnisse faelschen, und der echte Agent waere
    # ausgesperrt. Neu anmelden darf ein solches Geraet nur der
    # Generalschluessel, ein an genau dieses Geraet gebundener Schluessel -
    # oder nachdem es in der Oberflaeche entfernt bzw. vom Agent abgemeldet
    # wurde.
    if agent is not None and agent.rmm_token_hash:
        kind = (key.kind or "agent").lower()
        bound_here = key.agent_id is not None and key.agent_id == agent.id
        if kind != "global" and not bound_here:
            await db.rollback()
            rmm.record("enroll.denied",
                       f"RMM-Anmeldung für {hostname} abgelehnt: das Gerät ist bereits angemeldet "
                       f"(Schlüssel '{key.name}').", actor=hostname, target=hostname, source_ip=ip)
            raise HTTPException(
                status_code=409,
                detail=("Ein Gerät mit diesem Namen ist bereits angemeldet. Für eine Neuinstallation "
                        "das Gerät zuerst in der Oberfläche entfernen oder den Generalschlüssel "
                        "verwenden."))
        rmm.record("enroll.reenrolled",
                   f"RMM-Agent auf {hostname} neu angemeldet – der bisherige Agent-Token ist ungültig.",
                   actor=key.name or "Schlüssel", target=hostname, source_ip=ip)
    if agent is None:
        agent = Agent(hostname=hostname, device_type=data.platform or "unknown",
                      first_seen=_now(), last_seen=_now(), extra_data={})
        db.add(agent)
    token, token_hash = rmm.new_agent_token()
    agent.agent_uid = agent.agent_uid or rmm.new_uid()
    agent.rmm_token_hash = token_hash
    agent.site = data.site or agent.site or "Standard"
    agent.description = data.description or agent.description or ""
    agent.os, agent.platform, agent.arch = data.os, data.platform, data.arch
    agent.agent_version = data.version
    if data.local_ip:
        agent.ip_address = data.local_ip[:45]
    if agent.device_type in (None, "", "unknown") and data.platform:
        agent.device_type = data.platform
    agent.enrolled_at = agent.enrolled_at or _now()
    agent.rmm_last_seen = _now()
    await db.commit()
    rmm.record("enroll", f"RMM-Agent auf {hostname} angemeldet.", actor=key.name or "Schlüssel",
               target=hostname, source_ip=ip)
    return {"agent_uid": agent.agent_uid, "agent_token": token,
            "checkin_interval": rmm.CHECKIN_INTERVAL}


@agent_router.post("/unregister")
async def unregister(request: Request, agent: Agent = Depends(current_agent),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """Abmeldung durch den Agent selbst — beim Deinstallieren am Rechner oder
    nach einem „Entfernen und deinstallieren“ aus der Oberfläche."""
    hostname = agent.hostname
    deleted = await _remove_rmm(db, agent)
    rmm.record("agent.unregistered",
               f"RMM-Agent auf {hostname} hat sich abgemeldet"
               + (" – Gerät gelöscht." if deleted else " – das Gerät bleibt mit seinen Logs."),
               actor=hostname, target=hostname, source_ip=client_ip(request))
    return {"status": "ok", "device_deleted": deleted}


@agent_router.post("/checkin")
async def checkin(data: CheckinIn, request: Request, agent: Agent = Depends(current_agent),
                  db: AsyncSession = Depends(get_db)) -> dict:
    agent.rmm_last_seen = _now()
    if data.version:
        agent.agent_version = data.version
    if data.local_ip:
        agent.ip_address = data.local_ip[:45]
    agent.public_ip = data.public_ip or client_ip(request) or agent.public_ip
    th = await rmm.get_thresholds(db)

    if data.telemetry is not None:
        t = data.telemetry
        disk = [d.model_dump() for d in t.disk]
        db.add(Telemetry(agent_id=agent.id, cpu_percent=t.cpu_percent, mem_percent=t.mem_percent,
                         mem_used_mb=t.mem_used_mb, mem_total_mb=t.mem_total_mb,
                         uptime_seconds=t.uptime_seconds, disk=disk,
                         top_procs=[p.model_dump() for p in t.top_procs]))
        worst = max((d["percent"] for d in disk), default=0.0)
        agent.last_cpu, agent.last_mem, agent.last_disk = t.cpu_percent, t.mem_percent, worst
        if t.boot_time:
            agent.boot_time = _naive(t.boot_time)
        await rmm.evaluate_thresholds(db, agent, t.cpu_percent, t.mem_percent, worst, th)

    pending = (await db.execute(
        select(Command).where(Command.agent_id == agent.id, Command.status == "pending")
        .order_by(Command.id)
    )).scalars().all()
    commands = []
    for c in pending:
        c.status, c.picked_at = "running", _now()
        commands.append({"id": c.id, "shell": c.shell, "command": c.command,
                         "timeout": c.timeout, "kind": c.kind})

    checks = []
    for ck in (await db.execute(select(Check).where(Check.enabled.is_(True)))).scalars():
        if (ck.scope_type == "global"
                or (ck.scope_type == "site" and ck.scope_value == (agent.site or "Standard"))
                or (ck.scope_type == "agent" and ck.scope_value == agent.agent_uid)):
            checks.append({"id": ck.id, "type": ck.type, "params": ck.params or {},
                           "interval": ck.interval_sec})
    await db.commit()
    snmp_tasks = await snmpmon.tasks_for_agent(db, agent)
    return {"interval": rmm.CHECKIN_INTERVAL, "offline_after": th["offline_after"],
            "commands": commands, "checks": checks, "snmp": snmp_tasks}


@agent_router.post("/inventory")
async def inventory(payload: dict = Body(...), agent: Agent = Depends(current_agent),
                    db: AsyncSession = Depends(get_db)) -> dict:
    """Vollständige Inventur (OS, Hardware, Software, Dienste, Netzwerk)."""
    if len(json.dumps(payload, default=str)) > MAX_INVENTORY_BYTES:
        raise HTTPException(status_code=413, detail="Inventur zu groß.")
    agent.inventory = payload
    agent.rmm_last_seen = _now()
    for key in ("os", "platform", "arch"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            setattr(agent, key, val)
    await db.commit()
    return {"status": "ok"}


@agent_router.post("/results")
async def results(items: List[CommandResultIn], agent: Agent = Depends(current_agent),
                  db: AsyncSession = Depends(get_db)) -> dict:
    updated, found_patches = 0, 0
    scans: List[Command] = []
    for item in items:
        cmd = (await db.execute(select(Command).where(
            Command.id == item.id, Command.agent_id == agent.id))).scalar_one_or_none()
        if cmd is None:
            continue
        cmd.status = item.status if item.status in {"done", "error", "timeout"} else "done"
        cmd.exit_code = item.exit_code
        cmd.stdout = (item.stdout or "")[:2_000_000]
        cmd.stderr = (item.stderr or "")[:200_000]
        cmd.finished_at = _now()
        updated += 1
        if cmd.kind in ("patch_scan", "docker_scan") and cmd.status == "done":
            scans.append(cmd)
        elif cmd.kind == "patch_apply":
            report = (await db.execute(select(PatchReport).where(
                PatchReport.agent_id == agent.id))).scalar_one_or_none()
            if report is None:
                report = PatchReport(agent_id=agent.id, updates=[], count=0)
                db.add(report)
            report.last_apply_at = _now()
            report.last_apply_output = ((cmd.stdout or "") + "\n" + (cmd.stderr or ""))[:200_000]
    agent.rmm_last_seen = _now()
    await db.commit()
    for cmd in scans:
        try:
            payload = json.loads(cmd.stdout or "{}")
        except ValueError:
            payload = {}
        try:
            if cmd.kind == "patch_scan":
                found_patches += (await patchmgmt.ingest_os_scan(db, agent, payload))["new"]
            else:
                await patchmgmt.ingest_docker_scan(db, agent, payload)
        except Exception as exc:                                    # defensiv
            await db.rollback()
            rmm.record("patch.ingest.failed", f"{agent.hostname}: Scan nicht übernommen: {exc}",
                       target=agent.hostname)
    if found_patches:
        await rmm.alerts.dispatch("rmm_patches", f"RIMPERIO: {agent.hostname}",
                                  f"{agent.hostname}: {found_patches} neue Updates gefunden.",
                                  level="info", key=f"rmm:{agent.id}:patches")
    return {"status": "ok", "updated": updated}


@agent_router.post("/snmp-results")
async def snmp_results(items: List[Dict[str, Any]], agent: Agent = Depends(current_agent),
                       db: AsyncSession = Depends(get_db)) -> dict:
    """Rohwerte der SNMP-Geräte, die dieser Agent abfragt."""
    done = await snmpmon.ingest_agent_results(db, agent, items)
    return {"status": "ok", "accepted": done}


@agent_router.post("/check-results")
async def check_results(items: List[CheckResultIn], agent: Agent = Depends(current_agent),
                        db: AsyncSession = Depends(get_db)) -> dict:
    for item in items:
        check = await db.get(Check, item.check_id)
        if check is None:
            continue
        row = (await db.execute(select(CheckResult).where(
            CheckResult.check_id == item.check_id, CheckResult.agent_id == agent.id
        ))).scalar_one_or_none()
        if row is None:
            row = CheckResult(check_id=item.check_id, agent_id=agent.id)
            db.add(row)
        row.status = item.status if item.status in CHECK_STATUSES else "unknown"
        row.value = (item.value or "")[:255]
        row.output = (item.output or "")[:20000] or None
        row.ts = _now()
        kind = f"check:{item.check_id}"
        if item.status == "fail":
            await rmm.raise_alert(db, agent, kind, check.severity,
                                  f"{agent.hostname}: Check „{check.name}“ fehlgeschlagen ({item.value})")
        elif item.status == "pass":
            await rmm.resolve_alert(db, agent, kind)
    agent.rmm_last_seen = _now()
    await db.commit()
    return {"status": "ok"}


# ==============================================================================
# Verwaltung — Geräte
# ==============================================================================

async def _agent(db: AsyncSession, uid: str) -> Agent:
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == uid))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")
    return agent


async def _queue(db: AsyncSession, agent: Agent, user: User, *, shell: str, command: str,
                 timeout: int, kind: str = "shell", source: str = "") -> dict:
    if kind == "shell" and shell not in SHELLS:
        raise HTTPException(status_code=422, detail=f"Unbekannte Shell: {shell}")
    cmd = Command(agent_id=agent.id, shell=shell, command=command, timeout=timeout,
                  kind=kind, source=source, created_by=user.username)
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    what = source or (command[:80] if kind == "shell" else kind)
    rmm.record(f"command.{kind}", f"{user.username} → {agent.hostname}: {what}",
               actor=user.username, target=agent.hostname, command_id=cmd.id)
    return _command(cmd)


@router.get("/agents")
async def list_agents(site: Optional[str] = None, db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)) -> List[dict]:
    th = await rmm.get_thresholds(db)
    q = select(Agent).where(Agent.agent_uid.is_not(None)).order_by(Agent.hostname)
    if site:
        q = q.where(Agent.site == site)
    pending = await _uninstall_pending(db)
    return [{**rmm.agent_public(a, th["offline_after"]), "uninstall_pending": a.id in pending}
            for a in (await db.execute(q)).scalars()]


@router.get("/agents/{uid}")
async def get_agent(uid: str, db: AsyncSession = Depends(get_db),
                    _: User = Depends(get_current_user)) -> dict:
    th = await rmm.get_thresholds(db)
    agent = await _agent(db, uid)
    return {**rmm.agent_public(agent, th["offline_after"], detail=True),
            "uninstall_pending": agent.id in await _uninstall_pending(db)}


@router.patch("/agents/{uid}")
async def update_agent(uid: str, data: AgentUpdate, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    if data.site is not None:
        agent.site = data.site.strip() or "Standard"
    if data.description is not None:
        agent.description = data.description.strip()
    await db.commit()
    th = await rmm.get_thresholds(db)
    return rmm.agent_public(agent, th["offline_after"], detail=True)


@router.delete("/agents/{uid}")
async def detach_agent(uid: str, uninstall: bool = False, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    """Entfernt den RMM-Agent.

    * ``uninstall=true``: Der Agent bekommt beim nächsten Check-in den Auftrag,
      sich vom Rechner zu deinstallieren, und meldet sich dabei selbst ab. Erst
      dann verschwindet er hier. Ist der Rechner aus, wartet der Auftrag.
    * sonst sofort: nur der Eintrag geht, der Agent auf dem Rechner bleibt
      (und wird abgewiesen, bis man ihn dort deinstalliert).

    Ein Gerät mit Logs bleibt in beiden Fällen unter Geräte stehen."""
    agent = await _agent(db, uid)
    hostname = agent.hostname
    if uninstall:
        if agent.id in await _uninstall_pending(db):
            return {"status": "uninstall_queued"}
        await _queue(db, agent, user, shell="", command="Agent deinstallieren", timeout=300,
                     kind="uninstall", source="Agent deinstallieren")
        return {"status": "uninstall_queued"}
    deleted = await _remove_rmm(db, agent)
    rmm.record("agent.detached",
               f"RMM-Agent von {hostname} entfernt"
               + (" – Gerät gelöscht." if deleted else " – das Gerät bleibt mit seinen Logs."),
               actor=user.username, target=hostname)
    return {"status": "ok", "device_deleted": deleted}


@router.get("/agents/{uid}/telemetry")
async def telemetry(uid: str, hours: int = 24, db: AsyncSession = Depends(get_db),
                    _: User = Depends(get_current_user)) -> List[dict]:
    agent = await _agent(db, uid)
    since = _now() - timedelta(hours=max(1, min(hours, 24 * 14)))
    rows = (await db.execute(select(Telemetry).where(
        Telemetry.agent_id == agent.id, Telemetry.ts >= since).order_by(Telemetry.ts))).scalars()
    return [{"ts": rmm.iso(t.ts), "cpu": t.cpu_percent, "mem": t.mem_percent,
             "mem_used_mb": t.mem_used_mb, "mem_total_mb": t.mem_total_mb,
             "uptime": t.uptime_seconds, "disk": t.disk or [], "top_procs": t.top_procs or []}
            for t in rows]


@router.post("/agents/{uid}/commands", status_code=201)
async def create_command(uid: str, data: CommandIn, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    return await _queue(db, agent, user, shell=data.shell, command=data.command, timeout=data.timeout)


@router.get("/agents/{uid}/commands")
async def list_commands(uid: str, limit: int = 50, db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_user)) -> List[dict]:
    agent = await _agent(db, uid)
    rows = (await db.execute(select(Command).where(Command.agent_id == agent.id)
                             .order_by(Command.id.desc()).limit(max(1, min(limit, 500))))).scalars()
    return [_command(c) for c in rows]


# ==============================================================================
# Skripte
# ==============================================================================

@router.get("/scripts")
async def list_scripts(db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(Script).order_by(Script.category, Script.name))).scalars()
    return [_script(s) for s in rows]


@router.post("/scripts", status_code=201)
async def create_script(data: ScriptIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    s = Script(**data.model_dump(), created_by=user.username)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    rmm.record("script.created", f"Skript „{s.name}“ angelegt.", actor=user.username, target=s.name)
    return _script(s)


@router.put("/scripts/{script_id}")
async def update_script(script_id: int, data: ScriptIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    s = await db.get(Script, script_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Skript.")
    for key, value in data.model_dump().items():
        setattr(s, key, value)
    await db.commit()
    rmm.record("script.updated", f"Skript „{s.name}“ geändert.", actor=user.username, target=s.name)
    return _script(s)


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_admin)) -> dict:
    s = await db.get(Script, script_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Skript.")
    await db.delete(s)
    await db.commit()
    rmm.record("script.deleted", f"Skript „{s.name}“ gelöscht.", actor=user.username, target=s.name)
    return {"status": "ok"}


@router.post("/agents/{uid}/run-script/{script_id}", status_code=201)
async def run_script(uid: str, script_id: int, timeout: Optional[int] = None,
                     db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_admin)) -> dict:
    agent = await _agent(db, uid)
    s = await db.get(Script, script_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unbekanntes Skript.")
    return await _queue(db, agent, user, shell=s.shell, command=s.body,
                        timeout=timeout or s.default_timeout, source=f"Skript: {s.name}")


# ==============================================================================
# Checks
# ==============================================================================

def _validate_check(data: CheckIn) -> None:
    if data.type not in CHECK_TYPES:
        raise HTTPException(status_code=422, detail=f"Unbekannter Check-Typ: {data.type}")
    if data.scope_type not in {"global", "site", "agent"}:
        raise HTTPException(status_code=422, detail="Geltungsbereich: global, site oder agent.")


@router.get("/checks")
async def list_checks(db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)) -> List[dict]:
    return [_check(c) for c in (await db.execute(select(Check).order_by(Check.name))).scalars()]


@router.post("/checks", status_code=201)
async def create_check(data: CheckIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    _validate_check(data)
    c = Check(**data.model_dump())
    db.add(c)
    await db.commit()
    await db.refresh(c)
    rmm.record("check.created", f"Check „{c.name}“ angelegt.", actor=user.username, target=c.name)
    return _check(c)


@router.put("/checks/{check_id}")
async def update_check(check_id: int, data: CheckIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    _validate_check(data)
    c = await db.get(Check, check_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Unbekannter Check.")
    for key, value in data.model_dump().items():
        setattr(c, key, value)
    await db.commit()
    rmm.record("check.updated", f"Check „{c.name}“ geändert.", actor=user.username, target=c.name)
    return _check(c)


@router.delete("/checks/{check_id}")
async def delete_check(check_id: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    c = await db.get(Check, check_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Unbekannter Check.")
    await db.execute(delete(RmmAlert).where(RmmAlert.kind == f"check:{check_id}"))
    await db.delete(c)
    await db.commit()
    rmm.record("check.deleted", f"Check „{c.name}“ gelöscht.", actor=user.username, target=c.name)
    return {"status": "ok"}


@router.get("/checks/results")
async def check_results_overview(uid: Optional[str] = None, db: AsyncSession = Depends(get_db),
                                 _: User = Depends(get_current_user)) -> List[dict]:
    q = (select(CheckResult, Check, Agent)
         .join(Check, Check.id == CheckResult.check_id)
         .join(Agent, Agent.id == CheckResult.agent_id)
         .order_by(Agent.hostname, Check.name))
    if uid:
        q = q.where(Agent.agent_uid == uid)
    return [{"check_id": c.id, "agent_uid": a.agent_uid, "hostname": a.hostname, "name": c.name,
             "type": c.type, "status": r.status, "value": r.value, "output": r.output,
             "ts": rmm.iso(r.ts)} for r, c, a in (await db.execute(q)).all()]


# ==============================================================================
# Patches
# ==============================================================================

def _patch(report: Optional[PatchReport]) -> dict:
    if report is None:
        return {"count": 0, "updates": [], "scanned_at": None, "last_apply_at": None,
                "last_apply_output": None}
    return {"count": report.count, "updates": report.updates or [],
            "scanned_at": rmm.iso(report.scanned_at), "last_apply_at": rmm.iso(report.last_apply_at),
            "last_apply_output": report.last_apply_output}


@router.get("/patches")
async def patches_overview(db: AsyncSession = Depends(get_db),
                           _: User = Depends(get_current_user)) -> List[dict]:
    rows = (await db.execute(select(PatchReport, Agent).join(Agent, Agent.id == PatchReport.agent_id)
                             .order_by(PatchReport.count.desc(), Agent.hostname))).all()
    return [{"agent_uid": a.agent_uid, "hostname": a.hostname, "platform": a.platform,
             "site": a.site or "Standard", **_patch(r)} for r, a in rows]


@router.get("/agents/{uid}/patches")
async def agent_patches(uid: str, db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_user)) -> dict:
    agent = await _agent(db, uid)
    return _patch((await db.execute(select(PatchReport).where(
        PatchReport.agent_id == agent.id))).scalar_one_or_none())


@router.post("/agents/{uid}/patches/{action}", status_code=201)
async def patch_action(uid: str, action: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_admin)) -> dict:
    """scan: sofort suchen. apply: alle offenen Updates jetzt installieren
    (gilt als Freigabe; läuft über das Patch-Center mit Sicherung und Prüfung)."""
    if action not in ("scan", "apply"):
        raise HTTPException(status_code=404, detail="Aktion: scan oder apply.")
    agent = await _agent(db, uid)
    if action == "scan":
        _, settings = await patchmgmt.policy_for(db, agent)
        ids = await patchmgmt.queue_scan(db, agent, settings, user.username)
        await db.commit()
        rmm.record("command.patch_scan", f"{user.username} → {agent.hostname}: Patch-Suche",
                   actor=user.username, target=agent.hostname)
        return {"status": "queued", "commands": ids}
    open_ids = [i for (i,) in (await db.execute(select(PatchItem.id).where(
        PatchItem.agent_id == agent.id,
        PatchItem.status.in_(("auto", "approved", "needs_approval", "review"))))).all()]
    try:
        run = await patchmgmt.install_now(db, agent, open_ids, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return patchmgmt.public_run(run, agent)


# ==============================================================================
# Alarme, Übersicht, Einstellungen
# ==============================================================================

@router.get("/alerts")
async def list_alerts(active: bool = True, limit: int = 200, db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)) -> List[dict]:
    q = (select(RmmAlert, Agent.hostname).join(Agent, Agent.id == RmmAlert.agent_id, isouter=True)
         .order_by(RmmAlert.created_at.desc()).limit(max(1, min(limit, 1000))))
    if active:
        q = q.where(RmmAlert.active.is_(True))
    return [_alert(a, h or a.subject or "") for a, h in (await db.execute(q)).all()]


@router.post("/alerts/{alert_id}/resolve")
async def resolve(alert_id: int, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_admin)) -> dict:
    a = await db.get(RmmAlert, alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Unbekannter Alarm.")
    a.active, a.resolved_at = False, _now()
    await db.commit()
    rmm.record("alert.acknowledged", f"Alarm quittiert: {a.message}", actor=user.username)
    return {"status": "ok"}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)) -> dict:
    th = await rmm.get_thresholds(db)
    agents = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
    online = sum(1 for a in agents if rmm.is_online(a, th["offline_after"]))
    by_platform: Dict[str, int] = {}
    by_site: Dict[str, int] = {}
    for a in agents:
        by_platform[a.platform or "unbekannt"] = by_platform.get(a.platform or "unbekannt", 0) + 1
        by_site[a.site or "Standard"] = by_site.get(a.site or "Standard", 0) + 1
    alerts_active = (await db.execute(select(func.count()).select_from(RmmAlert)
                                      .where(RmmAlert.active.is_(True)))).scalar_one()
    pending = (await db.execute(select(func.count()).select_from(Command)
                                .where(Command.status.in_(("pending", "running"))))).scalar_one()
    patches = (await db.execute(select(func.coalesce(func.sum(PatchReport.count), 0)))).scalar_one()
    return {"agents_total": len(agents), "agents_online": online,
            "agents_offline": len(agents) - online, "alerts_active": alerts_active,
            "commands_pending": pending, "patches_pending": int(patches or 0),
            "by_platform": by_platform, "by_site": by_site}


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_user)) -> dict:
    return {"thresholds": await rmm.get_thresholds(db),
            "checkin_interval": rmm.CHECKIN_INTERVAL}


@router.put("/settings/thresholds")
async def put_thresholds(data: ThresholdsIn, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_admin)) -> dict:
    saved = await rmm.set_thresholds(db, data.model_dump())
    rmm.record("settings.thresholds", "RMM-Schwellen geändert.", actor=user.username, **saved)
    return saved
