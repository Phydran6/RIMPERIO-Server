# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.30.17.22.26
# Beschreibung: RIMPERIO - MFA / TOTP Endpoints (Setup, Verify, Disable, Backup-Codes)
# ==============================================================================

import io
import base64
import secrets
from datetime import datetime
from urllib.parse import quote

import bcrypt
import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, verify_password
from ..database import get_db
from ..limiter import limiter
from ..models import MFABackupCode, User
from ..schemas import (
    MFADisableRequest,
    MFARegenerateRequest,
    MFARegenerateResponse,
    MFASetupResponse,
    MFAStatusResponse,
    MFAVerifyRequest,
    MFAVerifyResponse,
)

router = APIRouter(prefix="/api/auth/mfa", tags=["MFA"])

BACKUP_CODE_COUNT = 10
BACKUP_CODE_BYTES = 5  # 10 Hex-Zeichen


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _normalize_code(code: str) -> str:
    """User-Input für Backup-Codes vergleichbar machen: Whitespace/Dashes raus, Großbuchstaben."""
    return code.strip().replace(" ", "").replace("-", "").upper()


def _generate_backup_codes() -> list[str]:
    """10 zufällige Klartext-Backup-Codes (10 Hex-Zeichen, Anzeige als XXXXX-XXXXX)."""
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = secrets.token_hex(BACKUP_CODE_BYTES).upper()
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def _hash_backup_code(code: str) -> str:
    # Wir hashen die normalisierte Form, damit User mit oder ohne "-" eintippen darf
    return bcrypt.hashpw(_normalize_code(code).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _build_qr(otpauth_uri: str) -> str:
    """otpauth-URI → data:image/png;base64,... QR-Code."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(otpauth_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


def _otpauth_uri(secret: str, username: str, issuer: str = "RIMPERIO") -> str:
    """RFC 6238 / Google Authenticator URI Format."""
    label = quote(f"{issuer}:{username}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


# -----------------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------------
@router.get("/status", response_model=MFAStatusResponse)
async def mfa_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    remaining = 0
    if current_user.mfa_enabled:
        result = await db.execute(
            select(func.count(MFABackupCode.id)).where(
                MFABackupCode.user_id == current_user.id,
                MFABackupCode.used_at == None,  # noqa: E711
            )
        )
        remaining = result.scalar() or 0
    return MFAStatusResponse(
        enabled=current_user.mfa_enabled,
        backup_codes_remaining=remaining,
        locked_until=current_user.mfa_locked_until,
    )


# -----------------------------------------------------------------------------
# Setup (Schritt 1) - Secret generieren + QR-Code; noch NICHT aktiv
# -----------------------------------------------------------------------------
@router.post("/setup", response_model=MFASetupResponse)
@limiter.limit("5/minute")
async def mfa_setup(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA ist bereits aktiv. Erst deaktivieren.")

    secret = pyotp.random_base32()
    current_user.mfa_secret = secret
    await db.commit()

    uri = _otpauth_uri(secret, current_user.username)
    return MFASetupResponse(secret=secret, otpauth_uri=uri, qr_image=_build_qr(uri))


# -----------------------------------------------------------------------------
# Verify (Schritt 2) - Erster TOTP-Code → MFA aktivieren + Backup-Codes
# -----------------------------------------------------------------------------
@router.post("/verify", response_model=MFAVerifyResponse)
@limiter.limit("10/minute")
async def mfa_verify(
    request: Request,
    data: MFAVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA ist bereits aktiv.")
    if not current_user.mfa_secret:
        raise HTTPException(status_code=400, detail="Bitte zuerst /setup aufrufen.")

    totp = pyotp.TOTP(current_user.mfa_secret)
    if not totp.verify(data.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Code ungültig")

    # Alte (theoretisch unmögliche) Backup-Codes räumen
    await db.execute(delete(MFABackupCode).where(MFABackupCode.user_id == current_user.id))

    plain_codes = _generate_backup_codes()
    for code in plain_codes:
        db.add(MFABackupCode(user_id=current_user.id, code_hash=_hash_backup_code(code)))

    current_user.mfa_enabled = True
    current_user.mfa_failed_count = 0
    current_user.mfa_locked_until = None
    await db.commit()

    return MFAVerifyResponse(enabled=True, backup_codes=plain_codes)


# -----------------------------------------------------------------------------
# Disable - Passwort + aktueller Code/Backup
# -----------------------------------------------------------------------------
@router.post("/disable", status_code=204)
@limiter.limit("5/minute")
async def mfa_disable(
    request: Request,
    data: MFADisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA ist nicht aktiv.")
    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Passwort falsch")

    if not await consume_totp_or_backup(db, current_user, data.code):
        raise HTTPException(status_code=400, detail="Code ungültig")

    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    current_user.mfa_failed_count = 0
    current_user.mfa_locked_until = None
    await db.execute(delete(MFABackupCode).where(MFABackupCode.user_id == current_user.id))
    await db.commit()


# -----------------------------------------------------------------------------
# Backup-Codes neu generieren
# -----------------------------------------------------------------------------
@router.post("/backup-codes/regenerate", response_model=MFARegenerateResponse)
@limiter.limit("3/minute")
async def mfa_regenerate(
    request: Request,
    data: MFARegenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA ist nicht aktiv.")
    if not await consume_totp_or_backup(db, current_user, data.code):
        raise HTTPException(status_code=400, detail="Code ungültig")

    await db.execute(delete(MFABackupCode).where(MFABackupCode.user_id == current_user.id))
    plain_codes = _generate_backup_codes()
    for code in plain_codes:
        db.add(MFABackupCode(user_id=current_user.id, code_hash=_hash_backup_code(code)))
    await db.commit()
    return MFARegenerateResponse(backup_codes=plain_codes)


# -----------------------------------------------------------------------------
# Gemeinsame Verifizierung TOTP ODER Backup-Code (mit Verbrauch)
# -----------------------------------------------------------------------------
async def consume_totp_or_backup(db: AsyncSession, user: User, code: str) -> bool:
    """
    Verifiziert TOTP-Code oder Backup-Code.
    Bei Backup-Code wird er als verbraucht markiert.
    Caller committet im Erfolgsfall.
    """
    cleaned = _normalize_code(code)

    # Erst TOTP versuchen (rein numerisch, 6 Stellen)
    if user.mfa_secret and cleaned.isdigit() and len(cleaned) == 6:
        if pyotp.TOTP(user.mfa_secret).verify(cleaned, valid_window=1):
            return True

    # Dann Backup-Codes prüfen (gehasht ohne Dash, daher gegen normalisierte Form)
    result = await db.execute(
        select(MFABackupCode).where(
            MFABackupCode.user_id == user.id,
            MFABackupCode.used_at == None,  # noqa: E711
        )
    )
    for backup in result.scalars().all():
        if bcrypt.checkpw(cleaned.encode("utf-8"), backup.code_hash.encode("utf-8")):
            backup.used_at = datetime.utcnow()
            return True

    return False
