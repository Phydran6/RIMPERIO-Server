# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer den Mailversand (Postfix, web-konfiguriert)
# ==============================================================================
"""
Einstellungen -> E-Mail. Nur fuer Administratoren - hier stehen Zugangsdaten drin.

Die gesamte Postfix-Konfiguration entsteht hier: was eingetragen wird, schreibt
das Backend nach `data/postfix/settings.env`, und der Container liest das beim
Start. `GET /preview` zeigt vorher, was in der Datei landen wuerde - damit man
nachvollziehen kann, was die Oberflaeche eigentlich tut.
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import alerts, mail, mail_m365, mail_providers
from ..auth import get_current_admin
from ..database import get_db

logger = logging.getLogger("rimperio.routes.mail")

router = APIRouter(prefix="/api/mail", tags=["Mail"])


class ConfigRequest(BaseModel):
    mode: Optional[str] = Field(default=None, description="off | container | relay | m365")
    provider: Optional[str] = Field(default=None, description="Vorlage aus /config → providers")
    from_address: Optional[str] = None
    from_name: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = Field(default=None,
                                         description="Leer = bestehendes Passwort behalten")
    clear_smtp_password: bool = False
    encryption: Optional[str] = Field(default=None, description="none | starttls | tls")
    verify_certificate: Optional[bool] = None
    myhostname: Optional[str] = None
    relay_host: Optional[str] = None
    relay_port: Optional[int] = Field(default=None, ge=1, le=65535)
    relay_user: Optional[str] = None
    relay_password: Optional[str] = None
    clear_relay_password: bool = False
    m365_tenant_id: Optional[str] = None
    m365_client_id: Optional[str] = None
    m365_client_secret: Optional[str] = Field(default=None,
                                              description="Leer = bestehendes Geheimnis behalten")
    default_recipients: Optional[List[str]] = None
    notify_on: Optional[Dict[str, bool]] = None


class TestRequest(BaseModel):
    recipient: str = Field(default="", description="Leer = die hinterlegten Empfänger")


class M365StartRequest(BaseModel):
    from_address: str = Field(default="", description="Leer = Postfach des anmeldenden Admins")
    from_name: str = Field(default="RIMPERIO")


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Aktuelle Einstellung — ohne Passwörter."""
    config = await mail.load_config(db)
    return {
        "config": mail.public_config(config),
        "providers": mail_providers.public_list(),
        "m365_restrict_command": (mail_m365.restrict_command(config)
                                  if config["mode"] == "m365" else ""),
        "modes": [{"id": key, **value} for key, value in mail.MODES.items()],
        "encryptions": [{"id": key, "label": label}
                        for key, label in mail.ENCRYPTIONS.items()],
        "events": [
            {"id": "update_available", "label": "Ein Update steht bereit"},
            {"id": "backup_failed", "label": "Eine Sicherung ist fehlgeschlagen"},
            {"id": "agent_offline", "label": "Ein Gerät meldet sich nicht mehr"},
            {"id": "disk_pressure", "label": "Der Plattenplatz wird knapp"},
        ],
    }


@router.put("/config")
async def put_config(data: ConfigRequest, db: AsyncSession = Depends(get_db),
                     admin=Depends(get_current_admin)):
    """Speichert die Einstellung und schreibt die Postfix-Datei neu."""
    try:
        result = await mail.save_config(db, data.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.warning("Mail-Einstellung geändert von %s", admin.username)
    await _refresh_alerts(db)
    return {"config": result}


async def _refresh_alerts(db: AsyncSession) -> None:
    """Die Benachrichtigungen lesen die Mail-Einstellung aus einem Zwischenstand,
    den sonst erst der Wachhund (minuetlich) erneuert."""
    try:
        await alerts.refresh(db)
    except Exception:                                               # defensiv
        logger.debug("alerts.refresh nach Mail-Änderung fehlgeschlagen", exc_info=True)


@router.post("/verify")
async def verify(data: ConfigRequest, db: AsyncSession = Depends(get_db),
                 _=Depends(get_current_admin)):
    """Prüft Verbindung und Anmeldung mit den eingegebenen Werten — ohne zu
    speichern und ohne eine Mail zu verschicken."""
    try:
        config = mail.merge_config(await mail.load_config(db),
                                   data.model_dump(exclude_none=True))
        return await mail.verify(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# =============================================================================
# Microsoft 365: Einrichtung per Admin-Anmeldung
# =============================================================================
@router.post("/m365/start")
async def m365_start(data: M365StartRequest, admin=Depends(get_current_admin)):
    """Holt einen Gerätecode bei Microsoft. Danach /m365/status abfragen."""
    if data.from_address and "@" not in data.from_address:
        raise HTTPException(status_code=400, detail="Die Absenderadresse ist ungültig.")
    try:
        return await mail_m365.start(data.from_address, data.from_name, admin.username)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/m365/status/{flow}")
async def m365_status(flow: str, _=Depends(get_current_admin)):
    """Stand der Einrichtung. Fragt selbst bei Microsoft nach, ob die Anmeldung
    durch ist - die Oberfläche ruft das einfach alle paar Sekunden auf."""
    try:
        return await mail_m365.status(flow)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.delete("/m365/{flow}")
async def m365_cancel(flow: str, _=Depends(get_current_admin)):
    mail_m365.cancel(flow)
    return {"cancelled": True}


@router.get("/preview")
async def preview(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Was steht in der Postfix-Datei? (Passwörter sind darin ersetzt.)"""
    config = await mail.load_config(db)
    rendered = mail.render_postfix_env(config)
    # Ein Relay-Passwort steht in der echten Datei drin - in der Vorschau nicht.
    if config.get("relay_password"):
        rendered = rendered.replace(config["relay_password"], "********")
    return {
        "path": mail.POSTFIX_ENV_RELATIVE,
        "content": rendered,
        "applies_to": "container",
        "note": ("Diese Datei schreibt RIMPERIO bei jedem Speichern neu. Änderungen "
                 "von Hand gehen dabei verloren."),
    }


@router.post("/test")
async def test(data: TestRequest, db: AsyncSession = Depends(get_db),
               admin=Depends(get_current_admin)):
    """Verschickt eine Probemail."""
    config = await mail.load_config(db)
    try:
        result = await mail.send_test(config, data.recipient)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Der Mailserver hat geantwortet, aber ablehnend - der Fehler liegt
        # hinter diesem Server, nicht in der Anfrage.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info("Probemail von %s verschickt an %s", admin.username, result["recipients"])
    return result
