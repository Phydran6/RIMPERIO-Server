"""
Datei:        routes/ingest.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Die Schnittstelle, mit der die Agents sprechen — und nur sie.
              Anmeldung (enroll) mit gemeinsamem Geheimnis, danach alles mit dem
              persönlichen Agent-Token: Lebenszeichen + Messwerte (checkin),
              Inventur (inventory) und Ergebnisse ausgeführter Befehle (results).
              Der Check-in liefert in seiner Antwort offene Befehle zurück —
              der Agent holt ab, kein Weg von außen auf den Agent nötig.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import json

from ..config import settings as cfg
from ..database import get_session
from ..models import Agent, Check, CheckResult, Command, PatchReport, Telemetry
from ..schemas import (
    CheckinRequest,
    CheckinResponse,
    CheckResultIn,
    CheckSpec,
    CommandResultIn,
    EnrollRequest,
    EnrollResponse,
    PendingCommand,
)
from .. import events
from ..security import generate_agent_token, get_current_agent
from ..service import (
    evaluate_thresholds,
    get_setting,
    get_thresholds,
    raise_alert,
    resolve_alert,
)

router = APIRouter(prefix="/api/agents", tags=["agents-ingest"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/enroll", response_model=EnrollResponse)
async def enroll(data: EnrollRequest, session: AsyncSession = Depends(get_session)) -> EnrollResponse:
    """Erst-Anmeldung eines Agents. Das Enrollment-Geheimnis ist der Vertrauens-
    anker; stimmt es, bekommt der Agent sein persönliches Token. Meldet sich ein
    bereits bekannter Hostname erneut (Neuinstallation), wird sein Token
    erneuert statt ein Duplikat anzulegen."""
    expected = await get_setting(session, "enroll_secret", cfg.rimperio_enroll_secret)
    if not data.enroll_secret or data.enroll_secret != expected:
        raise HTTPException(status_code=401, detail="Enrollment-Geheimnis falsch.")

    token, token_hash = generate_agent_token()

    agent = (
        await session.execute(
            select(Agent).where(Agent.hostname == data.hostname, Agent.site == data.site)
        )
    ).scalar_one_or_none()

    if agent is None:
        import secrets

        agent = Agent(agent_uid=secrets.token_hex(16), hostname=data.hostname)
        session.add(agent)

    agent.token_hash = token_hash
    agent.site = data.site or "Standard"
    agent.description = data.description or agent.description or ""
    agent.os = data.os
    agent.platform = data.platform
    agent.arch = data.arch
    agent.version = data.version
    agent.local_ip = data.local_ip
    agent.last_seen = _now()

    await session.commit()
    await session.refresh(agent)

    return EnrollResponse(
        agent_uid=agent.agent_uid,
        agent_token=token,
        checkin_interval=cfg.rimperio_checkin_interval,
    )


@router.post("/checkin", response_model=CheckinResponse)
async def checkin(
    data: CheckinRequest,
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_session),
) -> CheckinResponse:
    agent.last_seen = _now()
    if data.version:
        agent.version = data.version
    if data.local_ip:
        agent.local_ip = data.local_ip
    if data.public_ip:
        agent.public_ip = data.public_ip

    th = await get_thresholds(session)

    if data.telemetry is not None:
        t = data.telemetry
        disk = [d.model_dump() for d in t.disk]
        tel = Telemetry(
            agent_id=agent.id,
            cpu_percent=t.cpu_percent,
            mem_percent=t.mem_percent,
            mem_used_mb=t.mem_used_mb,
            mem_total_mb=t.mem_total_mb,
            uptime_seconds=t.uptime_seconds,
            disk=disk,
            top_procs=[p.model_dump() for p in t.top_procs],
        )
        session.add(tel)
        agent.last_cpu = t.cpu_percent
        agent.last_mem = t.mem_percent
        agent.last_disk = max((d["percent"] for d in disk), default=0.0)
        if t.boot_time:
            agent.boot_time = t.boot_time
        await evaluate_thresholds(session, agent, tel, th)

    # Offene Befehle abholen und als „läuft" markieren.
    pending = (
        await session.execute(
            select(Command)
            .where(Command.agent_id == agent.id, Command.status == "pending")
            .order_by(Command.id)
        )
    ).scalars().all()

    out: list[PendingCommand] = []
    for c in pending:
        c.status = "running"
        c.picked_at = _now()
        out.append(
            PendingCommand(id=c.id, shell=c.shell, command=c.command, timeout=c.timeout, kind=c.kind)
        )

    # Welche Checks gelten für diesen Agent? (global / Standort / einzeln)
    all_checks = (await session.execute(select(Check).where(Check.enabled.is_(True)))).scalars().all()
    specs: list[CheckSpec] = []
    for ck in all_checks:
        applies = (
            ck.scope_type == "global"
            or (ck.scope_type == "site" and ck.scope_value == agent.site)
            or (ck.scope_type == "agent" and ck.scope_value == agent.agent_uid)
        )
        if applies:
            specs.append(CheckSpec(id=ck.id, type=ck.type, params=ck.params or {}, interval=ck.interval_sec))

    await session.commit()

    return CheckinResponse(
        interval=cfg.rimperio_checkin_interval,
        offline_after=th.offline_after,
        commands=out,
        checks=specs,
    )


@router.post("/inventory")
async def inventory(
    payload: dict = Body(...),
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Vollständige Inventur ablegen. Freie Form — der Agent bestimmt den Inhalt
    (OS, Hardware, installierte Software, Dienste, Netzwerk)."""
    agent.inventory = payload
    agent.last_seen = _now()
    for key in ("os", "platform", "arch"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            setattr(agent, key, val)
    await session.commit()
    return {"status": "ok"}


@router.post("/results")
async def results(
    items: list[CommandResultIn],
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ergebnisse ausgeführter Befehle zurückmelden."""
    updated = 0
    finished: list[dict] = []
    patch_available = 0
    for item in items:
        cmd = (
            await session.execute(
                select(Command).where(Command.id == item.id, Command.agent_id == agent.id)
            )
        ).scalar_one_or_none()
        if cmd is None:
            continue
        cmd.status = item.status if item.status in {"done", "error", "timeout"} else "done"
        cmd.exit_code = item.exit_code
        cmd.stdout = (item.stdout or "")[:200_000]
        cmd.stderr = (item.stderr or "")[:200_000]
        cmd.finished_at = _now()
        updated += 1
        finished.append(
            {"command_id": cmd.id, "kind": cmd.kind, "status": cmd.status,
             "exit_code": cmd.exit_code, "source": cmd.source, "agent_uid": agent.agent_uid}
        )

        # Patch-Ergebnisse auswerten.
        if cmd.kind in ("patch_scan", "patch_apply"):
            report = (
                await session.execute(select(PatchReport).where(PatchReport.agent_id == agent.id))
            ).scalar_one_or_none()
            if report is None:
                report = PatchReport(agent_id=agent.id)
                session.add(report)
            if cmd.kind == "patch_scan":
                try:
                    updates = json.loads(cmd.stdout or "[]")
                    if not isinstance(updates, list):
                        updates = []
                except Exception:
                    updates = []
                report.updates = updates
                report.count = len(updates)
                report.scanned_at = _now()
                if updates:
                    patch_available = len(updates)
            else:  # patch_apply
                report.last_apply_at = _now()
                report.last_apply_output = ((cmd.stdout or "") + "\n" + (cmd.stderr or ""))[:200_000]

    agent.last_seen = _now()
    await session.commit()

    for fin in finished:
        await events.dispatch("command.finished", fin)
    if patch_available:
        await events.dispatch(
            "patch.available",
            {"agent_uid": agent.agent_uid, "hostname": agent.hostname, "count": patch_available},
        )
    return {"status": "ok", "updated": updated}


@router.post("/check-results")
async def check_results(
    items: list[CheckResultIn],
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ergebnisse der auf dem Agent gelaufenen Checks zurückmelden. Fehlgeschlagene
    Checks lösen einen Alarm aus, bestandene lösen ihn wieder auf."""
    for item in items:
        check = await session.get(Check, item.check_id)
        if check is None:
            continue
        row = (
            await session.execute(
                select(CheckResult).where(
                    CheckResult.check_id == item.check_id, CheckResult.agent_id == agent.id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CheckResult(check_id=item.check_id, agent_id=agent.id)
            session.add(row)
        row.status = item.status
        row.value = (item.value or "")[:255]
        row.output = (item.output or "")[:20000] if item.output else None
        row.ts = _now()

        kind = f"check:{item.check_id}"
        if item.status == "fail":
            await raise_alert(
                session, agent.id, kind, check.severity,
                f"{agent.hostname}: Check „{check.name}" fehlgeschlagen ({item.value})",
            )
        elif item.status == "pass":
            await resolve_alert(session, agent.id, kind)

    agent.last_seen = _now()
    await session.commit()
    return {"status": "ok"}
