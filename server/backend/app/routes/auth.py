# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.30.17.22.26
# Beschreibung: RIMPERIO - Auth API Endpoints + App-Login (QR-Code) + MFA-Login
# ==============================================================================

import io
import json
import base64
import logging
import secrets
from datetime import datetime, timedelta
from typing import Union
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import qrcode

from ..database import get_db
from ..limiter import limiter
from ..models import User, AppLoginToken
from ..schemas import (
    Token,
    UserResponse,
    AppTokenResponse,
    AppTokenExchangeRequest,
    AppQRResponse,
    LoginMFARequired,
    MFALoginRequest,
)
from .. import journal, ldap_auth
from ..limiter import client_ip as real_client_ip
from ..auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_mfa_pending_token,
    decode_mfa_pending_token,
    is_user_mfa_locked,
    register_mfa_failure,
    clear_mfa_failures,
    get_current_user,
    MFA_PENDING_TOKEN_MINUTES,
)
from ..config import settings
from .mfa import consume_totp_or_backup

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_APP_TOKEN_TTL_MINUTES = 15


async def _login_via_ldap(db: AsyncSession, username: str, password: str):
    """Anmeldung am Verzeichnis; legt den Benutzer bei Bedarf an. None = kein Zugang.

    Ein bereits vorhandenes **lokales** Konto gleichen Namens wird nicht
    übernommen: sonst könnte ein gleichnamiges Verzeichniskonto das lokale
    Admin-Konto kapern. Solche Fälle müssen die Admins selbst auflösen.
    """
    config = await ldap_auth.load_config(db)
    if not config.get("enabled"):
        return None

    ok, details = await ldap_auth.authenticate(config, username, password)
    if not ok:
        return None

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if user:
        if (user.auth_source or "local") != "ldap":
            logging.getLogger("rimperio.ldap").warning(
                "LDAP-Anmeldung für '%s' abgelehnt: es gibt bereits ein lokales Konto mit diesem Namen",
                username,
            )
            return None
        # Stammdaten bei jeder Anmeldung nachziehen - Gruppenwechsel wirken sofort.
        user.role = details.get("role", "user")
        if details.get("email"):
            user.email = details["email"]
    else:
        if not config.get("auto_create_users", True):
            return None
        user = User(
            username=username,
            email=details.get("email"),
            # Unbrauchbarer Hash: Anmeldung ist nur über das Verzeichnis möglich.
            password_hash=get_password_hash(secrets.token_urlsafe(32)),
            role=details.get("role", "user"),
            is_active=True,
            auth_source="ldap",
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=None)
@limiter.limit("10/minute")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Union[Token, LoginMFARequired]:
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        # Zweiter Versuch über das Verzeichnis, falls eingerichtet.
        user = await _login_via_ldap(db, form_data.username, form_data.password)

    source_ip = real_client_ip(request)

    # Gleiche Fehlermeldung für "User existiert nicht" und "Passwort falsch"
    # verhindert User-Enumeration
    if not user:
        # Ins Systemtagebuch gehoert der Fehlversuch trotzdem: dort faellt eine
        # Reihe von Versuchen aus demselben Netz sofort auf.
        await journal.record(
            db, category="auth", event="login.failed", level="warning",
            message=f"Fehlgeschlagene Anmeldung für '{form_data.username}'.",
            actor=form_data.username, source_ip=source_ip, ok=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falsche Anmeldedaten")
    if not user.is_active:
        await journal.record(
            db, category="auth", event="login.disabled", level="warning",
            message=f"Anmeldung abgelehnt: '{user.username}' ist deaktiviert.",
            actor=user.username, source_ip=source_ip, ok=False)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Benutzer deaktiviert")

    # MFA aktiv → kein Vollzugriffs-Token, sondern Pending-Token zurückgeben
    if user.mfa_enabled:
        if is_user_mfa_locked(user):
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"MFA gesperrt bis {user.mfa_locked_until.isoformat()}Z",
            )
        return LoginMFARequired(
            mfa_token=create_mfa_pending_token(user.username),
            expires_in_seconds=MFA_PENDING_TOKEN_MINUTES * 60,
        )

    await journal.record(
        db, category="auth", event="login.ok",
        message=f"'{user.username}' hat sich angemeldet.",
        actor=user.username, source_ip=source_ip,
        detail={"role": user.role, "source": user.auth_source or "local", "mfa": False})
    return Token(access_token=create_access_token(data={"sub": user.username}))


@router.post("/login/mfa", response_model=Token)
@limiter.limit("10/minute")
async def login_mfa(
    request: Request,
    data: MFALoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Zweiter Login-Schritt: Pending-Token + TOTP/Backup-Code → Access-Token."""
    username = decode_mfa_pending_token(data.mfa_token)
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not user.mfa_enabled:
        raise HTTPException(status_code=401, detail="MFA-Token ungültig")

    if is_user_mfa_locked(user):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"MFA gesperrt bis {user.mfa_locked_until.isoformat()}Z",
        )

    if not await consume_totp_or_backup(db, user, data.code):
        register_mfa_failure(user)
        await db.commit()
        await journal.record(
            db, category="auth", event="mfa.failed", level="warning",
            message=f"Falscher Zweitfaktor für '{user.username}'.",
            actor=user.username, source_ip=real_client_ip(request), ok=False)
        raise HTTPException(status_code=401, detail="Code ungültig")

    clear_mfa_failures(user)
    await db.commit()
    await journal.record(
        db, category="auth", event="login.ok",
        message=f"'{user.username}' hat sich angemeldet (mit Zweitfaktor).",
        actor=user.username, source_ip=real_client_ip(request),
        detail={"role": user.role, "mfa": True})
    return Token(access_token=create_access_token(data={"sub": user.username}))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


# =============================================================================
# App-Authentifizierung (Android App via URL oder QR-Code)
# =============================================================================

@router.post("/app-token", response_model=AppTokenResponse)
async def create_app_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Erstellt einen kurzlebigen Einmal-Token (15 min) für die App-Authentifizierung.
    Alle noch nicht verwendeten alten Tokens des Users werden dabei invalidiert.
    """
    # Alte, ungenutzte Tokens für diesen User löschen
    await db.execute(
        delete(AppLoginToken).where(
            AppLoginToken.user_id == current_user.id,
            AppLoginToken.used_at == None,  # noqa: E711
        )
    )

    token = secrets.token_hex(32)  # 64 Hex-Zeichen = 256 bit Entropie
    expires_at = datetime.utcnow() + timedelta(minutes=_APP_TOKEN_TTL_MINUTES)

    app_token = AppLoginToken(
        user_id=current_user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(app_token)
    await db.commit()

    return AppTokenResponse(
        token=token,
        expires_at=expires_at,
        expires_in_seconds=_APP_TOKEN_TTL_MINUTES * 60,
    )


@router.post("/app-token/exchange", response_model=Token)
@limiter.limit("20/minute")
async def exchange_app_token(
    request: Request,
    data: AppTokenExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Tauscht einen App-Einmal-Token gegen einen vollwertigen JWT aus.
    Öffentlicher Endpoint – wird von der Android-App nach QR-Code-Scan aufgerufen.
    """
    result = await db.execute(
        select(AppLoginToken).where(
            AppLoginToken.token == data.token,
            AppLoginToken.used_at == None,  # noqa: E711
            AppLoginToken.expires_at > datetime.utcnow(),
        )
    )
    app_token = result.scalar_one_or_none()
    if not app_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ungültig oder abgelaufen")

    # Token sofort als benutzt markieren (verhindert Replay)
    app_token.used_at = datetime.utcnow()

    user = await db.get(User, app_token.user_id)
    if not user or not user.is_active:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Benutzer nicht verfügbar")

    await db.commit()

    return Token(access_token=create_access_token(data={"sub": user.username}))


@router.get("/app-qr", response_model=AppQRResponse)
async def get_app_qr(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generiert einen QR-Code für die App-Authentifizierung.
    Der QR-Code enthält die API-URL und einen kurzlebigen Einmal-Token.
    Die App scannt den Code und ruft /api/auth/app-token/exchange auf.
    """
    # Alte, ungenutzte Tokens invalidieren
    await db.execute(
        delete(AppLoginToken).where(
            AppLoginToken.user_id == current_user.id,
            AppLoginToken.used_at == None,  # noqa: E711
        )
    )

    token = secrets.token_hex(32)
    expires_at = datetime.utcnow() + timedelta(minutes=_APP_TOKEN_TTL_MINUTES)

    app_token = AppLoginToken(
        user_id=current_user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(app_token)
    await db.commit()

    # API-URL ermitteln: SITE_URL hat Vorrang, sonst aus Request ableiten
    api_url = settings.site_url
    if not api_url:
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        forwarded_host = request.headers.get("x-forwarded-host", request.url.netloc)
        api_url = f"{forwarded_proto}://{forwarded_host}"

    # QR-Code-Payload (JSON, damit die App es parsen kann)
    qr_payload = json.dumps({
        "url": api_url,
        "token": token,
        "type": "rimperio_app_auth_v1",
    }, separators=(",", ":"))

    # QR-Code als PNG generieren
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(qr_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    qr_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return AppQRResponse(
        qr_image=f"data:image/png;base64,{qr_b64}",
        token=token,
        expires_at=expires_at,
        expires_in_seconds=_APP_TOKEN_TTL_MINUTES * 60,
        api_url=api_url,
    )
