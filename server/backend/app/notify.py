# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Eingang fuer Oberflaeche und App (Freigaben, Fehlschlaege)
# ==============================================================================
"""
Der Eingang: was jemand sehen oder entscheiden muss.

Jede Meldung landet in `notifications` - dort holt die App sie ab
(`/api/app/notifications?since_id=…`), die Oberflaeche zeigt sie an der Glocke.
Zusaetzlich geht sie ueber das allgemeine Meldesystem (alerts.py: Mail,
Telegram, Webhook/n8n), sofern die Ereignisart dort eingeschaltet ist.

Eine Meldung mit `action` (etwa "approve") wartet auf eine Entscheidung. Sie
wird `resolved`, sobald die Entscheidung gefallen ist - egal ob in der App, in
der Oberflaeche oder automatisch.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import alerts, tenancy
from .events import bus
from .models import Notification

logger = logging.getLogger("rimperio.notify")

NOTIFICATION_EVENT = "notification"


def public(n: Notification) -> dict:
    return {
        "id": n.id, "created_at": _iso(n.created_at), "category": n.category, "level": n.level,
        "title": n.title, "body": n.body, "link": n.link, "action": n.action,
        "data": n.data or {}, "read": n.read_at is not None,
        "resolved": n.resolved_at is not None,
    }


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


async def tenant_for(db: AsyncSession, data: Optional[dict]) -> Optional[int]:
    """Zu welchem Mandanten gehoert eine Meldung? Aus dem, worum es geht.

    Meldungen entstehen meist in Hintergrundaufgaben ohne Mandanten-Kontext
    (Patch-Lauf, SNMP-Abfrage). Damit sie trotzdem beim richtigen Kunden
    landen, wird der Mandant aus Geraet, Lauf oder DB-Update abgeleitet.
    """
    from .models import Agent, DbMigration, PatchRun, SnmpDevice
    data = data or {}
    opts = tenancy.ALL_TENANTS
    try:
        if data.get("agent_uid"):
            return (await db.execute(select(Agent.tenant_id).where(Agent.agent_uid == data["agent_uid"])
                                     .execution_options(**opts))).scalar_one_or_none()
        if data.get("run_id"):
            return (await db.execute(select(Agent.tenant_id).join(PatchRun, PatchRun.agent_id == Agent.id)
                                     .where(PatchRun.id == int(data["run_id"]))
                                     .execution_options(**opts))).scalar_one_or_none()
        if data.get("migration_id"):
            return (await db.execute(select(Agent.tenant_id).join(DbMigration, DbMigration.agent_id == Agent.id)
                                     .where(DbMigration.id == int(data["migration_id"]))
                                     .execution_options(**opts))).scalar_one_or_none()
        if data.get("device_id"):
            return (await db.execute(select(SnmpDevice.tenant_id).where(SnmpDevice.id == int(data["device_id"]))
                                     .execution_options(**opts))).scalar_one_or_none()
    except Exception as exc:                                        # defensiv
        logger.debug("Mandant der Meldung nicht ermittelt: %s", exc)
    return None


async def _mail_tenant(db: AsyncSession, tenant_id: Optional[int], subject: str, text: str) -> None:
    """Meldung zusaetzlich an die Adressen des Mandanten (Benutzer → Mandanten)."""
    if not tenant_id:
        return
    from .models import Tenant
    try:
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id)
                                   .execution_options(**tenancy.ALL_TENANTS))).scalar_one_or_none()
        recipients = tenancy.alert_recipients(tenant)
        if not recipients:
            return
        mail_config = alerts._current_mail()
        if not mail_config or mail_config.get("mode") == "off":
            return
        from . import mail
        await mail.send(mail_config, recipients, f"[{tenant.name}] {subject}", text)
    except Exception as exc:                                        # defensiv
        logger.warning("Meldung an den Mandanten nicht verschickt: %s", exc)


async def push(db: AsyncSession, *, category: str, title: str, body: str = "",
               level: str = "info", link: str = "", action: str = "",
               data: Optional[dict] = None, event: str = "", key: Optional[str] = None,
               commit: bool = True, tenant_id: Optional[int] = None) -> Notification:
    """Legt eine Meldung an und schickt sie - wenn gewuenscht - nach aussen."""
    if tenant_id is None:
        tenant_id = tenancy.current() or None
    if tenant_id is None:
        tenant_id = await tenant_for(db, data)
    n = Notification(category=category, title=title[:255], body=body, level=level,
                     link=link, action=action, data=data or {}, tenant_id=tenant_id)
    db.add(n)
    if commit:
        await db.commit()
        await db.refresh(n)
    else:
        await db.flush()
    try:
        bus.publish(NOTIFICATION_EVENT, {"id": n.id, "title": n.title, "level": level,
                                         "category": category}, sticky=False)
    except Exception:                                               # defensiv
        pass
    if event:
        text = body if not link else f"{body}\n\nIn RIMPERIO: {link}"
        await _mail_tenant(db, tenant_id, title, text)
        await alerts.dispatch(event, f"RIMPERIO: {title}", text,
                              level="critical" if level == "critical" else
                              ("warning" if level == "warning" else "info"),
                              key=key, detail={"category": category, "action": action,
                                               "notification_id": n.id, **(data or {})})
    return n


async def resolve(db: AsyncSession, *, action: str, match: dict, commit: bool = True) -> int:
    """Schliesst offene Meldungen mit dieser Aktion, deren data alle Werte aus `match` traegt."""
    rows = (await db.execute(select(Notification).where(
        Notification.action == action, Notification.resolved_at.is_(None)))).scalars().all()
    done = 0
    for n in rows:
        data = n.data or {}
        if all(data.get(k) == v for k, v in match.items()):
            n.resolved_at = datetime.utcnow()
            done += 1
    if commit and done:
        await db.commit()
    return done


async def mark_read(db: AsyncSession, ids: Optional[list] = None) -> int:
    q = update(Notification).where(Notification.read_at.is_(None))
    if ids:
        q = q.where(Notification.id.in_(ids))
    result = await db.execute(q.values(read_at=datetime.utcnow()))
    await db.commit()
    return result.rowcount or 0
