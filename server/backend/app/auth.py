# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.30.17.22.26
# Beschreibung: RIMPERIO - Auth Hilfsfunktionen (JWT, MFA-Pending-Token, Lockout)
# ==============================================================================

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

# MFA-Pending-Token: 5 Minuten, reicht für Code-Eingabe
MFA_PENDING_TOKEN_MINUTES = 5

# Lockout-Politik: 10 Falschversuche → 15min Sperre, danach Reset
MFA_FAIL_THRESHOLD = 10
MFA_LOCKOUT_MINUTES = 15


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    # Default-Scope auf "access" setzen, falls nicht überschrieben
    to_encode.setdefault("scope", SCOPE_ACCESS)
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.jwt_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


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
    return user

async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin erforderlich")
    return current_user


async def admin_from_token(token: str) -> Optional[User]:
    """Prueft einen Token von Hand und gibt den Admin zurueck - sonst None.

    Wofuer: Zwei Wege koennen keinen `Authorization`-Kopf mitschicken - der
    Ereignisstrom (`EventSource`) und das Terminal (`WebSocket`). Beide bekommen
    den Token deshalb als Abfrageparameter und pruefen ihn hier. Es ist dieselbe
    Pruefung wie sonst, nur nicht ueber die uebliche Abhaengigkeit.

    Kein HTTPException: die beiden Aufrufer muessen den Fehler selbst in ihrem
    Protokoll melden (SSE-Ereignis bzw. WebSocket-Schliessgrund).
    """
    from .database import async_session

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("scope", SCOPE_ACCESS) != SCOPE_ACCESS:
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
    return user