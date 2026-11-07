# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer das Systemtagebuch
# ==============================================================================
"""
Das Systemtagebuch lesen.

Warum es das gibt, steht ausfuehrlich in ``app/journal.py``. Kurz: auf diesem
Server soll nichts passieren, das hinterher niemand mehr nachvollziehen kann.

Lesen duerfen Administratoren. Nicht, weil die Eintraege geheim waeren -
sondern weil dort Benutzernamen, Absender-Adressen und Angaben zu Eingriffen
stehen, die einem Angreifer die halbe Arbeit abnehmen wuerden.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal
from ..auth import admin_from_token, get_current_admin
from ..database import get_db
from ..events import bus
from ..limiter import client_ip as real_client_ip

logger = logging.getLogger("rimperio.routes.journal")

router = APIRouter(prefix="/api/journal", tags=["Systemtagebuch"])


@router.get("")
async def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    category: str = Query("", description="auth, update, retention, container, …"),
    level: str = Query("", description="Ab dieser Stufe aufwärts"),
    actor: str = Query("", description="Wer — Benutzername oder Schlüsselname"),
    search: str = Query("", description="Freitext in Meldung, Vorgang und Ziel"),
    only_failures: bool = Query(False, description="Nur das, was schiefging"),
    since_hours: Optional[int] = Query(None, ge=1, le=24 * 365),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Blättert durch das Tagebuch."""
    return await journal.query(
        db, page=page, page_size=page_size, category=category, level=level,
        actor=actor, search=search, only_failures=only_failures,
        since_hours=since_hours)


@router.get("/summary")
async def event_summary(hours: int = Query(24, ge=1, le=24 * 30),
                        db: AsyncSession = Depends(get_db),
                        _=Depends(get_current_admin)):
    """Was war in den letzten Stunden los? Für die Kachel auf dem Dashboard."""
    return await journal.summary(db, hours=hours)


@router.get("/stream")
async def stream_events(token: str = Query(..., description="Access-Token eines Admins")):
    """Neue Einträge in dem Moment, in dem sie entstehen (Server-Sent Events).

    Damit läuft das Tagebuch während eines Updates oder eines Aufräumlaufs live
    mit — man sieht zu, statt hinterher nachzulesen.

    Der Token kommt als Abfrageparameter, weil `EventSource` im Browser keine
    eigenen Kopfzeilen mitschicken kann. Geprüft wird er genauso streng.
    """
    if not await admin_from_token(token, purpose="journal"):
        raise HTTPException(status_code=401,
                            detail="Nicht angemeldet oder keine Administratorrechte.")

    return StreamingResponse(
        bus.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/prune")
async def prune_events(days: int = Query(journal.DEFAULT_RETENTION_DAYS, ge=7, le=3650),
                       request: Request = None,
                       db: AsyncSession = Depends(get_db),
                       admin=Depends(get_current_admin)):
    """Räumt alte Einträge weg. Fehler und Kritisches bleiben stehen."""
    removed = await journal.prune(db, days=days)
    await journal.record(
        db, category="system", event="journal.pruned",
        message=f"{removed} Tagebuch-Einträge älter als {days} Tage entfernt.",
        actor=admin.username,
        source_ip=real_client_ip(request) if request else "")
    return {"removed": removed, "days": days,
            "message": (f"{removed} Einträge entfernt."
                        if removed else "Es gab nichts wegzuräumen.")}
