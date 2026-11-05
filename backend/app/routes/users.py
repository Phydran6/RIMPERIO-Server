"""
Datei:        routes/users.py
Projekt:      RIMPERIO
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Benutzerverwaltung — nur für Admins. Anlegen, Rolle und Sperre
              ändern, Passwort zurücksetzen, löschen. Mit zwei Sicherungen: man
              kann sich nicht selbst löschen und nicht den letzten Admin entfernen.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import User
from ..schemas import UserCreate, UserOut
from ..security import hash_password, require_role

router = APIRouter(prefix="/api/users", tags=["users"])

ROLES = {"admin", "operator", "readonly"}


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=6, max_length=256)


async def _count_admins(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
            )
        ).scalar_one()
    )


@router.get("", response_model=list[UserOut])
async def list_users(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    rows = (await session.execute(select(User).order_by(User.username))).scalars().all()
    return [UserOut.model_validate(u) for u in rows]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    data: UserCreate,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Rolle muss eine von {sorted(ROLES)} sein.")
    exists = (
        await session.execute(select(User).where(User.username == data.username))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="Benutzername gibt es schon.")
    user = User(username=data.username, password_hash=hash_password(data.password), role=data.role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: int,
    data: UserPatch,
    current: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden.")

    if data.role is not None:
        if data.role not in ROLES:
            raise HTTPException(status_code=400, detail="Unbekannte Rolle.")
        # Letzten Admin nicht herabstufen.
        if user.role == "admin" and data.role != "admin" and await _count_admins(session) <= 1:
            raise HTTPException(status_code=400, detail="Der letzte Admin kann nicht herabgestuft werden.")
        user.role = data.role
    if data.is_active is not None:
        if not data.is_active and user.role == "admin" and await _count_admins(session) <= 1:
            raise HTTPException(status_code=400, detail="Der letzte Admin kann nicht gesperrt werden.")
        user.is_active = data.is_active
    if data.new_password:
        user.password_hash = hash_password(data.new_password)

    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Sich selbst kann man nicht löschen.")
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden.")
    if user.role == "admin" and await _count_admins(session) <= 1:
        raise HTTPException(status_code=400, detail="Der letzte Admin kann nicht gelöscht werden.")
    await session.delete(user)
    await session.commit()
    return {"status": "ok"}
