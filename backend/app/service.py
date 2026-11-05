"""
Datei:        service.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Logik, die mehrere Routen teilen: Online-Zustand berechnen,
              Agents in das Ausgabeformat überführen, Laufzeiteinstellungen
              lesen/schreiben und aus Telemetrie Alarme ableiten (erzeugen und
              von selbst wieder auflösen).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .config import settings as cfg
from .models import Agent, Alert, Setting, Telemetry
from .schemas import AgentOut, ThresholdSettings


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_online(last_seen: datetime | None, offline_after: int) -> bool:
    last_seen = _aware(last_seen)
    if last_seen is None:
        return False
    return (datetime.now(timezone.utc) - last_seen) <= timedelta(seconds=offline_after)


def to_agent_out(agent: Agent, offline_after: int) -> AgentOut:
    out = AgentOut.model_validate(agent)
    out.online = is_online(agent.last_seen, offline_after)
    return out


# --- Einstellungen ---------------------------------------------------------
async def get_setting(session: AsyncSession, key: str, default=None):
    row = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    return row.value if row is not None else default


async def set_setting(session: AsyncSession, key: str, value) -> None:
    row = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def get_thresholds(session: AsyncSession) -> ThresholdSettings:
    raw = await get_setting(session, "thresholds")
    if isinstance(raw, dict):
        return ThresholdSettings(**raw)
    return ThresholdSettings(offline_after=cfg.rimperio_offline_after)


async def get_offline_after(session: AsyncSession) -> int:
    return (await get_thresholds(session)).offline_after


# --- Alarme ----------------------------------------------------------------
async def _active_alert(session: AsyncSession, agent_id: int, kind: str) -> Alert | None:
    return (
        await session.execute(
            select(Alert).where(
                Alert.agent_id == agent_id, Alert.kind == kind, Alert.active.is_(True)
            )
        )
    ).scalar_one_or_none()


async def raise_alert(
    session: AsyncSession, agent_id: int | None, kind: str, severity: str, message: str
) -> None:
    if agent_id is not None:
        existing = await _active_alert(session, agent_id, kind)
        if existing is not None:
            existing.message = message
            existing.severity = severity
            return
    session.add(Alert(agent_id=agent_id, kind=kind, severity=severity, message=message))
    await events.dispatch(
        "alert.raised",
        {"agent_id": agent_id, "kind": kind, "severity": severity, "message": message},
    )


async def resolve_alert(session: AsyncSession, agent_id: int, kind: str) -> None:
    existing = await _active_alert(session, agent_id, kind)
    if existing is not None:
        existing.active = False
        existing.resolved_at = datetime.now(timezone.utc)
        await events.dispatch(
            "alert.resolved",
            {"agent_id": agent_id, "kind": kind, "message": existing.message},
        )


async def evaluate_thresholds(
    session: AsyncSession, agent: Agent, tel: Telemetry, th: ThresholdSettings
) -> None:
    """Prüft eine frische Telemetrie gegen die Schwellen und pflegt Alarme."""
    # CPU
    if tel.cpu_percent >= th.cpu_percent:
        await raise_alert(
            session, agent.id, "cpu", "warning",
            f"{agent.hostname}: CPU bei {tel.cpu_percent:.0f}% (Schwelle {th.cpu_percent:.0f}%)",
        )
    else:
        await resolve_alert(session, agent.id, "cpu")

    # Arbeitsspeicher
    if tel.mem_percent >= th.mem_percent:
        await raise_alert(
            session, agent.id, "mem", "warning",
            f"{agent.hostname}: RAM bei {tel.mem_percent:.0f}% (Schwelle {th.mem_percent:.0f}%)",
        )
    else:
        await resolve_alert(session, agent.id, "mem")

    # Platten — kritischster Mountpunkt zählt
    worst = max((d.get("percent", 0.0) for d in (tel.disk or [])), default=0.0)
    if worst >= th.disk_percent:
        await raise_alert(
            session, agent.id, "disk", "warning",
            f"{agent.hostname}: Platte bei {worst:.0f}% (Schwelle {th.disk_percent:.0f}%)",
        )
    else:
        await resolve_alert(session, agent.id, "disk")


async def sweep_offline(session: AsyncSession) -> int:
    """Hebt für alle Agents, die zu lange still sind, einen Offline-Alarm; für
    alle, die sich wieder melden, löst er ihn auf. Gibt die Zahl der Offline-
    Agents zurück. Wird periodisch vom Hintergrunddienst gerufen."""
    offline_after = await get_offline_after(session)
    agents = (await session.execute(select(Agent))).scalars().all()
    offline = 0
    for agent in agents:
        if is_online(agent.last_seen, offline_after):
            await resolve_alert(session, agent.id, "offline")
        else:
            offline += 1
            await raise_alert(
                session, agent.id, "offline", "critical",
                f"{agent.hostname}: seit {offline_after}s kein Lebenszeichen.",
            )
    await session.commit()
    return offline
