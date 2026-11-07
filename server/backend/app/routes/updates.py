# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Patchmanagement: Stand pruefen, Release waehlen,
#               Update, Rueckfall, Sofortmeldung bei neuem Stand
# ==============================================================================
"""
Endpunkte fuer den Bereich "Updates".

Alles hier ist Administratoren vorbehalten - mit einer Ausnahme: der
GitHub-Webhook. Der kommt von aussen und weist sich stattdessen mit einer
HMAC-Signatur aus (Secret aus der .env). Ohne gesetztes Secret nimmt der Server
gar keine Webhook-Meldung an.

Update und Rueckfall greifen tief ins System ein. Deshalb zwei Sperren
hintereinander:

1. ein Bestaetigungswort im Rumpf ('UPDATE' / 'ROLLBACK') - gegen den
   versehentlichen Klick;
2. die beantwortete Sicherungsfrage (`backup`) - gegen den Datenverlust.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, updater
from ..auth import admin_from_token, get_current_admin
from ..database import get_db
from ..events import bus
from ..guard import BackupDecision, ensure_backup_decision
from ..limiter import limiter, client_ip as real_client_ip

logger = logging.getLogger("rimperio.updates")

router = APIRouter(prefix="/api/updates", tags=["Updates"])


class UpdateRequest(BaseModel):
    """Bestaetigung, Sicherungsentscheidung und - optional - ein Ziel-Release."""
    confirm: str = Field(..., description="Muss 'UPDATE' lauten")
    # Der alte Schalter bleibt: er steuert den zusaetzlichen pg_dump, den das
    # Wartungsskript auf dem Host anlegt (unabhaengig von der ZIP-Sicherung).
    database_backup: bool = True
    ref: str = Field(default="", pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._/\-]{0,199}$", description="Release/Tag/Commit; leer = eingestellter Kanal")
    allow_downgrade: bool = Field(
        default=False,
        description="Auch einspielen, wenn das Ziel aelter ist als der installierte Stand")
    backup: Optional[BackupDecision] = None


class RollbackRequest(BaseModel):
    """Bestaetigung, Sicherungsentscheidung und - optional - die Sicherung."""
    confirm: str = Field(..., description="Muss 'ROLLBACK' lauten")
    backup_name: str = Field(default="", alias="backup_dir",
                             description="Name der Sicherung auf dem Host; leer = neueste")
    backup: Optional[BackupDecision] = None

    model_config = {"populate_by_name": True}


class ChannelRequest(BaseModel):
    channel: str = Field(..., description="stable | edge | pinned")
    ref: str = Field(default="", pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._/\-]{0,199}$", description="Release/Tag bei 'pinned'")
    auto_offer: bool = Field(default=True,
                             description="Neuen Stand von selbst anbieten")


# =============================================================================
# Stand pruefen
# =============================================================================
@router.get("/status")
async def get_status(force: bool = Query(False, description="GitHub erneut abfragen"),
                     _=Depends(get_current_admin)):
    """Installierter Stand, Stand auf GitHub, laufender Vorgang und Sicherungen."""
    return await updater.update_status(force=force)


@router.post("/check")
async def check_now(_=Depends(get_current_admin)):
    """Fragt GitHub sofort erneut ab (umgeht den Zwischenspeicher)."""
    status = await updater.update_status(force=True)
    # Kam bei dieser Gelegenheit etwas Neues heraus, sollen es auch die anderen
    # offenen Fenster erfahren.
    await updater.announce_if_new(reason="manual-check", force_check=False)
    return status


@router.get("/log")
async def get_log(lines: int = Query(200, ge=10, le=2000), _=Depends(get_current_admin)):
    """Die letzten Zeilen des Wartungsprotokolls vom Host."""
    return {"lines": await updater.read_run_log(lines=lines)}


@router.get("/log/stream")
async def stream_log(token: str = Query(..., description="Access-Token eines Admins")):
    """Die Ausgabe des laufenden Updates — live, Zeile für Zeile.

    Ein Fortschrittsbalken sagt „43 %". Er sagt nicht, woran es gerade hängt,
    und wenn etwas schiefgeht, sagt er gar nichts. Hier läuft stattdessen mit,
    was das Wartungsskript auf dem Server tatsächlich ausgibt — so wie in einer
    Shell danebenzustehen.

    Der Token kommt als Abfrageparameter, weil `EventSource` im Browser keine
    eigenen Kopfzeilen mitschicken kann. Geprüft wird er genauso streng.
    """
    if not await admin_from_token(token, purpose="update-log"):
        raise HTTPException(status_code=401,
                            detail="Nicht angemeldet oder keine Administratorrechte.")

    return StreamingResponse(
        updater.stream_run_log(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Release-Auswahl
# =============================================================================
@router.get("/releases")
async def releases(force: bool = Query(False), _=Depends(get_current_admin)):
    """Welche Releases gibt es — und welches fährt dieser Server?"""
    data = await updater.list_releases(force=force)
    data["current_channel"] = await updater.load_channel()
    data["target_ref"] = await updater.resolve_target_ref(data["current_channel"])
    return data


@router.put("/channel")
async def set_channel(data: ChannelRequest, _=Depends(get_current_admin)):
    """Legt fest, welchen Stand dieser Server fahren soll."""
    try:
        config = await updater.save_channel(data.channel, data.ref, data.auto_offer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"channel": config, "status": await updater.update_status(force=True)}


# =============================================================================
# Sofortmeldung: Ereignisstrom und GitHub-Webhook
# =============================================================================
@router.get("/stream")
async def stream(token: str = Query(..., description="Access-Token eines Admins")):
    """Offener Kanal zur Oberfläche (Server-Sent Events).

    Darüber kommt der Hinweis auf einen neuen Stand in dem Moment an, in dem der
    Server ihn kennt — ohne dass die Seite dafür nachfragen muss.

    Der Token kommt als Abfrageparameter statt im Kopf: `EventSource` im Browser
    kann keine eigenen Kopfzeilen mitschicken. Geprüft wird er genauso streng.
    """
    if not await admin_from_token(token, purpose="updates"):
        raise HTTPException(status_code=401,
                            detail="Nicht angemeldet oder keine Administratorrechte.")

    return StreamingResponse(
        bus.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Caddy puffert nicht, andere Proxys schon - das schaltet es ab.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/webhook")
@limiter.limit("60/minute")
async def github_webhook(request: Request):
    """Nimmt die Push-Meldung von GitHub entgegen.

    Öffentlich erreichbar, aber nicht offen: ohne gültige HMAC-Signatur (und
    ohne gesetztes RIMPERIO_WEBHOOK_SECRET) wird nichts angenommen.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not updater.verify_webhook_signature(body, signature):
        # Bewusst dieselbe Antwort für "kein Secret gesetzt" und "falsch
        # signiert": von außen soll nicht erkennbar sein, welches von beidem.
        raise HTTPException(status_code=401, detail="Signatur fehlt oder passt nicht.")

    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"accepted": True, "pong": True}
    if event not in ("push", "release", "create"):
        return {"accepted": False, "reason": f"Ereignis '{event}' wird nicht ausgewertet."}

    logger.warning("GitHub-Webhook empfangen: %s", event)
    result = await updater.announce_if_new(reason=f"webhook:{event}")
    return {"accepted": True, **result}


# =============================================================================
# Update und Rueckfall
# =============================================================================
@router.post("/apply")
async def apply_update(data: UpdateRequest, request: Request,
                       db: AsyncSession = Depends(get_db),
                       admin=Depends(get_current_admin)):
    """Spielt den gewählten Stand ein.

    Der eigentliche Lauf passiert auf dem Host — dieser Aufruf kehrt sofort
    zurück. Während des Updates ist die Oberfläche kurz nicht erreichbar.
    """
    if data.confirm != "UPDATE":
        raise HTTPException(status_code=400,
                            detail="Bestätigung fehlt: 'confirm' muss 'UPDATE' lauten.")

    status = await updater.update_status()
    if not status["local"].get("host_access"):
        raise HTTPException(
            status_code=409,
            detail=("Kein Zugriff auf den Host. Das Update über die Oberfläche braucht ein "
                    "Backend mit privileged/pid:host (Standard-docker-compose.yml). "
                    "Alternativ per Kommandozeile: " + status["oneliner"]),
        )

    # Zeigt der Kanal auf einen älteren Stand als den installierten, wäre das
    # Einspielen ein Rückschritt — meist, weil für den aktuellen Stand noch kein
    # Release angelegt wurde. Das passiert nicht aus Versehen.
    if status.get("is_downgrade") and not data.ref.strip() and not data.allow_downgrade:
        raise HTTPException(
            status_code=409,
            detail=(f"{status['reason']} Einspielen würde den Server zurückfahren. "
                    f"Entweder ein Release für den aktuellen Stand anlegen, den Kanal "
                    f"auf '{updater.REPO_BRANCH}' stellen — oder, wenn der Rückschritt "
                    f"gewollt ist, 'allow_downgrade' setzen."),
        )

    # Erst sichern, dann anfassen. Scheitert die Sicherung, läuft nichts an.
    pre = await ensure_backup_decision(db, data.backup, "Update", created_by=admin.username)

    target = data.ref.strip() or status.get("target_ref", "")
    result = await updater.start_run("apply", database_backup=data.database_backup, ref=target)
    if not result.get("started"):
        raise HTTPException(status_code=409, detail=result.get("error", "Start fehlgeschlagen"))

    logger.warning("Update gestartet von %s (Ziel: %s, Datenbank-Abzug: %s, Sicherung: %s)",
                   admin.username, target or "Zweig", data.database_backup, pre.get("created"))
    await journal.record(
        db, category="update", event="update.started", level="warning",
        message=(f"Update gestartet (Ziel: {target or updater.REPO_BRANCH}). "
                 f"Sicherung vorher: {'ja' if pre.get('created') else 'nein'}."),
        actor=admin.username, source_ip=real_client_ip(request),
        target=target or updater.REPO_BRANCH,
        detail={"ref": target, "database_backup": data.database_backup,
                "from_version": (status.get("local") or {}).get("version"),
                "to_version": (status.get("remote") or {}).get("version"),
                "unit": result.get("unit")})
    result["pre_backup"] = pre
    result["ref"] = target
    return result


@router.post("/rollback")
async def rollback(data: RollbackRequest, request: Request,
                   db: AsyncSession = Depends(get_db),
                   admin=Depends(get_current_admin)):
    """Fährt auf eine vorherige Sicherung des Wartungsskripts zurück."""
    if data.confirm != "ROLLBACK":
        raise HTTPException(status_code=400,
                            detail="Bestätigung fehlt: 'confirm' muss 'ROLLBACK' lauten.")

    backups = await updater.list_backups()
    if not backups:
        raise HTTPException(status_code=404,
                            detail="Es gibt keine Sicherung, auf die zurückgefallen werden könnte.")
    if data.backup_name and data.backup_name not in [b["name"] for b in backups]:
        raise HTTPException(status_code=404, detail=f"Sicherung '{data.backup_name}' gibt es nicht.")

    pre = await ensure_backup_decision(db, data.backup, "Rückfall", created_by=admin.username)

    result = await updater.start_run("rollback", backup_name=data.backup_name)
    if not result.get("started"):
        raise HTTPException(status_code=409, detail=result.get("error", "Start fehlgeschlagen"))

    logger.warning("Rückfall gestartet von %s (Sicherung: %s)",
                   admin.username, data.backup_name or "neueste")
    await journal.record(
        db, category="update", event="rollback.started", level="critical",
        message=(f"Rückfall gestartet auf Sicherung "
                 f"'{data.backup_name or 'neueste'}'."),
        actor=admin.username, source_ip=real_client_ip(request),
        target=data.backup_name or "neueste",
        detail={"unit": result.get("unit")})
    result["pre_backup"] = pre
    return result
