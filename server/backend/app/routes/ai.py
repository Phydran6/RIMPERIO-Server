# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer die KI-Auswertung
# ==============================================================================
"""
Einrichten darf nur ein Administrator - dort steht der API-Schluessel drin.

*Benutzen* darf, wer freigeschaltet ist: der Schalter `enabled_for_users` gibt
die Auswertung fuer alle angemeldeten Benutzer frei. Steht er aus, koennen nur
Administratoren Logs an die KI geben. Voreinstellung ist "aus" - denn jede
Auswertung schickt Logdaten an einen Dritten.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ai
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import Agent, Log

logger = logging.getLogger("rimperio.routes.ai")

router = APIRouter(prefix="/api/ai", tags=["KI-Auswertung"])


class ConfigRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_header: Optional[str] = None
    system_prompt: Optional[str] = None
    max_logs: Optional[int] = Field(default=None, ge=1, le=ai.MAX_LOGS)
    include_raw: Optional[bool] = None
    enabled_for_users: Optional[bool] = None
    api_key: Optional[str] = Field(default=None,
                                   description="Leer lassen = bestehenden Schlüssel behalten")
    clear_api_key: bool = False


class AskRequest(BaseModel):
    """Welche Logs, und was soll die KI dazu sagen?"""
    question: str = Field(default="", max_length=2000)
    log_ids: Optional[List[int]] = Field(default=None,
                                         description="Bestimmte Zeilen; sonst greifen die Filter")
    hostname: Optional[str] = None
    level: Optional[str] = None
    min_severity: Optional[str] = None
    search: Optional[str] = None
    hours: int = Field(default=24, ge=1, le=720)
    limit: int = Field(default=ai.DEFAULT_LOGS, ge=1, le=ai.MAX_LOGS)


async def _collect_logs(db: AsyncSession, data: AskRequest, limit: int) -> List[Log]:
    """Sucht die Logzeilen zusammen, die ausgewertet werden sollen."""
    if data.log_ids:
        rows = (await db.execute(
            select(Log).where(Log.id.in_(data.log_ids[:limit])).order_by(desc(Log.timestamp))
        )).scalars().all()
        return list(rows)

    query = select(Log).where(Log.timestamp >= datetime.utcnow() - timedelta(hours=data.hours))
    if data.hostname:
        query = query.where(func.lower(Log.hostname) == data.hostname.strip().lower())
    if data.level:
        query = query.where(func.lower(Log.level) == data.level.strip().lower())
    if data.min_severity:
        from .mobile import SEVERITY_RANK, _levels_up_to
        rank = SEVERITY_RANK.get(data.min_severity.strip().lower())
        if rank is None:
            raise HTTPException(status_code=400,
                                detail=f"Unbekannter Schweregrad '{data.min_severity}'.")
        query = query.where(func.lower(Log.level).in_(_levels_up_to(rank)))
    if data.search:
        query = query.where(Log.message.ilike(f"%{data.search.strip()}%"))

    rows = (await db.execute(query.order_by(desc(Log.timestamp)).limit(limit))).scalars().all()
    return list(rows)


async def _require_usage_permission(db: AsyncSession, user) -> dict:
    """Darf dieser Benutzer die KI überhaupt fragen?"""
    config = await ai.load_config(db)
    if config["provider"] == "off":
        raise HTTPException(
            status_code=409,
            detail=("Die KI-Auswertung ist nicht eingerichtet. "
                    "Ein Administrator richtet sie unter System → KI-Auswertung ein."))
    if user.role != "admin" and not config.get("enabled_for_users"):
        raise HTTPException(
            status_code=403,
            detail="Die KI-Auswertung ist nur für Administratoren freigegeben.")
    return config


# =============================================================================
# Einrichtung (nur Admins)
# =============================================================================
@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Aktuelle Einstellung — ohne API-Schlüssel."""
    config = await ai.load_config(db)
    return {
        "config": ai.public_config(config),
        "providers": ai.catalog(),
        "limits": {"max_logs": ai.MAX_LOGS, "default_logs": ai.DEFAULT_LOGS},
        "default_system_prompt": ai.DEFAULT_SYSTEM_PROMPT,
    }


@router.put("/config")
async def put_config(data: ConfigRequest, db: AsyncSession = Depends(get_db),
                     admin=Depends(get_current_admin)):
    """Speichert die Einstellung."""
    try:
        config = await ai.save_config(db, data.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.warning("KI-Anbindung geändert von %s", admin.username)
    return {"config": config, "providers": ai.catalog()}


@router.post("/test")
async def test(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Probelauf mit einer erfundenen Logzeile — es gehen keine echten Daten raus."""
    return await ai.test_connection(await ai.load_config(db))


@router.get("/models")
async def models(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Welche Modelle bietet die eingerichtete Gegenstelle an?

    Bislang nur für Open WebUI: dort hängt hinter der Adresse das, was der
    Betreiber selbst installiert hat — den Namen kann niemand erraten. Bei
    Anthropic und OpenAI steht der Modellname in deren Dokumentation und
    ändert sich selten; da hilft eine Abfrage wenig.

    Antwortet mit einer leeren Liste, wenn die Gegenstelle nicht erreichbar ist
    oder keine Auskunft gibt. Das ist kein Fehler — es heißt nur, dass der
    Modellname von Hand einzutragen ist.
    """
    config = await ai.load_config(db)
    if config.get("provider") != "openwebui":
        return {"models": [], "provider": config.get("provider"),
                "note": "Eine Modellliste gibt es nur für Open WebUI."}
    found = await ai.list_openwebui_models(config)
    return {
        "models": found,
        "provider": "openwebui",
        "note": ("" if found else
                 "Open WebUI hat keine Modelle gemeldet. Stimmen Adresse und "
                 "API-Schlüssel? Der Schlüssel steht dort unter Profil → "
                 "Einstellungen → Konto."),
    }


# =============================================================================
# Auswerten
# =============================================================================
@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Kann *dieser* Benutzer die KI-Auswertung nutzen? (Für App und UI.)"""
    config = await ai.load_config(db)
    available = config["provider"] != "off"
    allowed = available and (user.role == "admin" or config.get("enabled_for_users"))
    return {
        "available": available,
        "allowed": allowed,
        "provider": config["provider"] if available else "off",
        "provider_label": ai.PROVIDERS.get(config["provider"], {}).get("label", ""),
        "max_logs": config.get("max_logs", ai.DEFAULT_LOGS),
    }


@router.post("/preview")
async def preview(data: AskRequest, db: AsyncSession = Depends(get_db),
                  user=Depends(get_current_user)):
    """Was würde gesendet? Zum Nachsehen, bevor Daten den Server verlassen."""
    config = await _require_usage_permission(db, user)
    limit = min(data.limit, int(config.get("max_logs") or ai.DEFAULT_LOGS))
    logs = await _collect_logs(db, data, limit)
    if not logs:
        raise HTTPException(status_code=404, detail="Zu dieser Auswahl gibt es keine Logzeilen.")
    return ai.preview_payload(logs, data.question, config)


@router.post("/analyze")
async def analyze(data: AskRequest, db: AsyncSession = Depends(get_db),
                  user=Depends(get_current_user)):
    """Gibt die ausgewählten Logzeilen an die eingestellte KI und liefert die Antwort."""
    config = await _require_usage_permission(db, user)
    limit = min(data.limit, int(config.get("max_logs") or ai.DEFAULT_LOGS))

    logs = await _collect_logs(db, data, limit)
    if not logs:
        raise HTTPException(status_code=404, detail="Zu dieser Auswahl gibt es keine Logzeilen.")

    try:
        result = await ai.ask(config, logs, data.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Der Anbieter hat geantwortet, aber ablehnend - 502 sagt: der Fehler
        # liegt nicht bei dieser Anfrage, sondern hinter dem Server.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info("KI-Auswertung von %s (%s Zeilen)", user.username, result.get("logs_sent"))
    return result
