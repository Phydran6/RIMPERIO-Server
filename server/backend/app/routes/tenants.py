# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Mandanten verwalten (Benutzer → Mandanten)
# ==============================================================================
"""Mandanten anlegen, zuordnen, ein- und ausschalten.

Verwalten darf nur der **Betreiber** (Administrator ohne Mandant). Jeder
angemeldete Benutzer darf ``/api/tenants/context`` lesen - daraus baut die
Oberflaeche den Umschalter und blendet aus, was ein Mandant nicht nutzt.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import journal, tenancy, tokens
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..limiter import client_ip as real_client_ip
from ..models import (Agent, AgentToken, BackupJob, BackupTarget, Check, NasDevice, Notification,
                      PatchPolicy, PveHost, Script, SnmpCredential, SnmpDevice, SnmpTemplate, Tenant,
                      User)

router = APIRouter(prefix="/api/tenants", tags=["Mandanten"])

# Was sich einem Mandanten zuordnen laesst (Name in der API -> Modell).
ASSIGNABLE = {
    "agents": Agent,
    "snmp_devices": SnmpDevice,
    "snmp_credentials": SnmpCredential,
    "snmp_templates": SnmpTemplate,
    "pve_hosts": PveHost,
    "tokens": AgentToken,
    "scripts": Script,
    "checks": Check,
    "policies": PatchPolicy,
    "nas_devices": NasDevice,
    "backup_targets": BackupTarget,
    "backup_jobs": BackupJob,
}
# Was beim Anlegen mit "alles Bisherige uebernehmen" mitwandert: was einem
# Kunden gehoert. Skripte, Checks, Richtlinien und Vorlagen bleiben beim
# Betreiber - sie gelten dann weiter fuer alle.
ADOPT = ("agents", "snmp_devices", "snmp_credentials", "pve_hosts", "tokens", "nas_devices")


async def provider_admin(admin: User = Depends(get_current_admin)) -> User:
    """Nur der Betreiber verwaltet Mandanten - und zwar ohne Mandanten-Filter."""
    if admin.tenant_id is not None:
        raise HTTPException(status_code=403, detail="Mandanten verwaltet nur der Betreiber.")
    tenancy.set_scope(None)
    return admin


class TenantIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=64)
    is_active: bool = True
    contact_name: str = Field(default="", max_length=200)
    contact_email: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=4000)
    alert_emails: str = Field(default="", max_length=2000)
    # Beim Anlegen: alles, was noch keinem Mandanten gehoert, uebernehmen
    # (Geraete, SNMP, Proxmox, Zugangsschluessel). Fuer "nachtraeglich
    # einschalten, der bisherige Bestand ist der erste Kunde".
    adopt_unassigned: bool = False


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=64)
    is_active: Optional[bool] = None
    contact_name: Optional[str] = Field(default=None, max_length=200)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=4000)
    alert_emails: Optional[str] = Field(default=None, max_length=2000)


class EnableIn(BaseModel):
    enabled: bool


class AssignIn(BaseModel):
    # 0 = zurueck zum Betreiber (bzw. "gilt fuer alle")
    tenant_id: int = Field(..., ge=0)
    items: Dict[str, List[int]] = Field(default_factory=dict)


async def _counts(db: AsyncSession) -> Dict[Optional[int], dict]:
    out: Dict[Optional[int], dict] = {}
    for key, model in (("users", User), ("agents", Agent), ("snmp_devices", SnmpDevice),
                       ("pve_hosts", PveHost), ("tokens", AgentToken), ("nas_devices", NasDevice),
                       ("backup_jobs", BackupJob)):
        q = select(model.tenant_id, func.count()).group_by(model.tenant_id)
        if model is AgentToken:
            q = q.where(AgentToken.revoked_at.is_(None))
        for tid, n in (await db.execute(q)).all():
            out.setdefault(tid, {})[key] = int(n)
    return out


async def _unique_slug(db: AsyncSession, wanted: str, skip_id: Optional[int] = None) -> str:
    base = tenancy.slugify(wanted)
    slug, n = base, 2
    while True:
        q = select(Tenant.id).where(Tenant.slug == slug)
        if skip_id:
            q = q.where(Tenant.id != skip_id)
        if (await db.execute(q)).first() is None:
            return slug
        slug = f"{base[:60]}-{n}"
        n += 1


async def _get(db: AsyncSession, tenant_id: int) -> Tenant:
    t = await db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Mandant nicht gefunden.")
    return t


async def _agent_conflicts(db: AsyncSession, ids: List[int], target: Optional[int]) -> List[str]:
    """Geraete, die es beim Ziel schon gibt (gleicher Hostname und gleiche IP).

    Hostname + IP sind je Mandant eindeutig. Zwei Kunden duerfen beide
    "server01" auf 192.168.1.10 haben - beim Zusammenlegen waeren es aber zwei
    gleiche Geraete in einem Mandanten.
    """
    moving = (await db.execute(select(Agent.id, Agent.hostname, Agent.ip_address)
                               .where(Agent.id.in_(ids)))).all()
    out = []
    for aid, host, ip in moving:
        q = select(Agent.id).where(Agent.hostname == host, Agent.ip_address == ip,
                                   Agent.id.not_in(ids))
        q = q.where(Agent.tenant_id.is_(None) if target is None else Agent.tenant_id == target)
        if (await db.execute(q.limit(1))).first() is not None:
            out.append(f"{host} ({ip or 'ohne IP'})")
    return out


async def _move(db: AsyncSession, kind: str, ids: List[int], target: Optional[int]) -> int:
    model = ASSIGNABLE[kind]
    if not ids:
        return 0
    if kind == "agents":
        clash = await _agent_conflicts(db, ids, target)
        if clash:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=("Beim Ziel gibt es schon ein Gerät mit gleichem Namen und gleicher IP: "
                        + ", ".join(clash[:10]) + (" …" if len(clash) > 10 else "")
                        + ". Das doppelte Gerät zuerst entfernen."))
    res = await db.execute(update(model).where(model.id.in_(ids)).values(tenant_id=target)
                           .execution_options(synchronize_session=False))
    moved = res.rowcount or 0
    if kind == "agents":
        # Geraeteschluessel und Meldungen wandern mit ihrem Geraet.
        await db.execute(update(AgentToken).where(AgentToken.agent_id.in_(ids))
                         .values(tenant_id=target).execution_options(synchronize_session=False))
        uids = (await db.execute(select(Agent.agent_uid).where(Agent.id.in_(ids),
                                                              Agent.agent_uid.is_not(None)))).scalars().all()
        if uids:
            rows = (await db.execute(select(Notification).where(Notification.resolved_at.is_(None)))).scalars().all()
            for n in rows:
                if (n.data or {}).get("agent_uid") in uids:
                    n.tenant_id = target
    return moved


# ------------------------------------------------------------------------------
# Lesen
# ------------------------------------------------------------------------------
@router.get("/context")
async def context(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Fuer Oberflaeche und App: ist die Mandantenfaehigkeit an, wer bin ich,
    wohin kann ich umschalten?"""
    own = None
    if user.tenant_id is not None:
        t = await db.get(Tenant, user.tenant_id)
        own = tenancy.public(t) if t else None
    tenants = []
    if user.tenant_id is None and tenancy.enabled():
        rows = (await db.execute(select(Tenant).order_by(Tenant.name))).scalars().all()
        tenants = [{"id": t.id, "name": t.name, "slug": t.slug, "is_active": bool(t.is_active)} for t in rows]
    return {
        "enabled": tenancy.enabled(),
        "is_provider": user.tenant_id is None,
        "tenant": own,
        "scope": tenancy.current(),
        "tenants": tenants,
        "header": tenancy.HEADER,
    }


@router.get("")
async def list_tenants(db: AsyncSession = Depends(get_db), _: User = Depends(provider_admin)):
    counts = await _counts(db)
    rows = (await db.execute(select(Tenant).order_by(Tenant.name))).scalars().all()
    return {
        "enabled": tenancy.enabled(),
        "items": [tenancy.public(t, counts.get(t.id, {})) for t in rows],
        "unassigned": counts.get(None, {}),
    }


@router.get("/assignable")
async def assignable(db: AsyncSession = Depends(get_db), _: User = Depends(provider_admin)):
    """Alles Zuordenbare mit seinem Mandanten - fuer die Zuordnungs-Ansicht."""
    out = {}
    label = {
        "agents": lambda r: r.hostname, "snmp_devices": lambda r: r.name,
        "snmp_credentials": lambda r: r.name, "snmp_templates": lambda r: r.name,
        "pve_hosts": lambda r: r.name, "tokens": lambda r: r.name,
        "scripts": lambda r: r.name, "checks": lambda r: r.name, "policies": lambda r: r.name,
        "nas_devices": lambda r: r.name, "backup_targets": lambda r: r.name, "backup_jobs": lambda r: r.name,
    }
    for kind, model in ASSIGNABLE.items():
        q = select(model)
        if model is AgentToken:
            q = q.where(AgentToken.revoked_at.is_(None), AgentToken.kind != "global")
        if model is PatchPolicy:
            q = q.where(PatchPolicy.builtin.is_(False))
        rows = (await db.execute(q.order_by(model.id).limit(5000))).scalars().all()
        items = []
        for r in rows:
            item = {"id": r.id, "name": label[kind](r) or f"#{r.id}", "tenant_id": r.tenant_id}
            if kind == "agents":
                item.update({"site": r.site or "", "ip": r.ip_address or "", "rmm": bool(r.agent_uid)})
            if kind == "tokens":
                item.update({"kind": r.kind, "bound": r.agent_id is not None})
            items.append(item)
        out[kind] = items
    return out


# ------------------------------------------------------------------------------
# Ein / aus
# ------------------------------------------------------------------------------
@router.post("/enable")
async def enable(data: EnableIn, request: Request, db: AsyncSession = Depends(get_db),
                 admin: User = Depends(provider_admin)):
    """Mandantenfaehigkeit ein- oder ausschalten. Daten bleiben dabei, wie sie sind.

    Ausschalten blendet Verwaltung und Umschalter aus. Konten, die einem
    Mandanten gehoeren, bleiben trotzdem auf ihn beschraenkt - aus einem
    Kunden-Konto wird nie stillschweigend ein Betreiber-Konto.
    """
    await tenancy.store_state(db, data.enabled)
    await journal.record(
        db, category="settings", event="tenancy.enabled" if data.enabled else "tenancy.disabled",
        level="warning",
        message=f"Mandantenfähigkeit {'eingeschaltet' if data.enabled else 'ausgeschaltet'}.",
        actor=admin.username, source_ip=real_client_ip(request))
    return {"enabled": tenancy.enabled()}


# ------------------------------------------------------------------------------
# Anlegen, aendern, loeschen
# ------------------------------------------------------------------------------
@router.post("", status_code=201)
async def create_tenant(data: TenantIn, request: Request, db: AsyncSession = Depends(get_db),
                        admin: User = Depends(provider_admin)):
    if not tenancy.enabled():
        raise HTTPException(status_code=400,
                            detail="Die Mandantenfähigkeit ist ausgeschaltet - erst einschalten.")
    t = Tenant(name=data.name.strip(), slug=await _unique_slug(db, data.slug or data.name),
               is_active=data.is_active, contact_name=data.contact_name.strip(),
               contact_email=data.contact_email.strip(), note=data.note,
               alert_emails=data.alert_emails.strip())
    db.add(t)
    await db.flush()
    adopted = {}
    if data.adopt_unassigned:
        for kind in ADOPT:
            model = ASSIGNABLE[kind]
            q = select(model.id).where(model.tenant_id.is_(None))
            if model is AgentToken:
                q = q.where(AgentToken.kind != "global")
            ids = list((await db.execute(q)).scalars().all())
            adopted[kind] = await _move(db, kind, ids, t.id)
    await db.commit()
    await db.refresh(t)
    await journal.record(
        db, category="settings", event="tenant.created",
        message=f"Mandant '{t.name}' angelegt"
                + (f" und den bisherigen Bestand übernommen ({sum(adopted.values())} Einträge)." if adopted else "."),
        actor=admin.username, source_ip=real_client_ip(request), target=t.slug, detail={"adopted": adopted})
    return {**tenancy.public(t), "adopted": adopted}


@router.put("/{tenant_id}")
async def update_tenant(tenant_id: int, data: TenantUpdate, request: Request,
                        db: AsyncSession = Depends(get_db), admin: User = Depends(provider_admin)):
    t = await _get(db, tenant_id)
    changes = data.model_dump(exclude_unset=True)
    if "slug" in changes and changes["slug"]:
        t.slug = await _unique_slug(db, changes.pop("slug"), skip_id=t.id)
    changes.pop("slug", None)
    for key, value in changes.items():
        setattr(t, key, value.strip() if isinstance(value, str) and key != "note" else value)
    await db.commit()
    await db.refresh(t)
    await journal.record(
        db, category="settings", event="tenant.updated",
        message=f"Mandant '{t.name}' geändert" + ("" if t.is_active else " (gesperrt)") + ".",
        actor=admin.username, source_ip=real_client_ip(request), target=t.slug,
        detail={"changed": sorted(changes)})
    return tenancy.public(t)


@router.delete("/{tenant_id}")
async def delete_tenant(tenant_id: int, request: Request, move_to: int = 0, delete_users: bool = False,
                        db: AsyncSession = Depends(get_db), admin: User = Depends(provider_admin)):
    """Loescht einen Mandanten. Sein Bestand wandert zu `move_to` (0 = Betreiber).

    Konten des Mandanten werden nie zum Betreiber verschoben - das waere ein
    Konto, das ploetzlich alles sieht. Sie muessen vorher weg, oder sie werden
    mit `delete_users=true` geloescht.
    """
    t = await _get(db, tenant_id)
    target = None
    if move_to:
        if move_to == tenant_id:
            raise HTTPException(status_code=400, detail="Ziel und Mandant sind derselbe.")
        target = (await _get(db, move_to)).id
    users = (await db.execute(select(User).where(User.tenant_id == t.id))).scalars().all()
    if users and not delete_users:
        raise HTTPException(status_code=409,
                            detail=f"Der Mandant hat noch {len(users)} Benutzer. Erst entfernen "
                                   f"oder ausdrücklich mitlöschen.")
    for u in users:
        await db.delete(u)
    # Generalschluessel und Einladungen des Mandanten werden entwertet, nicht
    # weitergereicht: sonst meldete ein ehemaliger Kunde mit seinem alten
    # Schluessel kuenftig Geraete beim Betreiber (oder beim Ziel-Mandanten) an.
    # Geraeteschluessel wandern mit ihrem Geraet.
    revoked = 0
    for row in (await db.execute(select(AgentToken).where(
            AgentToken.tenant_id == t.id, AgentToken.kind.in_(("global", "enroll")),
            AgentToken.revoked_at.is_(None)))).scalars().all():
        tokens.revoke(row, reason=f"Mandant '{t.name}' gelöscht")
        revoked += 1
    moved = {}
    for kind, model in ASSIGNABLE.items():
        ids = list((await db.execute(select(model.id).where(model.tenant_id == t.id))).scalars().all())
        moved[kind] = await _move(db, kind, ids, target)
    await db.execute(update(Notification).where(Notification.tenant_id == t.id).values(tenant_id=target)
                     .execution_options(synchronize_session=False))
    name, slug = t.name, t.slug
    await db.delete(t)
    await db.commit()
    await journal.record(
        db, category="settings", event="tenant.deleted", level="warning",
        message=f"Mandant '{name}' gelöscht; Bestand an "
                + (f"Mandant #{target}" if target else "den Betreiber") + " übergeben"
                + (f", {len(users)} Benutzer gelöscht." if users else "."),
        actor=admin.username, source_ip=real_client_ip(request), target=slug, detail={"moved": moved})
    return {"deleted": True, "moved": moved, "users_deleted": len(users), "keys_revoked": revoked}


@router.post("/assign")
async def assign(data: AssignIn, request: Request, db: AsyncSession = Depends(get_db),
                 admin: User = Depends(provider_admin)):
    """Geraete, Schluessel, Proxmox-Hosts ... einem Mandanten zuordnen (0 = Betreiber)."""
    target = None
    if data.tenant_id:
        target = (await _get(db, data.tenant_id)).id
    unknown = [k for k in data.items if k not in ASSIGNABLE]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Nicht zuordenbar: {', '.join(unknown)}")
    if "tokens" in data.items:
        glob = (await db.execute(select(AgentToken.id).where(AgentToken.kind == "global",
                                                             AgentToken.id.in_(data.items["tokens"])))).first()
        if glob is not None:
            raise HTTPException(status_code=400,
                                detail="Der Generalschlüssel gehört immer dem Betreiber. Für Mandanten "
                                       "eine Einladung (Anmelde-Schlüssel) anlegen.")
    if target and "backup_jobs" in data.items:
        own = (await db.execute(select(BackupJob.id).where(BackupJob.kind == "rimperio",
                                                           BackupJob.id.in_(data.items["backup_jobs"])))).first()
        if own is not None:
            raise HTTPException(status_code=400,
                                detail="Die Sicherung von RIMPERIO selbst gehört immer dem Betreiber.")
    moved = {k: await _move(db, k, tenancy.ids(v), target) for k, v in data.items.items()}
    await db.commit()
    await journal.record(
        db, category="settings", event="tenant.assigned",
        message=f"{sum(moved.values())} Einträge " + (f"Mandant #{target}" if target else "dem Betreiber")
                + " zugeordnet.",
        actor=admin.username, source_ip=real_client_ip(request), detail={"moved": moved, "tenant_id": target})
    return {"moved": moved}
