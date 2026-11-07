# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.30.17.22.26
# Beschreibung: RIMPERIO - Auth Hilfsfunktionen (JWT, MFA-Pending-Token, Lockout)
# ==============================================================================

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .database import get_db
from .models import User
from .schemas import TokenData

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Scope-Konstanten für JWTs
SCOPE_ACCESS = "access"
SCOPE_MFA_PENDING = "mfa_pending"
# Kurzlebiger Einmal-Zugang fuer Wege, die keinen Authorization-Kopf schicken
# koennen (EventSource, WebSocket, Download-Link). Siehe create_ticket().
SCOPE_TICKET = "ticket"
TICKET_SECONDS = 60
TICKET_PURPOSES = ("shell", "journal", "updates", "update-log", "backup-download", "api-docs")

# MFA-Pending-Token: 5 Minuten, reicht für Code-Eingabe
MFA_PENDING_TOKEN_MINUTES = 5

# Lockout-Politik: 10 Falschversuche → 15min Sperre, danach Reset
MFA_FAIL_THRESHOLD = 10
MFA_LOCKOUT_MINUTES = 15


# Vergleichs-Hash fuer unbekannte Benutzernamen: so dauert "gibt es nicht"
# genauso lange wie "Passwort falsch" - der Login verraet ueber die Antwortzeit
# nicht mehr, welche Konten es gibt.
_DUMMY_HASH = bcrypt.hashpw(b"rimperio-timing-guard", bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except (ValueError, TypeError):
        # Kaputter oder leerer Hash in der Datenbank: kein Zugang, kein 500.
        return False


def burn_password_check(plain: str) -> None:
    """Rechnet eine Passwortpruefung ins Leere (gleiche Laufzeit wie echt)."""
    verify_password(plain or "", _DUMMY_HASH)


def password_fingerprint(user: User) -> str:
    """Kurzer Abdruck des Passwort-Hashes - wandert als `pv` in jeden Token.

    Aendert sich das Passwort, passt der Abdruck nicht mehr, und alle vorher
    ausgestellten Sitzungen sind sofort ungueltig - statt bis zu 24 Stunden
    weiterzulaufen. Aus dem Abdruck laesst sich nichts zurueckrechnen: er ist
    ein HMAC mit dem JWT-Secret ueber den (ohnehin gesalzenen) bcrypt-Hash.
    """
    raw = (user.password_hash or "").encode("utf-8")
    return hmac.new(settings.jwt_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:16]

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    # Default-Scope auf "access" setzen, falls nicht überschrieben
    to_encode.setdefault("scope", SCOPE_ACCESS)
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.jwt_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def issue_access_token(user: User) -> str:
    """Vollwertiger Sitzungs-Token fuer diesen Benutzer (mit Passwort-Abdruck)."""
    return create_access_token({"sub": user.username, "pv": password_fingerprint(user)})


def create_ticket(user: User, purpose: str) -> str:
    """Einmal-Zugang fuer genau einen Zweck, 60 Sekunden gueltig.

    Wozu: EventSource, WebSocket und Download-Links koennen keinen
    Authorization-Kopf mitschicken. Frueher stand deshalb der volle 24-Stunden-
    Token in der Adresse - und damit in Proxy-Protokollen und im
    Browserverlauf. Ein Ticket dort ist wertlos, sobald es verbraucht oder
    abgelaufen ist, und es oeffnet nur den einen Weg, fuer den es ausgestellt
    wurde.
    """
    if purpose not in TICKET_PURPOSES:
        raise ValueError(f"Unbekannter Zweck '{purpose}'.")
    return create_access_token(
        {"sub": user.username, "scope": SCOPE_TICKET, "purpose": purpose,
         "pv": password_fingerprint(user)},
        expires_delta=timedelta(seconds=TICKET_SECONDS),
    )


def _fingerprint_ok(payload: dict, user: User) -> bool:
    presented = payload.get("pv")
    return bool(presented) and hmac.compare_digest(str(presented), password_fingerprint(user))


def create_mfa_pending_token(username: str) -> str:
    """Kurzlebiger JWT, der nur am /login/mfa-Endpoint akzeptiert wird."""
    return create_access_token(
        {"sub": username, "scope": SCOPE_MFA_PENDING},
        expires_delta=timedelta(minutes=MFA_PENDING_TOKEN_MINUTES),
    )


def decode_mfa_pending_token(token: str) -> str:
    """Validiert den Pending-Token und gibt den Username zurück."""
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA-Token ungültig oder abgelaufen")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise exc
    if payload.get("scope") != SCOPE_MFA_PENDING:
        raise exc
    username = payload.get("sub")
    if not username:
        raise exc
    return username


def is_user_mfa_locked(user: User) -> bool:
    """True wenn der User aktuell wegen zu vieler Falschversuche gesperrt ist."""
    return bool(user.mfa_locked_until and user.mfa_locked_until > datetime.utcnow())


def register_mfa_failure(user: User) -> None:
    """Zählt einen MFA-Fehlversuch und sperrt ggf. den User. Caller committet."""
    user.mfa_failed_count = (user.mfa_failed_count or 0) + 1
    if user.mfa_failed_count >= MFA_FAIL_THRESHOLD:
        user.mfa_locked_until = datetime.utcnow() + timedelta(minutes=MFA_LOCKOUT_MINUTES)
        user.mfa_failed_count = 0


def clear_mfa_failures(user: User) -> None:
    """Reset nach erfolgreichem MFA-Login. Caller committet."""
    user.mfa_failed_count = 0
    user.mfa_locked_until = None


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige Anmeldedaten",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        # Nur vollwertige Access-Tokens akzeptieren – kein MFA-Pending durchschleichen
        if payload.get("scope", SCOPE_ACCESS) != SCOPE_ACCESS:
            raise credentials_exception
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception
    # Passwort seit Ausstellung geaendert (oder Token aus einer Fassung ohne
    # Abdruck)? Dann gilt die Sitzung nicht mehr.
    if not _fingerprint_ok(payload, user):
        raise credentials_exception
    return user

async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin erforderlich")
    return current_user


async def admin_from_token(token: str, purpose: Optional[str] = None) -> Optional[User]:
    """Prueft einen Token von Hand und gibt den Admin zurueck - sonst None.

    Wofuer: Wege, die keinen `Authorization`-Kopf mitschicken koennen - der
    Ereignisstrom (`EventSource`), das Terminal (`WebSocket`) und der
    Download-Link. Mit `purpose` wird hier **nur** ein passendes Ticket
    angenommen (siehe create_ticket), kein voller Sitzungs-Token: der wuerde
    sonst in der Adresszeile und in Proxy-Protokollen landen.

    Ohne `purpose` (Aufruf mit Token aus dem Kopf) gilt der normale
    Sitzungs-Token.

    Kein HTTPException: die Aufrufer muessen den Fehler selbst in ihrem
    Protokoll melden (SSE-Ereignis bzw. WebSocket-Schliessgrund).
    """
    from .database import async_session

    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    scope = payload.get("scope", SCOPE_ACCESS)
    if purpose is None:
        if scope != SCOPE_ACCESS:
            return None
    elif scope != SCOPE_TICKET or payload.get("purpose") != purpose:
        return None
    username = payload.get("sub")
    if not username:
        return None

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()

    if not user or not user.is_active or user.role != "admin":
        return None
    if not _fingerprint_ok(payload, user):
        return None
    return user
