"""
Datei:        routes/integrations.py
Projekt:      LRMM
Paket:        backend.app.routes
Autor:        Phydran6
Version:      2026.11.05.12.00.00

Beschreibung: Anbindung nach außen. Ausgehende Webhooks (z. B. an n8n) verwalten
              und testen; langlebige Integrations-Token ausstellen, mit denen
              n8n oder eine KI die API im Namen einer Rolle ansprechen. Alles
              nur für Admins.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events
from ..database import get_session
from ..models import IntegrationToken, User, Webhook
from ..schemas import (
    IntegrationTokenCreated,
    IntegrationTokenIn,
    IntegrationTokenOut,
    WebhookIn,
    WebhookOut,
)
from ..security import generate_agent_token, require_role

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

ROLES = {"admin", "operator", "readonly"}


@router.get("/events")
async def event_types(_: User = Depends(require_role("admin"))) -> list[str]:
    return events.EVENT_TYPES


# --- Webhooks --------------------------------------------------------------
@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookOut]:
    rows = (await session.execute(select(Webhook).order_by(Webhook.name))).scalars().all()
    return [WebhookOut.model_validate(w) for w in rows]


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(
    data: WebhookIn,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> WebhookOut:
    bad = [e for e in data.events if e not in events.EVENT_TYPES]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unbekannte Ereignisse: {bad}")
    hook = Webhook(**data.model_dump())
    session.add(hook)
    await session.commit()
    await session.refresh(hook)
    return WebhookOut.model_validate(hook)


@router.put("/webhooks/{hook_id}", response_model=WebhookOut)
async def update_webhook(
    hook_id: int,
    data: WebhookIn,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> WebhookOut:
    hook = await session.get(Webhook, hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden.")
    for key, value in data.model_dump().items():
        setattr(hook, key, value)
    await session.commit()
    await session.refresh(hook)
    return WebhookOut.model_validate(hook)


@router.delete("/webhooks/{hook_id}")
async def delete_webhook(
    hook_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    hook = await session.get(Webhook, hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden.")
    await session.delete(hook)
    await session.commit()
    return {"status": "ok"}


@router.post("/webhooks/{hook_id}/test", response_model=WebhookOut)
async def test_webhook(
    hook_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> WebhookOut:
    hook = await session.get(Webhook, hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden.")
    # Direkt und abwartend senden, damit die Oberfläche das Ergebnis sieht.
    await events._send(hook.id, hook.url, hook.secret, "test", {"message": "LRMM-Testnachricht"})
    await session.refresh(hook)
    return WebhookOut.model_validate(hook)


# --- Integrations-Token ----------------------------------------------------
@router.get("/tokens", response_model=list[IntegrationTokenOut])
async def list_tokens(
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[IntegrationTokenOut]:
    rows = (await session.execute(select(IntegrationToken).order_by(IntegrationToken.name))).scalars().all()
    return [IntegrationTokenOut.model_validate(t) for t in rows]


@router.post("/tokens", response_model=IntegrationTokenCreated, status_code=201)
async def create_token(
    data: IntegrationTokenIn,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> IntegrationTokenCreated:
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Rolle muss eine von {sorted(ROLES)} sein.")
    token, token_hash = generate_agent_token()
    row = IntegrationToken(name=data.name, token_hash=token_hash, role=data.role)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    out = IntegrationTokenCreated.model_validate(row)
    out.token = token  # nur jetzt sichtbar
    return out


@router.delete("/tokens/{token_id}")
async def delete_token(
    token_id: int,
    _: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(IntegrationToken, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Token nicht gefunden.")
    await session.delete(row)
    await session.commit()
    return {"status": "ok"}
