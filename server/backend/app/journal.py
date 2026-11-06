# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Systemtagebuch: was der Server selbst getan hat
# ==============================================================================
"""
Das Systemtagebuch.

Der Anspruch dahinter ist einfach und hart: **es darf auf diesem Server nichts
passieren, das hinterher niemand mehr nachvollziehen kann.** Ein "ich weiss
nicht, warum das passiert ist" soll es nicht geben.

Was hier hineingeht: jeder Eingriff, den RIMPERIO selbst vornimmt oder
entgegennimmt - Update, Rueckfall, Sicherung, Aufraeumen der Platte,
Terminal-Sitzung, Container an/aus, Anmeldung und abgelehnte Anmeldung,
geaenderte Einstellung, erzeugter oder zurueckgezogener Schluessel.

Warum eine eigene Tabelle und nicht die `logs`-Tabelle:

* Der Aufraeumlauf loescht alte `logs`. Die eigene Spur darf er dabei nicht
  mitnehmen - sonst fehlt ausgerechnet der Eintrag "Aufraeumlauf hat
  4.2 Millionen Zeilen geloescht".
* Diese Zeilen sind klein und selten. Sie ueberleben problemlos Jahre, waehrend
  `logs` in Tagen wieder umlaeuft.
* Sie sind strukturiert: Kategorie, Vorgang, Wer, Woran, Erfolg, Dauer. Danach
  laesst sich filtern, ohne Volltext zu durchsuchen.

Schreiben kostet nichts und darf nie etwas kaputt machen: schlaegt das
Schreiben fehl (Datenbank gerade weg - genau waehrend eines Updates der
Normalfall), wird das nur ins Anwendungsprotokoll gemeldet und der Aufrufer
laeuft weiter. Ein Tagebuch, das den Betrieb anhaelt, waere schlimmer als
keines.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .events import SYSTEM_EVENT, bus
from .models import SystemEvent

logger = logging.getLogger("rimperio.journal")

# Kategorien. Die Oberflaeche macht daraus die Filterleiste; unbekannte
# Kategorien sind erlaubt und landen unter "Sonstiges".
CATEGORIES: Dict[str, str] = {
    "auth": "Anmeldung",
    "token": "Schlüssel",
    "update": "Update",
    "backup": "Sicherung",
    "retention": "Aufräumen",
    "container": "Container",
    "shell": "Terminal",
    "settings": "Einstellungen",
    "agent": "Geräte",
    "discovery": "Netzwerksuche",
    "archiving": "Archivierung",
    "mail": "Mail",
    "ai": "KI-Auswertung",
    "system": "System",
}

LEVELS = ("debug", "info", "notice", "warning", "error", "critical")

# Wie lange das Tagebuch aufgehoben wird. Bewusst lang: die Zeilen sind winzig,
# und die Frage "was ist da vor drei Monaten passiert?" kommt spaet.
DEFAULT_RETENTION_DAYS = 365
MAX_ROWS = 200_000


def _clip(value: Optional[str], length: int) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value[:length] if value else None


async def record(session: AsyncSession, *, category: str, event: str, message: str,
                 level: str = "info", actor: str = "system", source_ip: str = "",
                 target: str = "", ok: bool = True, duration_ms: Optional[int] = None,
                 detail: Optional[Dict[str, Any]] = None,
                 commit: bool = True) -> Optional[SystemEvent]:
    """Schreibt eine Zeile ins Tagebuch. Wirft nie."""
    level = (level or "info").lower()
    if level not in LEVELS:
        level = "info"

    row = SystemEvent(
        at=datetime.utcnow(),
        category=_clip(category, 40) or "system",
        level=level,
        event=_clip(event, 80) or "unbekannt",
        message=(message or "").strip() or event,
        actor=_clip(actor, 100),
        source_ip=_clip(source_ip, 45),
        target=_clip(target, 200),
        ok=bool(ok),
        duration_ms=duration_ms,
        detail=detail or {},
    )
    try:
        session.add(row)
        if commit:
            await session.commit()
    except Exception as exc:                                        # defensiv
        logger.warning("Systemtagebuch nicht geschrieben (%s): %s", event, exc)
        try:
            await session.rollback()
        except Exception:
            pass
        return None

    # Offene Oberflaechen sollen den Eintrag sofort sehen - dafuer ist der
    # Ereignisverteiler schon da.
    try:
        bus.publish(SYSTEM_EVENT, {
            "at": row.at.isoformat(), "category": row.category, "level": row.level,
            "event": row.event, "message": row.message, "actor": row.actor,
            "target": row.target, "ok": row.ok,
        }, sticky=False)
    except Exception:                                               # defensiv
        pass

    # Zusaetzlich ins Anwendungsprotokoll - dort sucht man bei einem Problem
    # zuerst, und `docker logs` soll dieselbe Geschichte erzaehlen.
    log_at = {"critical": logging.CRITICAL, "error": logging.ERROR,
              "warning": logging.WARNING, "notice": logging.INFO,
              "info": logging.INFO, "debug": logging.DEBUG}[level]
    logger.log(log_at, "[%s] %s — %s (%s)", row.category, row.event, row.message,
               row.actor or "system")
    return row


async def write(**kwargs) -> None:
    """Wie `record`, aber mit eigener Sitzung - fuer Hintergrundaufgaben.

    Gedacht fuer alles, was keine Sitzung zur Hand hat (Wachhunde, Zeitplaene).
    """
    from .database import async_session
    try:
        async with async_session() as session:
            await record(session, **kwargs)
    except Exception as exc:                                        # defensiv
        logger.warning("Systemtagebuch nicht geschrieben: %s", exc)


def fire(**kwargs) -> None:
    """Wie `write`, aber ohne await - fuer Stellen, die nicht warten koennen.

    Der Eintrag wird als Aufgabe nebenher erledigt. Laeuft gerade keine
    Ereignisschleife (z.B. beim Hochfahren), faellt der Eintrag aus - dann
    steht er wenigstens im Anwendungsprotokoll.
    """
    try:
        asyncio.get_running_loop().create_task(write(**kwargs))
    except RuntimeError:
        logger.info("[%s] %s — %s", kwargs.get("category"), kwargs.get("event"),
                    kwargs.get("message"))


# =============================================================================
# Lesen
# =============================================================================
async def query(session: AsyncSession, *, page: int = 1, page_size: int = 100,
                category: str = "", level: str = "", actor: str = "",
                search: str = "", only_failures: bool = False,
                since_hours: Optional[int] = None) -> dict:
    """Blaettert durch das Tagebuch."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 100), 500))

    condition = []
    if category:
        condition.append(SystemEvent.category == category.strip().lower())
    if level:
        wanted = level.strip().lower()
        if wanted in LEVELS:
            # "ab dieser Stufe aufwaerts" - wie beim Schweregrad der Logs.
            threshold = LEVELS.index(wanted)
            condition.append(SystemEvent.level.in_(LEVELS[threshold:]))
    if actor:
        condition.append(SystemEvent.actor.ilike(f"%{actor.strip()}%"))
    if search:
        needle = f"%{search.strip()}%"
        condition.append(SystemEvent.message.ilike(needle) | SystemEvent.event.ilike(needle)
                         | SystemEvent.target.ilike(needle))
    if only_failures:
        condition.append(SystemEvent.ok.is_(False))
    if since_hours:
        condition.append(SystemEvent.at >= datetime.utcnow() - timedelta(hours=int(since_hours)))

    base = select(SystemEvent)
    counter = select(func.count(SystemEvent.id))
    for clause in condition:
        base = base.where(clause)
        counter = counter.where(clause)

    total = (await session.execute(counter)).scalar() or 0
    rows = (await session.execute(
        base.order_by(desc(SystemEvent.at), desc(SystemEvent.id))
            .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    return {
        "items": [as_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "categories": [{"key": key, "label": label} for key, label in CATEGORIES.items()],
        "levels": list(LEVELS),
    }


def as_dict(row: SystemEvent) -> dict:
    return {
        "id": row.id,
        "at": row.at,
        "category": row.category,
        "category_label": CATEGORIES.get(row.category, "Sonstiges"),
        "level": row.level,
        "event": row.event,
        "message": row.message,
        "actor": row.actor,
        "source_ip": row.source_ip,
        "target": row.target,
        "ok": row.ok,
        "duration_ms": row.duration_ms,
        "detail": row.detail or {},
    }


async def summary(session: AsyncSession, hours: int = 24) -> dict:
    """Kurzer Ueberblick fuer das Dashboard: was war in den letzten Stunden los?"""
    since = datetime.utcnow() - timedelta(hours=max(1, int(hours)))
    rows = (await session.execute(
        select(SystemEvent.category, SystemEvent.level, func.count(SystemEvent.id))
        .where(SystemEvent.at >= since)
        .group_by(SystemEvent.category, SystemEvent.level)
    )).all()

    by_category: Dict[str, int] = {}
    problems = 0
    for category, level, count in rows:
        by_category[category] = by_category.get(category, 0) + count
        if level in ("error", "critical"):
            problems += count

    last_failure = (await session.execute(
        select(SystemEvent).where(SystemEvent.ok.is_(False))
        .order_by(desc(SystemEvent.at)).limit(1)
    )).scalar_one_or_none()

    return {
        "hours": hours,
        "total": sum(by_category.values()),
        "by_category": by_category,
        "problems": problems,
        "last_failure": as_dict(last_failure) if last_failure else None,
    }


# =============================================================================
# Aufraeumen
# =============================================================================
async def prune(session: AsyncSession, days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Haelt das Tagebuch in Grenzen - nach Alter und nach Zeilenzahl.

    Fehler und kritische Eintraege bleiben laenger: genau die will man haben,
    wenn jemand ein halbes Jahr spaeter fragt, was damals war.
    """
    removed = 0
    cutoff = datetime.utcnow() - timedelta(days=max(7, int(days)))
    result = await session.execute(
        delete(SystemEvent).where(
            SystemEvent.at < cutoff,
            SystemEvent.level.notin_(("error", "critical")),
        )
    )
    removed += result.rowcount or 0

    total = (await session.execute(select(func.count(SystemEvent.id)))).scalar() or 0
    if total > MAX_ROWS:
        excess = total - MAX_ROWS
        oldest = (
            select(SystemEvent.id).order_by(SystemEvent.at.asc()).limit(excess).scalar_subquery()
        )
        result = await session.execute(delete(SystemEvent).where(SystemEvent.id.in_(oldest)))
        removed += result.rowcount or 0

    await session.commit()
    return removed
