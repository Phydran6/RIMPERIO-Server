# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Wachhund: merkt, wenn der Sammler selbst nicht mehr sammelt
# ==============================================================================
"""
Der Wachhund.

Er beantwortet jede Minute eine einzige Frage: **sammelt RIMPERIO gerade?**

Dazu sieht er nach

* ob die Datenbank antwortet (ohne sie wird nichts gespeichert),
* wie voll die Platte ist (der Plattenwaechter raeumt, der Wachhund meldet),
* wann zuletzt ein Log ankam (Funkstille = irgendwo hakt es),
* ob ein Geraet flutet (eine Endlosschleife wie am 2026-10-10),
* ob Geraete verstummt sind, die bisher geliefert haben.

Jede Aenderung geht ueber `alerts.dispatch` nach aussen (Mail, Telegram,
Webhook) - und ins Systemtagebuch, sofern die Datenbank das zulaesst. Wird ein
Problem behoben, kommt eine Entwarnung.

Der Zustand liegt im Speicher und wird von `/api/health` mitgeliefert. So sieht
auch ein externer Monitor (Uptime Kuma o. ae.), ob RIMPERIO wirklich arbeitet -
und nicht nur, ob der Webserver antwortet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import func, select, text

from . import alerts, diskguard, journal
from .models import Agent, Log

logger = logging.getLogger("rimperio.watchdog")

CHECK_INTERVAL = max(30, int(os.getenv("WATCHDOG_INTERVAL", "60")))
DB_TIMEOUT = 5.0
# So viele Fehlversuche hintereinander, bevor "Datenbank weg" gemeldet wird -
# ein einzelner Aussetzer (Neustart, kurze Last) ist noch kein Alarm.
DB_FAILS_BEFORE_ALERT = 2
FLOOD_WINDOW_MINUTES = 5

_state: Dict[str, Any] = {
    "checked_at": None,
    "database": None,              # True / False / None (noch nicht geprueft)
    "database_error": "",
    "database_down_since": None,
    "last_log_at": None,
    "silent": False,
    "disk_percent": None,
    "disk_alerted": False,
    "ingest_blocked": False,
    "floods": {},                  # hostname -> Logs pro Minute
    "offline": [],                 # verstummte Geraete
}
_db_failures = 0
_offline_known: set = set()
_agents_initialized = False


def snapshot() -> dict:
    """Der letzte Stand fuer /api/health und die Oberflaeche."""
    data = dict(_state)
    for key in ("checked_at", "database_down_since", "last_log_at"):
        if isinstance(data.get(key), datetime):
            data[key] = data[key].isoformat()
    data["floods"] = dict(_state["floods"])
    data["offline"] = list(_state["offline"])
    return data


def problems() -> list:
    """Was gerade nicht stimmt - leer heisst: RIMPERIO sammelt."""
    found = []
    if _state["database"] is False:
        found.append("database")
    if _state["ingest_blocked"]:
        found.append("ingest_blocked")
    if _state["silent"]:
        found.append("silence")
    if _state["floods"]:
        found.append("flood")
    return found


def _fmt(moment: Optional[datetime]) -> str:
    return moment.strftime("%d.%m.%Y %H:%M UTC") if moment else "unbekannt"


# =============================================================================
# Einzelne Pruefungen
# =============================================================================
async def _check_database() -> None:
    global _db_failures
    from .database import async_session

    error = ""
    try:
        async with async_session() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=DB_TIMEOUT)
        ok = True
    except Exception as exc:
        ok = False
        error = str(exc) or exc.__class__.__name__

    now = datetime.utcnow()
    if ok:
        was_down = _state["database"] is False
        down_since = _state["database_down_since"]
        _db_failures = 0
        _state.update(database=True, database_error="", database_down_since=None)
        if was_down:
            alerts.forget("db_down")
            minutes = int((now - down_since).total_seconds() // 60) if down_since else 0
            await alerts.dispatch(
                "recovered", "Datenbank wieder erreichbar",
                f"Die Datenbank antwortet wieder (Ausfall etwa {minutes} min). "
                f"Logs werden wieder gespeichert.", level="info", key="recovered:db")
            await journal.write(
                category="system", event="watchdog.db.recovered", level="notice",
                message=f"Datenbank wieder erreichbar nach etwa {minutes} min.", actor="system")
        return

    _db_failures += 1
    _state["database_error"] = error[:300]
    if _db_failures < DB_FAILS_BEFORE_ALERT:
        return
    if _state["database"] is not False:
        _state["database"] = False
        _state["database_down_since"] = now
        logger.critical("Datenbank nicht erreichbar: %s", error)
    disk = diskguard.usage()
    hint = (" Die Platte ist voll – das ist sehr wahrscheinlich die Ursache."
            if disk["percent"] >= diskguard.INGEST_STOP else "")
    await alerts.dispatch(
        "db_down", "Datenbank nicht erreichbar – es wird NICHTS gespeichert",
        (f"Seit {_fmt(_state['database_down_since'])} antwortet die Datenbank nicht.{hint}\n"
         f"Fehler: {error[:200]}\nPlatte: {disk['percent']} % belegt, "
         f"{diskguard._human(disk['free_bytes'])} frei."),
        level="critical", key="db_down")


async def _check_disk() -> None:
    info = diskguard.usage()
    percent = info["percent"]
    blocked = percent >= diskguard.INGEST_STOP
    _state.update(disk_percent=percent, ingest_blocked=blocked)

    if percent >= diskguard.THRESHOLD_HIGH:
        level = "critical" if blocked else "warning"
        title = ("Platte voll – Annahme gestoppt" if blocked
                 else f"Platte bei {percent} %")
        text_ = (f"Die Platte ist zu {percent} % belegt, "
                 f"{diskguard._human(info['free_bytes'])} frei. ")
        text_ += ("RIMPERIO nimmt gerade KEINE Logs an (HTTPS-Agents puffern bei sich und "
                  "liefern nach). " if blocked else
                  "Der Plattenwächter kürzt bereits die Aufbewahrung. ")
        text_ += "Bitte Platz schaffen oder die Aufbewahrung verkürzen."
        # Zwei Schluessel: die Steigerung zu "Annahme gestoppt" soll trotz
        # Sperrfrist sofort raus.
        await alerts.dispatch("disk", title, text_, level=level,
                              key="disk:blocked" if blocked else "disk:high",
                              detail={"percent": percent})
        _state["disk_alerted"] = True
    elif _state["disk_alerted"] and percent < diskguard.THRESHOLD_WARN:
        _state["disk_alerted"] = False
        alerts.forget("disk:high")
        alerts.forget("disk:blocked")
        await alerts.dispatch("recovered", "Platte wieder im grünen Bereich",
                              f"Die Platte ist nur noch zu {percent} % belegt.",
                              level="info", key="recovered:disk")


async def _check_logs(session, config: dict) -> None:
    now = datetime.utcnow()

    # Juengster Log - ueber den Index auf timestamp, nicht ueber die ganze
    # Tabelle. Zeitstempel aus der Zukunft (falsche Uhr am Geraet) zaehlen
    # nicht, sonst wuerde eine Funkstille dahinter verschwinden.
    newest = (await session.execute(
        select(func.max(Log.timestamp)).where(Log.timestamp <= now + timedelta(minutes=5))
    )).scalar()
    _state["last_log_at"] = newest

    silence = int(config.get("silence_minutes") or 0)
    if silence and newest is not None:
        quiet = now - newest > timedelta(minutes=silence)
        if quiet and not _state["silent"]:
            _state["silent"] = True
            await alerts.dispatch(
                "silence", "Keine neuen Logs mehr",
                (f"Seit {_fmt(newest)} ist kein Log mehr angekommen "
                 f"(Grenze: {silence} min). Agents, Netz und Datenbank prüfen."),
                level="warning", key="silence")
            await journal.record(
                session, category="system", event="watchdog.silence", level="warning",
                message=f"Seit {_fmt(newest)} keine neuen Logs.", actor="system")
        elif not quiet and _state["silent"]:
            _state["silent"] = False
            alerts.forget("silence")
            await alerts.dispatch("recovered", "Logs kommen wieder an",
                                  f"Der letzte Log kam um {_fmt(newest)}.",
                                  level="info", key="recovered:silence")

    # Flut: wer schickt mehr als erlaubt? Nur echte Gegenwart zaehlt - ein
    # Agent, der Rueckstand mit alten Zeitstempeln nachliefert, flutet nicht.
    per_minute = int(config.get("flood_per_minute") or 0)
    floods: Dict[str, int] = {}
    if per_minute:
        since = now - timedelta(minutes=FLOOD_WINDOW_MINUTES)
        rows = (await session.execute(
            select(Log.hostname, func.count())
            .where(Log.timestamp >= since, Log.timestamp <= now + timedelta(minutes=1))
            .group_by(Log.hostname)
            .having(func.count() > per_minute * FLOOD_WINDOW_MINUTES)
        )).all()
        floods = {host or "?": int(count // FLOOD_WINDOW_MINUTES) for host, count in rows}
    for host, rate in floods.items():
        if host not in _state["floods"]:
            await journal.record(
                session, category="system", event="watchdog.flood", level="warning",
                message=f"{host} schickt etwa {rate} Logs pro Minute.", actor="system",
                target=host, detail={"per_minute": rate})
        await alerts.dispatch(
            "flood", f"{host} flutet",
            (f"{host} schickt gerade etwa {rate:,} Logs pro Minute "
             f"(Grenze: {per_minute:,}). Das kann eine Fehlerschleife am Gerät sein – "
             f"oder ein echtes Problem, das genau jetzt passiert.").replace(",", "."),
            level="warning", key=f"flood:{host}", detail={"host": host, "per_minute": rate})
    for host in set(_state["floods"]) - set(floods):
        alerts.forget(f"flood:{host}")
        await alerts.dispatch("recovered", f"{host} wieder normal",
                              f"{host} schickt wieder im üblichen Rahmen.",
                              level="info", key=f"recovered:flood:{host}")
    _state["floods"] = floods


async def _check_agents(session, config: dict) -> None:
    """Geraete, die bisher geliefert haben und jetzt schweigen."""
    global _offline_known
    now = datetime.utcnow()
    limit = timedelta(minutes=int(config.get("agent_offline_minutes") or 30))
    rows = (await session.execute(
        select(Agent.hostname, Agent.last_seen, Agent.extra_data)
        .where(Agent.last_seen.is_not(None),
               Agent.last_seen >= now - timedelta(days=7))
    )).all()

    offline = set()
    for hostname, last_seen, extra in rows:
        if isinstance(extra, dict) and extra.get("decommissioned"):
            continue
        if now - last_seen > limit:
            offline.add(hostname)
    _state["offline"] = sorted(offline)

    global _agents_initialized
    if not _agents_initialized:
        # Erster Blick nach dem Start: nur merken. Sonst kaeme nach jedem
        # Update eine Sammelmeldung ueber Geraete, die schon laenger schweigen.
        _agents_initialized = True
        _offline_known = offline
        return
    new = offline - _offline_known
    back = _offline_known - offline
    _offline_known = offline
    if new:
        await alerts.dispatch(
            "agent_offline", f"{len(new)} Gerät(e) melden sich nicht mehr",
            "Seit über {} min nichts mehr von: {}".format(
                int(limit.total_seconds() // 60), ", ".join(sorted(new))),
            level="warning", key="agent_offline:" + ",".join(sorted(new)))
    if back:
        await alerts.dispatch(
            "recovered", f"{len(back)} Gerät(e) wieder da",
            "Liefern wieder: " + ", ".join(sorted(back)),
            level="info", key="recovered:agents:" + ",".join(sorted(back)))


# =============================================================================
# Der Lauf
# =============================================================================
async def check_once() -> dict:
    from .database import async_session

    await _check_database()
    await _check_disk()
    if _state["database"]:
        try:
            async with async_session() as session:
                config = await alerts.refresh(session)
                await _check_logs(session, config)
                await _check_agents(session, config)
        except Exception as exc:                                    # defensiv
            logger.warning("Wachhund: Log-Pruefung unvollstaendig: %s", exc)
    _state["checked_at"] = datetime.utcnow()
    return snapshot()


async def watch_task() -> None:
    logger.info("Wachhund läuft (alle %ss)", CHECK_INTERVAL)
    # Erst hochfahren lassen: direkt nach dem Start ist die Datenbank oft noch
    # nicht so weit, und das ist kein Alarm wert.
    await asyncio.sleep(30)
    while True:
        try:
            await check_once()
        except Exception as exc:                                    # defensiv
            logger.error("Wachhund: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL)
