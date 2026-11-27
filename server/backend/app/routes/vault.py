# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - API Zugangsdaten / Vaultwarden (Integrationen)
# ==============================================================================
"""
`/api/vault/…` - nur Administratoren. Geheimnisse (Master-Passwort,
client_secret, Passwoerter der Eintraege) werden nie ausgeliefert.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, vault
from ..auth import get_current_admin
from ..database import get_db
from ..models import BackupJob, BackupTarget, NasDevice, StoredSecret, User

router = APIRouter(prefix="/api/vault", tags=["Zugangsdaten"])


class VaultIn(BaseModel):
    enabled: bool = True
    url: str = Field(min_length=1, max_length=512)
    email: str = Field(min_length=3, max_length=255)
    master_password: str = Field(default="", description="Leer = unverändert")
    client_id: str = ""
    client_secret: str = Field(default="", description="Leer = unverändert")
    organization_id: str = ""
    collection_id: str = ""
    folder: str = vault.FOLDER_DEFAULT
    verify_tls: bool = True
    allow_http: bool = False
    totp: str = Field(default="", description="Nur falls das Konto Zwei-Faktor verlangt (einmalig)")


class SecretIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = "other"
    username: str = ""
    password: Optional[str] = None
    totp: Optional[str] = None
    uri: str = ""
    notes: str = ""


def _vault_error(exc: Exception) -> HTTPException:
    if isinstance(exc, vault.VaultNeedsTotp):
        return HTTPException(status_code=409, detail=f"need_totp: {exc}")
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    cfg = await vault.load_config(db)
    counts = {"local": 0, "vaultwarden": 0}
    for row in (await db.execute(select(StoredSecret))).scalars():
        counts[row.backend] = counts.get(row.backend, 0) + 1
    return {**vault.public_config(cfg), "counts": counts}


@router.put("/config")
async def put_config(data: VaultIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    cfg = await vault.load_config(db)
    new = dict(cfg)
    try:
        new["url"] = vault.normalize_url(data.url, data.allow_http)
    except vault.VaultError as exc:
        raise _vault_error(exc) from exc
    for k in ("enabled", "email", "client_id", "organization_id", "collection_id", "folder", "verify_tls", "allow_http"):
        new[k] = getattr(data, k)
    new["email"] = new["email"].strip().lower()
    if data.master_password:
        new["master_password"] = data.master_password
    if data.client_secret:
        new["client_secret"] = data.client_secret
    if not new["client_id"]:
        new["client_secret"] = ""
    if new["email"] != cfg.get("email") or new["url"] != cfg.get("url"):
        new["remember_token"] = ""
    try:
        info = await vault.test_connection(new, totp=data.totp)
    except vault.VaultError as exc:
        raise _vault_error(exc) from exc
    from datetime import datetime
    new["last_ok_at"], new["last_error"] = datetime.utcnow().isoformat(timespec="seconds") + "Z", ""
    await vault.save_config(db, new)
    journal.fire(category="security", event="vault.configured", actor=user.username,
                 message=f"Vaultwarden angebunden: {new['url']} ({new['email']}).")
    return {**vault.public_config(new), "info": info}


@router.post("/test")
async def test(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    cfg = await vault.load_config(db)
    if not cfg.get("url"):
        raise HTTPException(status_code=422, detail="Vaultwarden ist nicht eingerichtet.")
    try:
        info = await vault.test_connection(cfg)
    except vault.VaultError as exc:
        return {"ok": False, "message": str(exc), "need_totp": isinstance(exc, vault.VaultNeedsTotp)}
    await vault._save_cfg_tokens(db, cfg)
    return {"ok": True, "message": f"Verbunden als {info.get('email')} – {info.get('ciphers')} Einträge im Tresor.", "info": info}


@router.delete("/config")
async def disable(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    """Abschalten. Vorhandene Eintraege bleiben in Vaultwarden und werden weiter von dort gelesen."""
    cfg = await vault.load_config(db)
    cfg["enabled"] = False
    await vault.save_config(db, cfg)
    journal.fire(category="security", event="vault.disabled", actor=user.username,
                 message="Vaultwarden für neue Zugänge abgeschaltet.")
    return vault.public_config(cfg)


@router.post("/migrate")
async def migrate(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_admin)) -> dict:
    try:
        res = await vault.migrate_local(db)
    except vault.VaultError as exc:
        raise _vault_error(exc) from exc
    journal.fire(category="security", event="vault.migrated", actor=user.username,
                 message=f"{res['moved']} lokale Zugänge nach Vaultwarden verschoben, {len(res['failed'])} fehlgeschlagen.")
    return res


@router.get("/entries")
async def entries(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> List[dict]:
    """Alle Zugaenge, die RIMPERIO kennt - mit Verwendung."""
    rows = (await db.execute(select(StoredSecret).order_by(StoredSecret.kind, StoredSecret.name))).scalars().all()
    used: dict = {}
    for n in (await db.execute(select(NasDevice))).scalars():
        if n.secret_id:
            used.setdefault(n.secret_id, []).append(f"NAS {n.name}")
    for t in (await db.execute(select(BackupTarget))).scalars():
        if t.secret_id:
            used.setdefault(t.secret_id, []).append(f"Ort {t.name}")
    for j in (await db.execute(select(BackupJob))).scalars():
        if j.key_secret_id:
            used.setdefault(j.key_secret_id, []).append(f"Auftrag {j.name}")
    from .. import backupjobs
    default = await backupjobs.default_key(db, create=False)
    if default:
        used.setdefault(default, []).append("Standard-Backup-Schlüssel")
    return [{**vault.public_secret(r), "used_by": used.get(r.id, [])} for r in rows]


@router.post("/entries", status_code=201)
async def create_entry(data: SecretIn, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    try:
        row = await vault.put(db, secret_id=None, kind=data.kind, name=data.name, username=data.username,
                              password=data.password or "", totp=data.totp or "", uri=data.uri, notes=data.notes)
    except vault.VaultError as exc:
        raise _vault_error(exc) from exc
    return vault.public_secret(row)


@router.put("/entries/{secret_id}")
async def update_entry(secret_id: int, data: SecretIn, db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_admin)) -> dict:
    if await db.get(StoredSecret, secret_id) is None:
        raise HTTPException(status_code=404, detail="Unbekannter Zugang.")
    try:
        row = await vault.put(db, secret_id=secret_id, kind=data.kind, name=data.name, username=data.username,
                              password=data.password or None, totp=data.totp, uri=data.uri, notes=data.notes)
    except vault.VaultError as exc:
        raise _vault_error(exc) from exc
    return vault.public_secret(row)


@router.post("/entries/{secret_id}/check")
async def check_entry(secret_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    """Ist der Eintrag lesbar? (ohne Inhalt auszuliefern)"""
    try:
        data = await vault.get(db, secret_id)
    except vault.VaultError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "has_password": bool(data.get("password")), "has_totp": bool(data.get("totp")),
            "fields": sorted((data.get("fields") or {}).keys())}
