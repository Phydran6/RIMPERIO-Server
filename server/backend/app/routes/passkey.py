# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.18.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Passkeys (WebAuthn): registrieren, verwalten, anmelden
# ==============================================================================
"""
Anmeldung mit Passkey — Windows Hello, Face ID, Fingerabdruck oder ein
Sicherheitsschlüssel statt Passwort und TOTP-Code.

Warum das sicherer ist: Der private Schlüssel verlässt das Gerät nie, und die
Signatur ist an die Domäne gebunden. Eine nachgebaute Anmeldeseite bekommt damit
nichts Verwertbares — anders als bei Passwort oder abgetipptem Einmalcode.

Zwei Abläufe, beide zweistufig:
  * Registrieren  (angemeldet): /register/options -> Browser -> /register/verify
  * Anmelden      (offen):      /login/options    -> Browser -> /login/verify

Die Zufallsaufgabe ("Challenge") zwischen den beiden Schritten liegt im
Arbeitsspeicher dieses Prozesses. Das genügt, weil das Backend als ein einzelner
Uvicorn-Prozess läuft; bei mehreren Arbeitsprozessen müsste sie in die Datenbank
oder einen gemeinsamen Zwischenspeicher wandern.
"""

import json
import logging
import secrets
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import issue_access_token, get_current_user
from ..config import settings
from ..database import get_db
from ..limiter import limiter
from ..models import User, WebAuthnCredential
from ..schemas import Token

logger = logging.getLogger("rimperio.passkey")
router = APIRouter(prefix="/api/auth/passkey", tags=["Passkey"])

CHALLENGE_TTL_SECONDS = 300

# Schlüssel -> (Challenge, Ablaufzeitpunkt, Benutzername oder None)
_challenges: dict = {}


def _store_challenge(challenge: bytes, username: Optional[str]) -> str:
    _cleanup_challenges()
    handle = secrets.token_urlsafe(24)
    _challenges[handle] = (challenge, time.time() + CHALLENGE_TTL_SECONDS, username)
    return handle


def _take_challenge(handle: str):
    """Holt eine Challenge und entwertet sie sofort (jede gilt genau einmal)."""
    _cleanup_challenges()
    entry = _challenges.pop(handle, None)
    if not entry:
        return None, None
    challenge, expires, username = entry
    if expires < time.time():
        return None, None
    return challenge, username


def _cleanup_challenges() -> None:
    now = time.time()
    for key in [k for k, (_, expires, _) in _challenges.items() if expires < now]:
        _challenges.pop(key, None)


# =============================================================================
# Herkunft (Origin) und Domäne (RP-ID)
# =============================================================================

def _origin_and_rp_id(request: Request) -> tuple:
    """Ermittelt erwartete Herkunft und Domäne für die Signaturprüfung.

    SITE_URL hat Vorrang — der Wert ist bewusst gesetzt und damit vertrauenswürdig.
    Sonst wird der Origin-Header genommen; hinter dem Reverse Proxy ist das die
    Adresse, die der Browser tatsächlich benutzt hat.
    """
    site_url = (settings.site_url or "").strip()
    if site_url:
        parts = urlsplit(site_url)
        return f"{parts.scheme}://{parts.netloc}", (parts.hostname or "")

    origin = request.headers.get("origin")
    if not origin:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        origin = f"{proto}://{host}"

    parts = urlsplit(origin)
    return f"{parts.scheme}://{parts.netloc}", (parts.hostname or "")


def _require_lib():
    try:
        import webauthn  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Passkeys sind im Backend nicht verfügbar (Paket 'webauthn' fehlt)",
        )


# =============================================================================
# Schemas
# =============================================================================

class PasskeyRegisterVerify(BaseModel):
    challenge_handle: str
    credential: dict
    name: str = Field(default="", max_length=100)


class PasskeyLoginOptions(BaseModel):
    # Optional: ohne Benutzernamen sucht der Browser selbst einen passenden
    # Passkey heraus (Discoverable Credential).
    username: str = ""


class PasskeyLoginVerify(BaseModel):
    challenge_handle: str
    credential: dict


class PasskeyRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


# =============================================================================
# Registrieren
# =============================================================================

@router.post("/register/options")
async def register_options(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Schritt 1: Aufgabe für den Browser erzeugen."""
    _require_lib()
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    from webauthn.helpers import base64url_to_bytes

    _, rp_id = _origin_and_rp_id(request)
    if not rp_id:
        raise HTTPException(400, "Domäne konnte nicht bestimmt werden (SITE_URL setzen)")

    existing = (await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == current_user.id)
    )).scalars().all()

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="RIMPERIO",
        user_id=str(current_user.id).encode("utf-8"),
        user_name=current_user.username,
        user_display_name=current_user.username,
        # Bereits registrierte Schlüssel ausschließen - sonst legt derselbe
        # Stick stillschweigend einen zweiten Eintrag an.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )

    handle = _store_challenge(options.challenge, current_user.username)
    return {"challenge_handle": handle, "options": json.loads(options_to_json(options))}


@router.post("/register/verify")
async def register_verify(
    request: Request,
    data: PasskeyRegisterVerify,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Schritt 2: Antwort des Browsers prüfen und Passkey speichern."""
    _require_lib()
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    challenge, username = _take_challenge(data.challenge_handle)
    if not challenge or username != current_user.username:
        raise HTTPException(400, "Die Anfrage ist abgelaufen — bitte erneut versuchen")

    origin, rp_id = _origin_and_rp_id(request)
    try:
        verified = verify_registration_response(
            credential=json.dumps(data.credential),
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
        )
    except Exception as exc:
        logger.warning("Passkey-Registrierung abgelehnt: %s", exc)
        raise HTTPException(400, f"Passkey konnte nicht geprüft werden: {exc}")

    credential_id = bytes_to_base64url(verified.credential_id)
    already = (await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
    )).scalar_one_or_none()
    if already:
        raise HTTPException(409, "Dieser Passkey ist bereits hinterlegt")

    transports = data.credential.get("response", {}).get("transports") or []

    record = WebAuthnCredential(
        user_id=current_user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count or 0,
        name=(data.name or "").strip() or "Passkey",
        transports=",".join(transports)[:255] if isinstance(transports, list) else None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return _as_dict(record)


# =============================================================================
# Verwalten
# =============================================================================

def _as_dict(record: WebAuthnCredential) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "created_at": record.created_at,
        "last_used_at": record.last_used_at,
        "transports": (record.transports or "").split(",") if record.transports else [],
    }


@router.get("/credentials")
async def list_credentials(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Passkeys des angemeldeten Benutzers."""
    rows = (await db.execute(
        select(WebAuthnCredential)
        .where(WebAuthnCredential.user_id == current_user.id)
        .order_by(WebAuthnCredential.created_at)
    )).scalars().all()
    return {"items": [_as_dict(r) for r in rows]}


@router.put("/credentials/{credential_id}")
async def rename_credential(
    credential_id: int,
    data: PasskeyRename,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(WebAuthnCredential, credential_id)
    if not record or record.user_id != current_user.id:
        raise HTTPException(404, "Passkey nicht gefunden")
    record.name = data.name.strip()
    await db.commit()
    return _as_dict(record)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(WebAuthnCredential, credential_id)
    if not record or record.user_id != current_user.id:
        raise HTTPException(404, "Passkey nicht gefunden")
    await db.delete(record)
    await db.commit()
    return None


# =============================================================================
# Anmelden
# =============================================================================

@router.post("/login/options")
@limiter.limit("20/minute")
async def login_options(
    request: Request,
    data: PasskeyLoginOptions,
    db: AsyncSession = Depends(get_db),
):
    """Schritt 1 der Anmeldung. Offen erreichbar.

    Ohne Benutzernamen werden keine Schlüssel vorgegeben — der Browser bietet
    dann selbst an, was er für diese Domäne gespeichert hat. Mit Benutzernamen
    wird die Liste eingegrenzt; existiert er nicht, kommen trotzdem gültige
    Optionen zurück, damit sich über diesen Weg keine Konten ausspähen lassen.
    """
    _require_lib()
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
    from webauthn.helpers import base64url_to_bytes

    _, rp_id = _origin_and_rp_id(request)
    if not rp_id:
        raise HTTPException(400, "Domäne konnte nicht bestimmt werden (SITE_URL setzen)")

    username = (data.username or "").strip()
    allow = []
    if username:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user:
            rows = (await db.execute(
                select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
            )).scalars().all()
            allow = [
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(r.credential_id))
                for r in rows
            ]

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    handle = _store_challenge(options.challenge, username or None)
    return {"challenge_handle": handle, "options": json.loads(options_to_json(options))}


@router.post("/login/verify", response_model=Token)
@limiter.limit("20/minute")
async def login_verify(
    request: Request,
    data: PasskeyLoginVerify,
    db: AsyncSession = Depends(get_db),
):
    """Schritt 2 der Anmeldung: Signatur prüfen und Zugangstoken ausstellen."""
    _require_lib()
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    challenge, _ = _take_challenge(data.challenge_handle)
    if not challenge:
        raise HTTPException(400, "Die Anfrage ist abgelaufen — bitte erneut versuchen")

    credential_id = data.credential.get("id")
    if not credential_id:
        raise HTTPException(400, "Unvollständige Antwort des Browsers")

    record = (await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
    )).scalar_one_or_none()
    if not record:
        raise HTTPException(401, "Passkey unbekannt")

    user = await db.get(User, record.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "Benutzer nicht verfügbar")

    origin, rp_id = _origin_and_rp_id(request)
    try:
        verified = verify_authentication_response(
            credential=json.dumps(data.credential),
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
            credential_public_key=base64url_to_bytes(record.public_key),
            credential_current_sign_count=record.sign_count,
        )
    except Exception as exc:
        logger.warning("Passkey-Anmeldung abgelehnt (%s): %s", user.username, exc)
        raise HTTPException(401, "Passkey konnte nicht bestätigt werden")

    record.sign_count = verified.new_sign_count
    record.last_used_at = datetime.utcnow()
    await db.commit()

    # Bewusst kein zusätzlicher MFA-Schritt: der Passkey ist bereits zwei
    # Faktoren (Gerät + Entsperrung durch PIN/Biometrie).
    return Token(access_token=issue_access_token(user))
