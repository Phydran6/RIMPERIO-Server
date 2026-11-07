# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer Single Sign-on (Microsoft 365 / OIDC)
# ==============================================================================
"""
Die Endpunkte fuer die Anmeldung ueber ein Firmenkonto.

Drei davon sind oeffentlich - sie muessen es sein, denn wer sich gerade erst
anmelden will, ist noch nicht angemeldet:

* ``GET  /api/auth/sso/status``   - gibt es den Knopf, und wie heisst er?
  Antwortet bewusst *nur* mit dem Noetigsten. Weder Mandant noch Anwendungs-ID
  stehen darin: das braucht der Anmeldeschirm nicht.
* ``GET  /api/auth/sso/start``    - beginnt den Vorgang und schickt zum Anbieter.
* ``GET  /api/auth/sso/callback`` - nimmt die Rueckkehr entgegen.

Die Verwaltung (``/api/sso/config``) ist Administratoren vorbehalten.

**Wie der Browser am Ende zu seiner Sitzung kommt.** Der Anbieter schickt den
Browser mit einem Code zurueck - per Umleitung, nicht per Skript. An dieser
Stelle laesst sich kein JSON zurueckgeben, das die Oberflaeche auswerten
koennte. Deshalb legt der Rueckweg einen kurzlebigen Einmal-Schluessel an
(dieselbe Machart wie beim Anmelden der App per QR-Code, ``app_login_tokens``)
und leitet auf ``/login?sso=<Schluessel>`` weiter. Die Oberflaeche tauscht ihn
ueber den bereits vorhandenen, erprobten Endpunkt gegen eine Sitzung. So gibt
es keinen zweiten, halb parallelen Anmeldeweg - und nichts Geheimes steht
jemals dauerhaft in einer Adresszeile.
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, sso
from ..auth import get_current_admin
from ..config import settings
from ..database import get_db
from ..limiter import limiter, client_ip as real_client_ip
from ..models import AppLoginToken

logger = logging.getLogger("rimperio.routes.sso")

router = APIRouter(tags=["Single Sign-on"])

# Wie lange der Einmal-Schluessel gilt, mit dem der Browser seine Sitzung
# abholt. Sehr kurz: er wandert einmal durch die Adresszeile und ist dann weg.
HANDOFF_SECONDS = 120


class SSOConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = Field(default=None, pattern="^(entra|generic)$")
    label: Optional[str] = Field(default=None, max_length=100)
    tenant_id: Optional[str] = Field(default=None, max_length=100)
    client_id: Optional[str] = Field(default=None, max_length=200)
    client_secret: Optional[str] = Field(default=None, max_length=500)
    clear_client_secret: bool = False
    discovery_url: Optional[str] = Field(default=None, max_length=500)
    scopes: Optional[str] = Field(default=None, max_length=300)
    redirect_uri: Optional[str] = Field(default=None, max_length=500)
    auto_create_users: Optional[bool] = None
    default_role: Optional[str] = Field(default=None, pattern="^(user|admin)$")
    admin_groups: Optional[str] = Field(default=None, max_length=1000)
    admin_role_claim: Optional[str] = Field(default=None, max_length=100)
    allowed_domains: Optional[str] = Field(default=None, max_length=500)
    username_claim: Optional[str] = Field(default=None, max_length=60)


def _base_url(request: Request) -> str:
    """Unter welcher Adresse ist dieser Server von aussen zu erreichen?"""
    # Zentrale Regel (SITE_URL, gespeicherte Adresse, Browser, Proxy-Erkennung):
    # hinter einem externen HTTPS-Proxy stand hier sonst http:// - Entra lehnt
    # eine solche Rueckadresse ab.
    from .. import public_url
    return public_url.base_url(request)


# =============================================================================
# Oeffentlich: gibt es den Knopf?
# =============================================================================
@router.get("/api/auth/sso/status")
async def sso_status(db: AsyncSession = Depends(get_db)):
    """Für den Anmeldeschirm: Knopf anzeigen — ja oder nein?

    Antwortet absichtlich knapp. Mandant, Anwendungs-ID und alles Weitere
    gehen niemanden etwas an, der noch nicht angemeldet ist.
    """
    try:
        config = await sso.load_config(db)
    except Exception as exc:                                        # DB noch nicht da
        logger.debug("SSO-Status nicht lesbar: %s", exc)
        return {"enabled": False, "label": ""}
    return {
        "enabled": bool(config.get("enabled")),
        "label": config.get("label") or "Mit Microsoft 365 anmelden",
    }


@router.get("/api/auth/sso/start")
@limiter.limit("20/minute")
async def sso_start(request: Request, next: str = Query("", max_length=200),
                    db: AsyncSession = Depends(get_db)):
    """Beginnt den Anmeldevorgang und schickt den Browser zum Anbieter."""
    config = await sso.load_config(db)
    if not config.get("enabled"):
        raise HTTPException(status_code=404, detail="Single Sign-on ist nicht eingeschaltet.")

    try:
        flow = await sso.begin(config, _base_url(request), next_url=next)
    except (ValueError, RuntimeError) as exc:
        await journal.record(db, category="auth", event="sso.start.failed", level="error",
                             message=f"Anmeldung über Single Sign-on nicht möglich: {exc}",
                             actor="anonymous", source_ip=real_client_ip(request), ok=False)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RedirectResponse(url=flow["url"], status_code=302)


@router.get("/api/auth/sso/callback")
@limiter.limit("30/minute")
async def sso_callback(request: Request, code: str = Query(default=""),
                       state: str = Query(default=""),
                       error: str = Query(default=""),
                       error_description: str = Query(default=""),
                       db: AsyncSession = Depends(get_db)):
    """Nimmt die Rückkehr vom Anbieter entgegen und macht daraus eine Sitzung."""
    source_ip = real_client_ip(request)

    if error:
        await journal.record(
            db, category="auth", event="sso.denied", level="warning",
            message=f"Der Anbieter hat die Anmeldung abgelehnt: {error_description or error}",
            actor="anonymous", source_ip=source_ip, ok=False)
        return RedirectResponse(url=f"/login?sso_error={_short(error_description or error)}",
                                status_code=302)

    if not code or not state:
        return RedirectResponse(url="/login?sso_error=Unvollst%C3%A4ndige%20Antwort",
                                status_code=302)

    config = await sso.load_config(db)
    if not config.get("enabled"):
        return RedirectResponse(url="/login?sso_error=Nicht%20eingeschaltet", status_code=302)

    try:
        claims = await sso.exchange(config, code, state, _base_url(request))
        user = await sso.upsert_user(db, config, claims)
    except PermissionError as exc:
        await journal.record(
            db, category="auth", event="sso.rejected", level="warning",
            message=f"Anmeldung abgewiesen: {exc}", actor="anonymous",
            source_ip=source_ip, ok=False)
        return RedirectResponse(url=f"/login?sso_error={_short(str(exc))}", status_code=302)
    except (ValueError, RuntimeError) as exc:
        await journal.record(
            db, category="auth", event="sso.failed", level="error",
            message=f"Anmeldung fehlgeschlagen: {exc}", actor="anonymous",
            source_ip=source_ip, ok=False)
        return RedirectResponse(url=f"/login?sso_error={_short(str(exc))}", status_code=302)

    # Einmal-Schluessel fuer die Uebergabe an die Oberflaeche.
    handoff = secrets.token_hex(32)
    db.add(AppLoginToken(
        user_id=user.id, token=handoff,
        expires_at=datetime.utcnow() + timedelta(seconds=HANDOFF_SECONDS)))
    await db.commit()

    await journal.record(
        db, category="auth", event="sso.login",
        message=f"'{user.username}' hat sich über Single Sign-on angemeldet.",
        actor=user.username, source_ip=source_ip,
        detail={"role": user.role, "provider": config.get("provider")})

    target = claims.get("_next") or ""
    # Nur Pfade auf diesem Server: "//boese.example" oder "/\\boese" waeren fuer
    # den Browser fremde Adressen (offene Weiterleitung). Und kodiert, damit
    # niemand ueber "next" weitere Parameter einschmuggelt.
    from urllib.parse import quote
    safe_target = (target.startswith("/") and not target.startswith(("//", "/\\"))
                   and "\\" not in target and len(target) <= 200)
    suffix = f"&next={quote(target, safe='/')}" if safe_target else ""
    return RedirectResponse(url=f"/login?sso={handoff}{suffix}", status_code=302)


def _short(text: str) -> str:
    """Fehlertext fuer die Adresszeile - kurz und ohne Sonderzeichen-Aerger."""
    from urllib.parse import quote
    return quote((text or "Unbekannter Fehler").strip()[:200], safe="")


# =============================================================================
# Verwaltung
# =============================================================================
admin_router = APIRouter(prefix="/api/sso", tags=["Single Sign-on"])


@admin_router.get("/config")
async def get_config(request: Request, db: AsyncSession = Depends(get_db),
                     _=Depends(get_current_admin)):
    """Die Einstellung — ohne das Client-Geheimnis."""
    config = await sso.load_config(db)
    return {
        "config": sso.public_config(config),
        "providers": [{"id": key, **value} for key, value in sso.PROVIDERS.items()],
        "guide": sso.setup_guide(config, _base_url(request)),
    }


@admin_router.put("/config")
async def put_config(data: SSOConfigRequest, request: Request,
                     db: AsyncSession = Depends(get_db),
                     admin=Depends(get_current_admin)):
    """Speichert die Einstellung."""
    try:
        config = await sso.save_config(db, data.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await journal.record(
        db, category="settings", event="sso.configured", level="warning",
        message=(f"Single Sign-on {'eingeschaltet' if config.get('enabled') else 'ausgeschaltet'} "
                 f"({config.get('provider')})."),
        actor=admin.username, source_ip=real_client_ip(request))
    return {"config": config, "guide": sso.setup_guide(await sso.load_config(db),
                                                       _base_url(request))}


@admin_router.post("/test")
async def test_connection(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Erreichen wir den Anbieter, und beschreibt er sich vollständig?

    Das prüft die halbe Einrichtung, ohne dass sich jemand anmelden muss:
    stimmt die Verzeichnis-ID, antwortet der Anbieter, liefert er seine
    Schlüssel? Was der Test nicht prüfen kann, ist das Client-Geheimnis — das
    zeigt sich erst bei der ersten echten Anmeldung.
    """
    config = await sso.load_config(db)
    try:
        meta = await sso.metadata(config, force=True)
        keys = await sso.jwks(config, force=True)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:                                        # defensiv
        return {"ok": False, "message": f"Der Anbieter war nicht erreichbar: {exc}"}

    return {
        "ok": True,
        "issuer": meta.get("issuer"),
        "authorization_endpoint": meta.get("authorization_endpoint"),
        "keys": len(keys.get("keys", [])),
        "message": ("Der Anbieter antwortet und liefert seine Schlüssel. Ob das "
                    "Client-Geheimnis stimmt, zeigt die erste Anmeldung."),
    }


router.include_router(admin_router)
