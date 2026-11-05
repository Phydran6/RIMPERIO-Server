"""
Datei:        security.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Alles rund um Zugang: Passwörter hashen (Argon2), Sitzungs-Token
              ausstellen und prüfen (JWT), Agent-Token erzeugen und abgleichen.
              Dazu die FastAPI-Abhängigkeiten, die Routen vor unberechtigtem
              Zugriff schützen — für Benutzer wie für Agents.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_session
from .models import Agent, IntegrationToken, User

_ph = PasswordHasher()
_JWT_ALG = "HS256"


# --- Passwörter ------------------------------------------------------------
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except Exception:
        return False


# --- Sitzungs-Token (Benutzer) --------------------------------------------
def create_session_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "uid": user.id,
        "role": user.role,
        "type": "session",
        "iat": now,
        "exp": now + timedelta(minutes=settings.rimperio_session_minutes),
    }
    return jwt.encode(payload, settings.rimperio_secret_key, algorithm=_JWT_ALG)


def decode_session_token(token: str) -> dict:
    return jwt.decode(token, settings.rimperio_secret_key, algorithms=[_JWT_ALG])


# --- Agent-Token -----------------------------------------------------------
def generate_agent_token() -> tuple[str, str]:
    """Liefert (Klartext-Token, SHA-256-Hash). Der Klartext verlässt den Server
    genau einmal — bei der Anmeldung des Agents."""
    token = secrets.token_urlsafe(32)
    return token, hash_agent_token(token)


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kein gültiger Token im Authorization-Header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


# --- FastAPI-Abhängigkeiten: Benutzer -------------------------------------
async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = _bearer(authorization)

    # 1) Benutzer-Sitzung (JWT)
    try:
        payload = decode_session_token(token)
        if payload.get("type") == "session":
            user = (
                await session.execute(select(User).where(User.username == payload.get("sub")))
            ).scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="Benutzer nicht gefunden oder gesperrt.")
            return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sitzung abgelaufen. Bitte neu anmelden.")
    except jwt.PyJWTError:
        pass  # Kein JWT — vielleicht ein Integrations-Token.

    # 2) Integrations-Token (langlebig, für n8n/KI)
    it = (
        await session.execute(
            select(IntegrationToken).where(IntegrationToken.token_hash == hash_agent_token(token))
        )
    ).scalar_one_or_none()
    if it is not None:
        it.last_used = datetime.now(timezone.utc)
        await session.commit()
        # Vertretungs-Benutzer, nicht in der Datenbank — trägt nur Name und Rolle.
        return User(
            id=0,
            username=f"integration:{it.name}",
            password_hash="",
            role=it.role,
            is_active=True,
        )

    raise HTTPException(status_code=401, detail="Token ungültig.")


def require_role(*roles: str):
    """Abhängigkeit, die eine der genannten Rollen verlangt."""

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if roles and user.role not in roles:
            raise HTTPException(status_code=403, detail="Dafür fehlt die Berechtigung.")
        return user

    return _dep


# --- FastAPI-Abhängigkeit: Agent ------------------------------------------
async def get_current_agent(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Agent:
    token = _bearer(authorization)
    token_hash = hash_agent_token(token)
    agent = (
        await session.execute(select(Agent).where(Agent.token_hash == token_hash))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=401, detail="Agent-Token unbekannt.")
    return agent


async def agent_from_query_token(
    token: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> Agent:
    """Für Verbindungen, die keinen Header tragen können (z. B. WebSocket)."""
    agent = (
        await session.execute(
            select(Agent).where(Agent.token_hash == hash_agent_token(token))
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=401, detail="Agent-Token unbekannt.")
    return agent
