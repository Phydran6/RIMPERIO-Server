# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - RMM-Kern: Schema, Schwellen, Alarme, Offline-Wächter
# ==============================================================================
"""
Fernwartung über den RIMPERIO-Agent.

Ein Gerät ist eine Zeile in `agents` — dieselbe, in der auch seine Logs hängen.
Meldet sich der RMM-Agent an, wird ein Gerät mit gleichem Hostnamen
weiterverwendet statt ein zweites anzulegen.

Alarme landen in `rmm_alerts` (sichtbar in der Oberfläche) und gehen
zusätzlich über das allgemeine Meldesystem (alerts.py: Mail, Telegram,
Webhook/n8n) hinaus — dort lässt sich je Ereignisart ein- und ausschalten,
was gemeldet wird.
"""

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import alerts, journal
from .database import Base, async_session, engine
from .models import RMM_TABLES, Agent, RmmAlert, Setting

logger = logging.getLogger("rimperio.rmm")

CHECKIN_INTERVAL = 60                       # Sekunden zwischen zwei Check-ins
TELEMETRY_RETENTION_DAYS = 14
SWEEP_SECONDS = 60
THRESHOLD_KEY = "rmm_thresholds"
DEFAULT_THRESHOLDS = {"cpu_percent": 90.0, "mem_percent": 90.0,
                      "disk_percent": 90.0, "offline_after": 300}

# Neue Spalten an `agents` — idempotent, für bestehende Installationen.
_AGENT_COLUMNS = [
    "agent_uid VARCHAR(64)", "rmm_token_hash VARCHAR(64)", "site VARCHAR(128)",
    "description VARCHAR(255)", "os VARCHAR(128)", "platform VARCHAR(32)",
    "arch VARCHAR(32)", "agent_version VARCHAR(64)", "public_ip VARCHAR(64)",
    "enrolled_at TIMESTAMP", "rmm_last_seen TIMESTAMP", "boot_time TIMESTAMP",
    "last_cpu DOUBLE PRECISION", "last_mem DOUBLE PRECISION",
    "last_disk DOUBLE PRECISION", "inventory JSON",
]


async def ensure_schema() -> None:
    """Legt die RMM-Tabellen an und erweitert `agents`. Wirft nie."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=RMM_TABLES))
            for col in _AGENT_COLUMNS:
                await conn.exec_driver_sql(f"ALTER TABLE agents ADD COLUMN IF NOT EXISTS {col}")
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_agent_uid ON agents (agent_uid)")
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_agents_rmm_token_hash ON agents (rmm_token_hash)")
        logger.info("RMM-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("RMM-Schema nicht angelegt: %s", exc)


# --- Token ---------------------------------------------------------------------

def new_agent_token() -> tuple[str, str]:
    """(Klartext, SHA-256). Der Klartext geht genau einmal an den Agent."""
    token = "rma_" + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_uid() -> str:
    return secrets.token_hex(16)


# --- Einstellungen -------------------------------------------------------------

async def get_thresholds(db: AsyncSession) -> dict:
    row = (await db.execute(select(Setting).where(Setting.key == THRESHOLD_KEY))).scalar_one_or_none()
    out = dict(DEFAULT_THRESHOLDS)
    if row is not None and isinstance(row.value, dict):
        out.update({k: v for k, v in row.value.items() if k in out})
    return out


async def set_thresholds(db: AsyncSession, values: dict) -> dict:
    merged = {**DEFAULT_THRESHOLDS, **{k: v for k, v in values.items() if k in DEFAULT_THRESHOLDS}}
    row = (await db.execute(select(Setting).where(Setting.key == THRESHOLD_KEY))).scalar_one_or_none()
    if row is None:
        db.add(Setting(key=THRESHOLD_KEY, value=merged,
                       description="RMM: Alarmschwellen und Offline-Grenze"))
    else:
        row.value = merged
    await db.commit()
    return merged


# --- Status ---------------------------------------------------------------------

def iso(dt: Optional[datetime]) -> Optional[str]:
    """UTC-Zeitpunkt eindeutig als ISO mit 'Z' (die DB speichert ohne Zone)."""
    return dt.isoformat(timespec="seconds") + "Z" if dt else None




def is_online(agent: Agent, offline_after: int) -> bool:
    seen = agent.rmm_last_seen
    return bool(seen) and (datetime.utcnow() - seen) <= timedelta(seconds=offline_after)


def agent_public(agent: Agent, offline_after: int, detail: bool = False) -> dict:
    out = {
        "id": agent.id, "agent_uid": agent.agent_uid, "hostname": agent.hostname,
        "site": agent.site or "Standard", "description": agent.description or "",
        "os": agent.os or "", "platform": agent.platform or "", "arch": agent.arch or "",
        "version": agent.agent_version or "", "public_ip": agent.public_ip,
        "local_ip": agent.ip_address, "enrolled_at": iso(agent.enrolled_at),
        "last_seen": iso(agent.rmm_last_seen), "last_cpu": agent.last_cpu,
        "last_mem": agent.last_mem, "last_disk": agent.last_disk,
        "online": is_online(agent, offline_after),
    }
    if detail:
        out["boot_time"] = iso(agent.boot_time)
        out["inventory"] = agent.inventory
    return out


# --- Alarme --------------------------------------------------------------------

_EVENT_FOR_KIND = {"offline": "rmm_offline", "cpu": "rmm_threshold",
                   "mem": "rmm_threshold", "disk": "rmm_threshold"}


def _event(kind: str) -> str:
    return "rmm_check" if kind.startswith("check:") else _EVENT_FOR_KIND.get(kind, "rmm_check")


async def _active(db: AsyncSession, agent_id: Optional[int], kind: str) -> Optional[RmmAlert]:
    return (await db.execute(select(RmmAlert).where(
        RmmAlert.agent_id == agent_id, RmmAlert.kind == kind, RmmAlert.active.is_(True)
    ))).scalar_one_or_none()


async def raise_alert(db: AsyncSession, agent: Agent, kind: str, severity: str, message: str) -> None:
    existing = await _active(db, agent.id, kind)
    if existing is not None:
        existing.message, existing.severity = message, severity
        return
    db.add(RmmAlert(agent_id=agent.id, kind=kind, severity=severity, message=message))
    level = "critical" if severity == "critical" else "warning"
    await alerts.dispatch(_event(kind), f"RIMPERIO: {agent.hostname}", message, level=level,
                          key=f"rmm:{agent.id}:{kind}",
                          detail={"agent_uid": agent.agent_uid, "hostname": agent.hostname,
                                  "kind": kind, "severity": severity})


async def resolve_alert(db: AsyncSession, agent: Agent, kind: str) -> None:
    existing = await _active(db, agent.id, kind)
    if existing is None:
        return
    existing.active = False
    existing.resolved_at = datetime.utcnow()
    alerts.forget(f"rmm:{agent.id}:{kind}")
    await alerts.dispatch("recovered", f"RIMPERIO: {agent.hostname}",
                          f"Behoben: {existing.message}", level="info",
                          key=f"rmm:{agent.id}:{kind}:ok")


async def evaluate_thresholds(db: AsyncSession, agent: Agent, cpu: float, mem: float,
                              disk: float, th: dict) -> None:
    for kind, value, label in (("cpu", cpu, "CPU"), ("mem", mem, "RAM"), ("disk", disk, "Platte")):
        limit = float(th[f"{kind}_percent"])
        if value >= limit:
            await raise_alert(db, agent, kind, "warning",
                              f"{agent.hostname}: {label} bei {value:.0f} % (Schwelle {limit:.0f} %)")
        else:
            await resolve_alert(db, agent, kind)


# --- Hintergrund ---------------------------------------------------------------

async def sweep_once() -> None:
    """Offline-Alarme pflegen und alte Telemetrie aufräumen."""
    from .models import Telemetry
    from sqlalchemy import delete
    async with async_session() as db:
        th = await get_thresholds(db)
        rows = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
        for agent in rows:
            if is_online(agent, th["offline_after"]):
                await resolve_alert(db, agent, "offline")
            else:
                await raise_alert(db, agent, "offline", "critical",
                                  f"{agent.hostname}: seit über {th['offline_after']} s kein Lebenszeichen.")
        cutoff = datetime.utcnow() - timedelta(days=TELEMETRY_RETENTION_DAYS)
        await db.execute(delete(Telemetry).where(Telemetry.ts < cutoff))
        await db.commit()


async def sweep_loop() -> None:
    while True:
        try:
            await sweep_once()
        except Exception as exc:                                    # defensiv
            logger.warning("RMM-Wächter: %s", exc)
        await asyncio.sleep(SWEEP_SECONDS)


def record(event: str, message: str, actor: str = "system", target: str = "", **detail) -> None:
    """Eintrag ins Systemtagebuch (Kategorie rmm), ohne zu warten."""
    journal.fire(category="rmm", event=event, message=message, actor=actor,
                 target=target, detail=detail or {})
