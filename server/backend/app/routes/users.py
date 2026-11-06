# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.30.17.22.26
# Beschreibung: RIMPERIO - Users API Endpoints (inkl. Admin-MFA-Reset)
# ==============================================================================

from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models import User, MFABackupCode
from ..schemas import UserCreate, UserUpdate, UserResponse
from ..auth import (get_current_user, get_current_admin, get_password_hash,
                    verify_password)
from ..limiter import client_ip as real_client_ip
from .. import journal

router = APIRouter(prefix="/api/users", tags=["Users"])

@router.get("", response_model=List[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(User).order_by(User.username))
    return result.scalars().all()

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    return user

@router.post("", response_model=UserResponse, status_code=201)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    exists = (await db.execute(select(User).where(User.username == data.username))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="Username existiert bereits")
    user = User(username=data.username, email=data.email, password_hash=get_password_hash(data.password), role=data.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, data: UserUpdate, request: Request,
                      db: AsyncSession = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """Konto ändern.

    Wer darf was:

    * **Administratoren** dürfen jedes Konto ändern, samt Rolle und Zustand.
    * **Alle anderen** dürfen nur ihr eigenes Konto anfassen, und davon nur
      E-Mail-Adresse und Passwort. Rolle und Aktiv-Schalter sind tabu - auch
      der eigene: ein Konto, das sich selbst deaktiviert, ist ein Supportfall
      ohne Gewinn für irgendwen.

    Neu: **Wer sein eigenes Passwort ändert, muss das alte nennen.** Vorher
    genügte eine gültige Sitzung. Wer sich einen Token beschafft hatte - über
    einen unbeaufsichtigten Rechner oder eine gestohlene Sitzung -, konnte
    damit das Passwort setzen und den rechtmäßigen Besitzer aussperren.
    Administratoren, die ein fremdes Passwort zurücksetzen, brauchen es
    weiterhin nicht: sie kennen es ja nicht.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")

    is_admin = current_user.role == "admin"
    is_self = current_user.id == user_id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if data.role is not None and not is_admin:
        raise HTTPException(status_code=403, detail="Rollenänderung nur durch Admin")
    if data.is_active is not None and not is_admin:
        raise HTTPException(status_code=403,
                            detail="Den Aktiv-Zustand setzt nur ein Administrator.")

    if data.password and is_self:
        if (user.auth_source or "local") != "local":
            raise HTTPException(
                status_code=400,
                detail=("Dieses Konto kommt aus dem Verzeichnis bzw. von einem "
                        "Anmeldedienst - ein Passwort gibt es hier nicht zu ändern."))
        if not data.current_password:
            raise HTTPException(
                status_code=400,
                detail="Zum Ändern des eigenen Passworts das aktuelle mit angeben.")
        if not verify_password(data.current_password, user.password_hash):
            await journal.record(
                db, category="auth", event="password.change.failed", level="warning",
                message=f"Passwortänderung für '{user.username}' abgelehnt: altes Passwort falsch.",
                actor=current_user.username, source_ip=real_client_ip(request), ok=False)
            raise HTTPException(status_code=403, detail="Das aktuelle Passwort stimmt nicht.")

    changed = []
    if data.email is not None:
        user.email = data.email
        changed.append("E-Mail")
    if data.role is not None:
        user.role = data.role
        changed.append(f"Rolle → {data.role}")
    if data.is_active is not None:
        user.is_active = data.is_active
        changed.append("aktiv" if data.is_active else "deaktiviert")
    if data.password:
        user.password_hash = get_password_hash(data.password)
        changed.append("Passwort")

    await db.commit()
    await db.refresh(user)

    if changed:
        await journal.record(
            db, category="auth", event="user.updated", level="warning",
            message=f"Konto '{user.username}' geändert: {', '.join(changed)}.",
            actor=current_user.username, source_ip=real_client_ip(request),
            target=user.username, detail={"changed": changed, "self": is_self})
    return user

@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_admin)):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Selbst löschen nicht möglich")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    await db.delete(user)
    await db.commit()


@router.post("/{user_id}/mfa/reset", status_code=204)
async def admin_reset_mfa(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Notfall-Reset: Admin kann MFA für andere User deaktivieren (z. B. bei verlorenem Handy).
    Eigenen Account aus Sicherheitsgründen NICHT über diesen Endpoint resetbar."""
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Eigenes MFA bitte über /api/auth/mfa/disable deaktivieren")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_failed_count = 0
    user.mfa_locked_until = None
    await db.execute(delete(MFABackupCode).where(MFABackupCode.user_id == user_id))
    await db.commit()
