"""
Datei:        routes/auth.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Anmeldung der Benutzer. Passwort prüfen, Sitzungs-Token ausstellen,
              eigenes Konto abfragen, eigenes Passwort ändern.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import User
from ..schemas import LoginRequest, PasswordChange, TokenResponse, UserOut
from ..security import (
    create_session_token,
    get_current_user,
    hash_password,
    needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = (
        await session.execute(select(User).where(User.username == data.username))
    ).scalar_one_or_none()

    if user is None or not verify_password(data.password, user.password_hash):
        # Gleiche Antwort für „kein Benutzer" und „falsches Passwort".
        raise HTTPException(status_code=401, detail="Benutzername oder Passwort falsch.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Konto ist gesperrt.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
    user.last_login = datetime.now(timezone.utc)
    await session.commit()

    return TokenResponse(token=create_session_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/password")
async def change_password(
    data: PasswordChange,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Altes Passwort stimmt nicht.")
    user.password_hash = hash_password(data.new_password)
    await session.commit()
    return {"status": "ok", "detail": "Passwort geändert."}
