# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.16.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - LDAP-Einstellungen und Verbindungstest (nur Admin)
# ==============================================================================

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_admin
from ..database import get_db
from ..ldap_auth import (
    DEFAULT_CONFIG,
    SETTING_KEY,
    authenticate,
    load_config,
    public_config,
    role_for,
)
from ..models import Setting, User

logger = logging.getLogger("rimperio.ldap")
router = APIRouter(prefix="/api/ldap", tags=["LDAP"])


class LdapConfigUpdate(BaseModel):
    enabled: bool = False
    server_uri: str = ""
    start_tls: bool = False
    verify_cert: bool = True
    bind_dn: str = ""
    # Leer lassen = gespeichertes Passwort behalten (die Oberfläche bekommt es nie zu sehen).
    bind_password: str = ""
    base_dn: str = ""
    user_filter: str = Field(default="(sAMAccountName={username})")
    attr_email: str = "mail"
    attr_display_name: str = "displayName"
    attr_groups: str = "memberOf"
    required_group: str = ""
    admin_group: str = ""
    default_role: str = "user"
    auto_create_users: bool = True


class LdapTestRequest(BaseModel):
    """Test mit echten Anmeldedaten - so sieht man sofort, ob Filter und Gruppen passen."""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


@router.get("/config")
async def get_ldap_config(db: AsyncSession = Depends(get_db), _admin: User = Depends(get_current_admin)):
    """GET /api/ldap/config - aktuelle Einstellungen ohne Passwort."""
    return public_config(await load_config(db))


@router.put("/config")
async def update_ldap_config(
    data: LdapConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """PUT /api/ldap/config - Einstellungen speichern."""
    current = await load_config(db)
    new_config = {**DEFAULT_CONFIG, **current, **data.model_dump()}

    # Leeres Passwortfeld heißt "nicht angefasst", nicht "löschen".
    if not data.bind_password:
        new_config["bind_password"] = current.get("bind_password", "")

    if new_config["enabled"]:
        if not new_config["server_uri"].strip():
            raise HTTPException(400, "Server-Adresse fehlt")
        if not new_config["base_dn"].strip():
            raise HTTPException(400, "Basis-DN fehlt")
        if "{username}" not in new_config["user_filter"]:
            raise HTTPException(400, "Der Suchfilter muss den Platzhalter {username} enthalten")

    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = new_config
    else:
        db.add(Setting(key=SETTING_KEY, value=new_config, description="LDAP-/AD-Anmeldung"))
    await db.commit()

    return public_config(new_config)


@router.post("/test")
async def test_ldap(
    data: LdapTestRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """POST /api/ldap/test - Anmeldung eines echten Kontos durchspielen.

    Der Test läuft auch, wenn LDAP noch nicht aktiviert ist: sonst müsste man
    scharf schalten, bevor man weiß, ob die Einstellungen stimmen.
    """
    config = {**await load_config(db), "enabled": True}
    ok, details = await authenticate(config, data.username.strip(), data.password)

    if not ok:
        return {"success": False, "message": details.get("reason", "Anmeldung fehlgeschlagen")}

    groups = details.get("groups", [])
    return {
        "success": True,
        "message": "Anmeldung erfolgreich",
        "dn": details.get("dn"),
        "email": details.get("email"),
        "display_name": details.get("display_name"),
        "groups": groups[:50],
        "group_count": len(groups),
        "role": role_for(config, groups),
    }
