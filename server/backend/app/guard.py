# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Pflicht-Rueckfrage "Sicherung vorher?" vor Systemeingriffen
# ==============================================================================
"""
Ein Systemeingriff (Update, Rueckfall, Zurueckspielen, Container-Stack aendern,
Neustart) darf nicht einfach losrennen. Vorher steht immer dieselbe Frage:

    "Vorher eine Sicherung anlegen — ja oder nein?"

Damit das nicht nur eine Zeile in der Oberflaeche ist, die man weglassen kann,
haengt es hier am Endpunkt: ohne ausgefuellte Entscheidung antwortet der Server
mit 400. Ein Skript, das den Dialog umgehen will, muss die Entscheidung also
bewusst mitschicken - und zwar auch das "nein".
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from . import backup as backup_module

logger = logging.getLogger("rimperio.guard")


class BackupDecision(BaseModel):
    """Die Antwort aus dem Sicherungs-Dialog.

    `create=None` bedeutet: der Dialog wurde gar nicht durchlaufen. Das ist der
    Fall, den wir abfangen wollen.
    """
    create: Optional[bool] = Field(
        default=None,
        description="true = vorher sichern, false = bewusst ohne Sicherung weiter",
    )
    scopes: Optional[List[str]] = Field(
        default=None,
        description="Welche Bereiche gesichert werden sollen (leer = Standardumfang)",
    )
    passphrase: str = Field(default="", description="Optionales Passwort für die Sicherung")
    note: str = Field(default="", description="Freier Vermerk zur Sicherung")


async def ensure_backup_decision(
    session: AsyncSession,
    decision: Optional[BackupDecision],
    operation: str,
    created_by: str = "",
) -> dict:
    """Setzt die Entscheidung um. Gibt einen Bericht fuer die Antwort zurueck.

    Wurde der Dialog nicht durchlaufen (`create` fehlt), bricht der Aufruf mit
    HTTP 400 ab und nennt beide gueltigen Antworten.
    """
    if decision is None or decision.create is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Vor '{operation}' muss die Sicherungsfrage beantwortet werden. "
                "Erwartet wird 'backup': {'create': true, ...} für eine Sicherung "
                "oder 'backup': {'create': false} für den bewussten Verzicht."
            ),
        )

    if not decision.create:
        logger.warning("'%s' läuft OHNE vorherige Sicherung (bewusst gewählt von %s)",
                       operation, created_by or "unbekannt")
        return {"created": False, "skipped_by_choice": True,
                "message": "Ohne vorherige Sicherung fortgefahren — so gewählt."}

    try:
        manifest = await backup_module.create_backup(
            session,
            scopes=decision.scopes or backup_module.DEFAULT_SCOPES,
            passphrase=decision.passphrase,
            note=decision.note or f"Automatisch vor: {operation}",
            trigger=f"pre:{operation}",
            created_by=created_by,
        )
    except Exception as exc:
        # Scheitert die Sicherung, laeuft der Eingriff NICHT an. Genau dafuer
        # ist die Sicherung ja da.
        logger.error("Sicherung vor '%s' fehlgeschlagen: %s", operation, exc)
        raise HTTPException(
            status_code=500,
            detail=(f"Die Sicherung vor '{operation}' ist fehlgeschlagen: {exc}. "
                    f"Der Eingriff wurde deshalb nicht gestartet."),
        ) from exc

    backup_module.prune()
    return {
        "created": True,
        "name": manifest["name"],
        "scopes": manifest["scopes"],
        "encrypted": manifest["encrypted"],
        "size_bytes": manifest.get("size_bytes"),
        "message": f"Sicherung '{manifest['name']}' wurde vorher angelegt.",
    }
