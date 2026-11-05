"""
Datei:        events.py
Projekt:      LRMM
Paket:        backend.app
Autor:        Phydran6
Version:      2026.11.05.12.00.00

Beschreibung: Ausgehende Webhooks. Bei einem Ereignis (Alarm, Befehl fertig,
              Patches verfügbar) wird eine JSON-Nachricht an alle passenden,
              aktiven Webhooks geschickt — HMAC-SHA256-signiert, damit der
              Empfänger (z. B. n8n) die Echtheit prüfen kann. Versand läuft im
              Hintergrund und darf den Auslöser nie blockieren oder scheitern lassen.
"""
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from .database import SessionLocal
from .models import Webhook

# Bekannte Ereignisse (für die Oberfläche).
EVENT_TYPES = [
    "alert.raised",
    "alert.resolved",
    "command.finished",
    "patch.available",
]

# Laufende Sende-Tasks festhalten, damit sie nicht vorzeitig eingesammelt werden.
_tasks: set[asyncio.Task] = set()


async def dispatch(event_type: str, payload: dict) -> None:
    """Sucht passende Webhooks und stößt den Versand an (ohne zu warten)."""
    try:
        async with SessionLocal() as session:
            hooks = (
                await session.execute(select(Webhook).where(Webhook.enabled.is_(True)))
            ).scalars().all()
            targets = [h for h in hooks if (not h.events) or (event_type in h.events)]
            specs = [(h.id, h.url, h.secret) for h in targets]
    except Exception:
        return

    for hook_id, url, secret in specs:
        task = asyncio.create_task(_send(hook_id, url, secret, event_type, payload))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _send(hook_id: int, url: str, secret: str, event_type: str, payload: dict) -> None:
    body = json.dumps(
        {"event": event_type, "ts": datetime.now(timezone.utc).isoformat(), "data": payload},
        default=str,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-LRMM-Event": event_type}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-LRMM-Signature"] = "sha256=" + sig

    status: int | None = None
    err: str | None = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, content=body, headers=headers)
            status = resp.status_code
    except Exception as exc:  # Netzfehler, Timeout, falsche URL …
        err = str(exc)[:500]

    try:
        async with SessionLocal() as session:
            hook = await session.get(Webhook, hook_id)
            if hook is not None:
                hook.last_status = status
                hook.last_error = err
                hook.last_sent = datetime.now(timezone.utc)
                await session.commit()
    except Exception:
        pass
