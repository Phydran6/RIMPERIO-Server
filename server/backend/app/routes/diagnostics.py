# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.14.12.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer den Systemcheck (Selbstdiagnose)
# ==============================================================================
"""
Der Systemcheck laeuft absichtlich nur auf Knopfdruck: er fragt Dienste ab,
misst Antwortzeiten und rechnet ueber die Datenbank. Das gehoert nicht in eine
Seite, die sich alle paar Sekunden selbst aktualisiert.

Der letzte Bericht wird im Speicher gehalten, damit die Seite beim Oeffnen
sofort etwas anzeigen kann, ohne erneut zu messen.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import diagnostics
from ..auth import get_current_admin

logger = logging.getLogger("rimperio.diagnostics")

router = APIRouter(prefix="/api/diagnostics", tags=["Diagnose"])

_last_report: Optional[dict] = None
_run_lock = asyncio.Lock()


@router.get("/last")
async def last_report(_=Depends(get_current_admin)):
    """Der zuletzt erstellte Bericht (oder ein Hinweis, dass noch keiner lief)."""
    if _last_report is None:
        return {"available": False,
                "message": "Es wurde noch kein Systemcheck ausgefuehrt."}
    return {"available": True, "report": _last_report}


@router.post("/run")
async def run_diagnostics(_=Depends(get_current_admin)):
    """Prueft das System einmal komplett durch."""
    global _last_report

    if _run_lock.locked():
        raise HTTPException(status_code=409,
                            detail="Es laeuft bereits ein Systemcheck. Bitte kurz warten.")

    async with _run_lock:
        try:
            report = await diagnostics.run_all()
        except Exception as exc:                                # defensiv
            logger.exception("Systemcheck fehlgeschlagen")
            raise HTTPException(status_code=500,
                                detail=f"Systemcheck fehlgeschlagen: {exc}")
        _last_report = report
        return {"available": True, "report": report}
