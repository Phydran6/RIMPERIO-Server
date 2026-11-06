# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.13.20.58.33
# Beschreibung: RIMPERIO v2026.04.11.13.38.42 - Agents API Endpoints
# ==============================================================================

from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models import Agent, AgentToken, Log, User, Setting
from ..schemas import (AgentResponse, AgentListResponse, AgentDecommissionRequest,
                       AgentRetentionUpdate)
from ..auth import get_current_user, get_current_admin
from ..limiter import limiter, client_ip as real_client_ip
from .. import journal, tokens

router = APIRouter(prefix="/api/agents", tags=["Agents"])

@router.get("", response_model=AgentListResponse)
async def list_agents(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    device_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user)
):
    query = select(Agent)
    count_query = select(func.count(Agent.id))
    
    if search:
        f = f"%{search}%"
        query = query.where((Agent.hostname.ilike(f)) | (Agent.ip_address.ilike(f)) | (Agent.mac_address.ilike(f)))
        count_query = count_query.where((Agent.hostname.ilike(f)) | (Agent.ip_address.ilike(f)) | (Agent.mac_address.ilike(f)))
    if device_type:
        query = query.where(Agent.device_type == device_type)
        count_query = count_query.where(Agent.device_type == device_type)
    
    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.order_by(Agent.last_seen.desc()).offset(offset).limit(page_size))

    # Offline-Timeout aus Settings (Fallback 300s)
    offline_timeout = 300
    setting_row = await db.execute(select(Setting).where(Setting.key == "agent_offline_timeout"))
    s = setting_row.scalar_one_or_none()
    if s:
        try:
            offline_timeout = int(s.value)
        except Exception:
            pass
    cutoff = datetime.utcnow() - timedelta(seconds=offline_timeout)

    items = []
    for agent in result.scalars().all():
        is_online = bool(agent.last_seen and agent.last_seen >= cutoff)
        items.append(AgentResponse(
            id=agent.id,
            hostname=agent.hostname,
            ip_address=agent.ip_address,
            mac_address=agent.mac_address,
            device_type=agent.device_type,
            last_seen=agent.last_seen,
            first_seen=agent.first_seen,
            extra_data=getattr(agent, "extra_data", {}) or {},
            metadata=getattr(agent, "extra_data", {}) or {},
            is_online=is_online,
            agent_uid=agent.agent_uid,
        ))

    return AgentListResponse(items=items, total=total, page=page, page_size=page_size)

@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    log_count = (await db.execute(select(func.count(Log.id)).where(Log.agent_id == agent_id))).scalar() or 0

    offline_timeout = 300
    setting_row = await db.execute(select(Setting).where(Setting.key == "agent_offline_timeout"))
    s = setting_row.scalar_one_or_none()
    if s:
        try:
            offline_timeout = int(s.value)
        except Exception:
            pass
    cutoff = datetime.utcnow() - timedelta(seconds=offline_timeout)
    is_online = bool(agent.last_seen and agent.last_seen >= cutoff)

    return AgentResponse(
        id=agent.id, hostname=agent.hostname, ip_address=agent.ip_address,
        mac_address=agent.mac_address, device_type=agent.device_type,
        last_seen=agent.last_seen, first_seen=agent.first_seen,
        extra_data=getattr(agent, "extra_data", {}) or {},
        metadata=getattr(agent, "extra_data", {}) or {},
        is_online=is_online,
        log_count=log_count,
        retention_max_logs=agent.retention_max_logs,
        retention_days=agent.retention_days,
    )


@router.put("/{agent_id}/retention")
async def set_agent_retention(
    agent_id: int,
    data: AgentRetentionUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Setzt die Aufbewahrung für ein einzelnes Gerät.

    Nur Administratoren: hier wird festgelegt, ab wann Logzeilen verschwinden —
    das ist keine Anzeigeeinstellung, das ist Datenverlust auf Ansage.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    agent.retention_max_logs = data.retention_max_logs
    agent.retention_days = data.retention_days
    await db.commit()
    return {
        "agent_id": agent_id,
        "retention_max_logs": agent.retention_max_logs,
        "retention_days": agent.retention_days,
    }


@router.post("/{agent_id}/retention/execute")
async def execute_agent_retention(
    agent_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Führt die Aufbewahrung für ein Gerät sofort aus (löscht Logzeilen)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    deleted = 0
    if agent.retention_days:
        cutoff = datetime.utcnow() - timedelta(days=agent.retention_days)
        r = await db.execute(delete(Log).where(Log.agent_id == agent_id, Log.timestamp < cutoff))
        deleted += r.rowcount or 0

    if agent.retention_max_logs:
        count = (await db.execute(
            select(func.count(Log.id)).where(Log.agent_id == agent_id)
        )).scalar() or 0
        excess = count - agent.retention_max_logs
        if excess > 0:
            subq = (
                select(Log.id)
                .where(Log.agent_id == agent_id)
                .order_by(Log.timestamp.asc())
                .limit(excess)
                .scalar_subquery()
            )
            r = await db.execute(delete(Log).where(Log.id.in_(subq)))
            deleted += r.rowcount or 0

    await db.commit()
    if deleted:
        await journal.record(
            db, category="retention", event="agent.retention.manual", level="notice",
            message=f"{deleted} Logzeilen von '{agent.hostname}' von Hand entfernt.",
            actor=admin.username, source_ip=real_client_ip(request), target=agent.hostname,
            detail={"agent_id": agent_id, "deleted": deleted})
    return {"agent_id": agent_id, "deleted_count": deleted}


@router.post("/decommission", status_code=status.HTTP_200_OK)
@limiter.limit("30/minute")
async def decommission_agent(
    payload: AgentDecommissionRequest,
    request: Request,
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """Wird vom Agent beim vollständigen Entfernen aufgerufen."""
    source_ip = real_client_ip(request)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer Token erforderlich")

    # Neu: ein GERAETESCHLUESSEL darf nur sein eigenes Geraet abmelden. Vorher
    # konnte jeder gueltige Schluessel jedes beliebige Geraet samt Logs
    # loeschen - auf einem Netz mit hundert Agents war das eine Einladung.
    # Der Generalschluessel darf weiterhin alles; er ist der des Administrators.
    agent_token, deny_reason = await tokens.resolve(db, authorization[7:], source_ip)
    if not agent_token:
        await journal.record(
            db, category="agent", event="decommission.rejected", level="warning",
            message=f"Abmeldung abgelehnt: {deny_reason}",
            actor="agent", source_ip=source_ip, target=payload.hostname or "", ok=False)
        raise HTTPException(status_code=401, detail="Ungültiger Agent-Token")

    # Zuordnung, vom Genauesten zum Ungenauesten. `matched_by` wandert in die
    # Antwort - der Agent kann dann sagen, ob wirklich sein eigener Eintrag
    # getroffen wurde oder nur einer, der zufaellig so heisst.
    agents: List[Agent] = []
    matched_by = ""

    if payload.agent_id:
        hit = (await db.execute(select(Agent).where(Agent.id == payload.agent_id))).scalar_one_or_none()
        if hit:
            agents, matched_by = [hit], "agent_id"

    if not agents and payload.mac_address:
        hit = (await db.execute(
            select(Agent).where(Agent.mac_address == payload.mac_address)
        )).scalar_one_or_none()
        if hit:
            agents, matched_by = [hit], "mac_address"

    if not agents and payload.hostname and payload.ip_address:
        hit = (await db.execute(
            select(Agent).where(Agent.hostname == payload.hostname,
                                Agent.ip_address == payload.ip_address)
        )).scalar_one_or_none()
        if hit:
            agents, matched_by = [hit], "hostname+ip"

    if not agents and payload.hostname:
        # Ein Rechner, der die IP gewechselt hat (DHCP, NAT, Umzug), steht hier
        # mehrfach. Beim Abraeumen sollen alle mitgehen - sonst bleiben
        # Karteileichen mit ihren Logs liegen.
        rows = (await db.execute(
            select(Agent).where(Agent.hostname == payload.hostname)
        )).scalars().all()
        if rows:
            agents = list(rows) if payload.all_for_hostname else [rows[0]]
            matched_by = "hostname"

    if not agents:
        raise HTTPException(
            status_code=404,
            detail=("Zu dieser Angabe gibt es auf dem Server kein Geraet. "
                    "Moeglicherweise wurde es schon entfernt."))

    # Beim Deinstallieren soll wirklich alles weg sein, was zu diesem Rechner
    # gehoert. Ein Treffer ueber die Geraetenummer ist zwar genau - er findet
    # aber nur EINEN Eintrag. Hat der Rechner zwischendurch die IP gewechselt,
    # steht er mehrfach in der Liste, und der Rest bliebe als Karteileiche mit
    # seinen Logs zurueck. Also: gefundene Eintraege um die gleichnamigen ergaenzen.
    if payload.all_for_hostname and payload.hostname:
        known = {a.id for a in agents}
        extra = (await db.execute(
            select(Agent).where(Agent.hostname == payload.hostname)
        )).scalars().all()
        for candidate in extra:
            if candidate.id not in known:
                agents.append(candidate)
                known.add(candidate.id)

    ids = [a.id for a in agents]

    # Darf dieser Schluessel diese Geraete ueberhaupt anfassen? Ein
    # Geraeteschluessel darf genau eines: sich selbst abmelden.
    for agent in agents:
        allowed, why = tokens.may_decommission(agent_token, agent.id)
        if not allowed:
            await journal.record(
                db, category="agent", event="decommission.denied", level="warning",
                message=f"Abmeldung von '{agent.hostname}' verweigert: {why}",
                actor=agent_token.name, source_ip=source_ip, target=agent.hostname, ok=False)
            raise HTTPException(status_code=403, detail=why)

    # Der Schluessel des Geraets wird beim Abmelden entwertet. Ein
    # deinstallierter Agent laesst keinen gueltigen Schluessel zurueck.
    revoked = 0
    for agent in agents:
        for stale in (await db.execute(
            select(AgentToken).where(AgentToken.kind == "agent",
                                     AgentToken.agent_id == agent.id,
                                     AgentToken.revoked_at.is_(None))
        )).scalars().all():
            tokens.revoke(stale, reason=f"{agent.hostname} wurde abgemeldet")
            revoked += 1
    tokens.note_use(agent_token, source_ip)

    if payload.purge:
        deleted_logs = 0
        hostnames = [a.hostname for a in agents]
        for agent in agents:
            result = await db.execute(delete(Log).where(Log.agent_id == agent.id))
            deleted_logs += result.rowcount or 0
            await db.delete(agent)
        await db.commit()
        await journal.record(
            db, category="agent", event="decommission.purged", level="warning",
            message=(f"{len(ids)} Geraet(e) restlos entfernt ({', '.join(hostnames)}), "
                     f"{deleted_logs} Logzeilen geloescht."),
            actor=agent_token.name, source_ip=source_ip,
            target=", ".join(hostnames)[:200],
            detail={"agent_ids": ids, "deleted_logs": deleted_logs,
                    "revoked_tokens": revoked, "matched_by": matched_by})
        return {"status": "purged", "agent_id": ids[0], "agent_ids": ids,
                "deleted_agents": len(ids), "deleted_logs": deleted_logs,
                "revoked_tokens": revoked,
                "matched_by": matched_by,
                "message": (f"{len(ids)} Geraet(e) und {deleted_logs} Logzeilen geloescht.")}

    # Nur abmelden: Eintrag und Logs bleiben, das Geraet gilt aber als stillgelegt.
    for agent in agents:
        # Kopie: dasselbe Objekt geaendert und wieder zugewiesen erkennt
        # SQLAlchemy nicht als Aenderung - die Stilllegung wurde nie gespeichert.
        extra = dict(getattr(agent, "extra_data", {}) or {})
        extra.update({
            "decommissioned": True,
            "decommissioned_at": datetime.utcnow().isoformat(),
            "decommissioned_token": agent_token.name,
            "decommissioned_token_type": agent_token.device_type,
        })
        agent.extra_data = extra
        agent.last_seen = None
    await db.commit()
    await journal.record(
        db, category="agent", event="decommission.ok",
        message=(f"{len(ids)} Geraet(e) stillgelegt. Logs und Eintrag bleiben erhalten."),
        actor=agent_token.name, source_ip=source_ip,
        target=", ".join(a.hostname for a in agents)[:200],
        detail={"agent_ids": ids, "revoked_tokens": revoked, "matched_by": matched_by})
    return {"status": "decommissioned", "agent_id": ids[0], "agent_ids": ids,
            "matched_by": matched_by, "revoked_tokens": revoked,
            "message": (f"{len(ids)} Geraet(e) stillgelegt. Logs und Eintrag bleiben "
                        f"erhalten.")}


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: int,
    request: Request,
    purge_logs: bool = Query(False, description="Logs des Agents mit loeschen"),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Entfernt ein Geraet. Nur Administratoren - es ist nicht umkehrbar."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    hostname = agent.hostname
    deleted_logs = 0
    if purge_logs:
        outcome = await db.execute(delete(Log).where(Log.agent_id == agent_id))
        deleted_logs = outcome.rowcount or 0

    await db.delete(agent)
    await db.commit()
    await journal.record(
        db, category="agent", event="agent.deleted", level="warning",
        message=(f"Geraet '{hostname}' geloescht"
                 + (f", dazu {deleted_logs} Logzeilen." if purge_logs else ".")),
        actor=admin.username, source_ip=real_client_ip(request), target=hostname,
        detail={"agent_id": agent_id, "deleted_logs": deleted_logs})

# ==============================================================================
# Schluesselverwaltung
# ==============================================================================
"""
Die Endpunkte zur Schluesselverwaltung.

**Zwei Dinge haben sich gegenueber frueher geaendert, und beide sind wichtig.**

1. *Nur noch Administratoren.* Vorher reichte ein beliebiges angemeldetes
   Konto, um alle Agent-Schluessel im Klartext zu lesen, neue anzulegen und
   vorhandene zu loeschen. Ein Benutzer mit reinen Leserechten auf die Logs
   konnte sich damit den Generalschluessel besorgen. Das war eine Luecke, und
   sie ist zu.
2. *Der Schluessel wird genau einmal angezeigt.* Danach gibt es ihn nirgends
   mehr zu lesen - er liegt nur noch als Pruefsumme in der Datenbank. Wer ihn
   verlegt, erneuert ihn; das ist eine Sache von zwei Klicks und besser als ein
   Schluesselbund, den jeder Datenbankabzug mitnimmt.
"""

token_router = APIRouter(prefix="/api/agent-tokens", tags=["Agent Tokens"])


class TokenCreateRequest(BaseModel):
    """Ein neuer Schluessel."""
    name: str = Field(..., min_length=1, max_length=100)
    kind: str = Field(default="agent", pattern="^(global|agent|enroll)$")
    device_type: Optional[str] = Field(default=None, pattern="^(linux|windows)$")
    agent_id: Optional[int] = None
    allowed_cidrs: str = Field(default="", max_length=500,
                               description="Absenderbereiche, z.B. '10.0.0.0/8'. Leer = ueberall.")
    expires_in_hours: Optional[int] = Field(default=None, ge=1, le=24 * 365 * 5)
    max_uses: Optional[int] = Field(default=None, ge=1, le=10000)
    note: str = Field(default="", max_length=500)


class TokenUpdateRequest(BaseModel):
    """Was sich nachtraeglich noch aendern laesst - der Schluessel selbst nie."""
    name: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None
    allowed_cidrs: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=500)


class EnrollRequest(BaseModel):
    """Ein Agent meldet sich an und bittet um einen eigenen Schluessel."""
    hostname: str = Field(..., min_length=1, max_length=255)
    device_type: Optional[str] = Field(default=None, pattern="^[a-z0-9_-]{1,50}$")
    ip_address: Optional[str] = Field(default=None, max_length=45)
    mac_address: Optional[str] = Field(default=None, max_length=17)


@token_router.get("")
async def list_agent_tokens(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Alle Schlüssel — ohne die Schlüssel selbst."""
    return {
        "items": await tokens.list_all(db),
        "note": ("Ein Schlüssel wird genau einmal angezeigt: direkt nach dem Anlegen. "
                 "Danach steht er nur noch als Prüfsumme in der Datenbank und lässt "
                 "sich nicht mehr auslesen — auch von hier nicht."),
    }


@token_router.post("", status_code=201)
async def create_agent_token(data: TokenCreateRequest, request: Request,
                             db: AsyncSession = Depends(get_db),
                             admin=Depends(get_current_admin)):
    """Legt einen Schlüssel an und zeigt ihn **einmalig** an."""
    if data.kind == "global":
        existing = (await db.execute(
            select(AgentToken).where(AgentToken.kind == "global",
                                     AgentToken.revoked_at.is_(None))
        )).scalars().first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=("Es gibt bereits einen Generalschlüssel. Absichtlich nur einer — "
                        "zwei davon heißt, dass niemand mehr weiß, welcher wo liegt. "
                        "Zum Austauschen: 'Neu würfeln'."))

    try:
        row, plain = await tokens.create(
            db, name=data.name, kind=data.kind, device_type=data.device_type,
            agent_id=data.agent_id, allowed_cidrs=data.allowed_cidrs,
            expires_in_hours=(data.expires_in_hours
                              or (tokens.DEFAULT_ENROLL_HOURS if data.kind == "enroll" else None)),
            max_uses=(data.max_uses
                      or (tokens.DEFAULT_ENROLL_USES if data.kind == "enroll" else None)),
            created_by=admin.username, note=data.note)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await journal.record(
        db, category="token", event="token.created", level="warning",
        message=f"Schlüssel '{row.name}' ({data.kind}) angelegt.",
        actor=admin.username, source_ip=real_client_ip(request), target=row.name,
        detail={"kind": data.kind, "prefix": row.prefix})

    return {
        **tokens.public(row),
        "token": plain,
        "shown_once": True,
        "warning": "Jetzt notieren oder eintragen — danach ist der Schlüssel nicht mehr lesbar.",
    }


@token_router.put("/{token_id}")
async def update_agent_token(token_id: int, data: TokenUpdateRequest, request: Request,
                             db: AsyncSession = Depends(get_db),
                             admin=Depends(get_current_admin)):
    """Name, Zustand, Absenderbereich und Vermerk ändern."""
    row = (await db.execute(select(AgentToken).where(AgentToken.id == token_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Diesen Schlüssel gibt es nicht.")

    if data.name is not None:
        row.name = data.name.strip()[:100] or row.name
    if data.is_active is not None:
        row.is_active = data.is_active
    if data.allowed_cidrs is not None:
        row.allowed_cidrs = data.allowed_cidrs.strip() or None
    if data.note is not None:
        row.note = data.note.strip() or None
    await db.commit()

    await journal.record(db, category="token", event="token.updated",
                         message=f"Schlüssel '{row.name}' geändert.",
                         actor=admin.username, source_ip=real_client_ip(request),
                         target=row.name)
    return tokens.public(row)


@token_router.post("/{token_id}/regenerate")
async def regenerate_agent_token(token_id: int, request: Request,
                                 db: AsyncSession = Depends(get_db),
                                 admin=Depends(get_current_admin)):
    """Würfelt den Schlüssel neu. Der alte gilt ab diesem Moment nicht mehr."""
    row = (await db.execute(select(AgentToken).where(AgentToken.id == token_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Diesen Schlüssel gibt es nicht.")

    plain = await tokens.rotate(db, row, created_by=admin.username)
    await db.commit()

    await journal.record(
        db, category="token", event="token.rotated", level="warning",
        message=(f"Schlüssel '{row.name}' neu gewürfelt. Jedes Gerät, das den alten "
                 f"benutzt, kommt ab sofort nicht mehr durch."),
        actor=admin.username, source_ip=real_client_ip(request), target=row.name)

    return {
        **tokens.public(row),
        "token": plain,
        "shown_once": True,
        "warning": ("Jetzt eintragen. Geräte mit dem alten Schlüssel liefern ab sofort "
                    "nichts mehr."),
    }


@token_router.post("/harden")
async def harden_legacy_tokens(request: Request, db: AsyncSession = Depends(get_db),
                               admin=Depends(get_current_admin)):
    """Überführt Alt-Schlüssel in den geschützten Speicher.

    Schlüssel aus früheren Fassungen liegen noch im Klartext in der Datenbank.
    Dieser Aufruf trägt ihre Prüfsumme nach und löscht den Klartext. Die
    Schlüssel selbst bleiben gültig — auf den Geräten ändert sich nichts. Was
    verloren geht, ist nur die Möglichkeit, sie hier noch einmal abzulesen.
    """
    rows = (await db.execute(
        select(AgentToken).where(AgentToken.token.is_not(None))
    )).scalars().all()

    hardened = []
    for row in rows:
        if not row.token_hash:
            row.token_hash = tokens.digest(row.token)
        if not row.prefix:
            row.prefix = row.token[:10]
        if row.name == tokens.GLOBAL_TOKEN_NAME:
            row.kind = "global"
        elif not row.kind:
            row.kind = "agent"
        row.token = None
        hardened.append(row.name)
    await db.commit()

    if hardened:
        await journal.record(
            db, category="token", event="token.hardened", level="warning",
            message=f"{len(hardened)} Alt-Schlüssel in den geschützten Speicher überführt.",
            actor=admin.username, source_ip=real_client_ip(request),
            detail={"names": hardened})

    return {
        "hardened": len(hardened),
        "names": hardened,
        "message": (f"{len(hardened)} Schlüssel überführt. Sie gelten unverändert weiter, "
                    f"sind hier aber nicht mehr lesbar."
                    if hardened else "Es lag nichts mehr im Klartext."),
    }


@token_router.delete("/{token_id}", status_code=204)
async def delete_agent_token(token_id: int, request: Request,
                             db: AsyncSession = Depends(get_db),
                             admin=Depends(get_current_admin)):
    row = (await db.execute(select(AgentToken).where(AgentToken.id == token_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Diesen Schlüssel gibt es nicht.")
    if (row.kind or "").lower() == "global":
        raise HTTPException(
            status_code=409,
            detail=("Der Generalschlüssel lässt sich nicht löschen — ohne ihn kommt kein "
                    "Agent mehr durch, und ein neuer wäre beim nächsten Start ohnehin da. "
                    "Zum Austauschen: 'Neu würfeln'."))

    name = row.name
    await db.delete(row)
    await db.commit()
    await journal.record(db, category="token", event="token.deleted", level="warning",
                         message=f"Schlüssel '{name}' gelöscht.",
                         actor=admin.username, source_ip=real_client_ip(request), target=name)


# ==============================================================================
# Anmelden: ein Agent holt sich seinen eigenen Schluessel
# ==============================================================================
@router.post("/enroll", status_code=201)
@limiter.limit("30/minute")
async def enroll_agent(data: EnrollRequest, request: Request,
                       authorization: str = Header(...),
                       db: AsyncSession = Depends(get_db)):
    """Ein Agent tauscht eine Einladung (oder den Generalschlüssel) gegen seinen eigenen.

    Damit muss der Generalschlüssel nicht mehr auf jeden Rechner kopiert werden.
    Der Rechner bekommt einen Schlüssel, der nur für ihn gilt; geht das Gerät
    verloren, entwertet man genau diesen einen.

    Wird zweimal für denselben Hostnamen aufgerufen (Neuinstallation), entsteht
    ein neuer Schlüssel und der vorherige dieses Geräts wird zurückgezogen —
    sonst sammelt sich mit der Zeit ein Schlüsselbund an, den keiner mehr kennt.
    """
    source_ip = real_client_ip(request)
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer Token erforderlich")

    presenter, reason = await tokens.resolve(db, authorization[7:], source_ip)
    if not presenter or not tokens.may_enroll(presenter):
        await journal.record(
            db, category="token", event="enroll.rejected", level="warning",
            message=f"Anmeldung von {data.hostname} abgelehnt: {reason or 'nicht berechtigt'}",
            actor="agent", source_ip=source_ip, target=data.hostname, ok=False)
        raise HTTPException(status_code=401, detail="Ungültiger oder nicht berechtigter Schlüssel")

    hostname = data.hostname.strip()

    # Gibt es das Geraet schon? Dann bindet der neue Schluessel daran.
    agent = (await db.execute(
        select(Agent).where(Agent.hostname == hostname)
        .order_by(Agent.last_seen.desc().nullslast())
    )).scalars().first()

    # Alten Schluessel dieses Geraets zurueckziehen.
    replaced = 0
    if agent is not None:
        previous = (await db.execute(
            select(AgentToken).where(AgentToken.kind == "agent",
                                     AgentToken.agent_id == agent.id,
                                     AgentToken.revoked_at.is_(None))
        )).scalars().all()
        for old in previous:
            tokens.revoke(old, reason=f"Ersetzt bei Neuanmeldung von {hostname}")
            replaced += 1

    row, plain = await tokens.create(
        db,
        name=f"agent:{hostname}"[:100],
        kind="agent",
        device_type=data.device_type,
        agent_id=agent.id if agent else None,
        created_by=f"enroll:{presenter.name}"[:50],
        note=f"Automatisch beim Anmelden von {hostname} erzeugt.")

    tokens.note_use(presenter, source_ip)
    await db.commit()

    await journal.record(
        db, category="token", event="enroll.ok",
        message=(f"{hostname} hat einen eigenen Schlüssel bekommen"
                 + (f" (ersetzt {replaced} alten)." if replaced else ".")),
        actor=f"enroll:{presenter.name}", source_ip=source_ip, target=hostname,
        detail={"prefix": row.prefix, "replaced": replaced})

    return {
        "token": plain,
        "name": row.name,
        "prefix": row.prefix,
        "agent_id": agent.id if agent else None,
        "replaced_previous": replaced,
        "message": ("Dieser Schlüssel gilt nur für dieses Gerät. Er wird nur jetzt "
                    "ausgegeben."),
    }
