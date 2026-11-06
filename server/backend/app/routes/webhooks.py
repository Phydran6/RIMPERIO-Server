# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.13.20.58.33
# Beschreibung: RIMPERIO v2026.01.30.13.30.00 - Webhooks API Endpoints
# ==============================================================================

import secrets
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models import Webhook, User
from ..schemas import WebhookCreate, WebhookUpdate, WebhookResponse
from ..auth import get_current_user, get_current_admin

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])

@router.get("", response_model=List[WebhookResponse])
async def list_webhooks(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Webhook).order_by(Webhook.created_at.desc()))
    return result.scalars().all()

@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(webhook_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    return webhook

# ==============================================================================
# Anlegen und aendern: nur Administratoren
# ==============================================================================
# Warum das enger ist als frueher: Ein Webhook ist ein Endpunkt, der OHNE
# Anmeldung Logdaten herausgibt - der Token steht in der Adresse, mehr braucht
# es nicht. Wer einen anlegen darf, kann sich also einen dauerhaften, offenen
# Abgriff auf den Logbestand bauen und ihn weitergeben.
#
# Vorher genuegte dafuer ein beliebiges angemeldetes Konto. Ein Benutzer mit
# reinen Leserechten konnte sich damit einen Zugang schaffen, der auch nach dem
# Sperren seines Kontos weiterlief. Lesen darf die Liste weiterhin jeder
# Angemeldete; anlegen, aendern, neu wuerfeln und loeschen nur Administratoren.
@router.post("", response_model=WebhookResponse, status_code=201)
async def create_webhook(data: WebhookCreate, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    webhook = Webhook(
        name=data.name, description=data.description, token=secrets.token_hex(32),
        filters=data.filters.model_dump() if data.filters else {},
        max_results=data.max_results, include_raw=data.include_raw, is_active=data.is_active
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook

@router.put("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(webhook_id: int, data: WebhookUpdate, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    if data.name is not None: webhook.name = data.name
    if data.description is not None: webhook.description = data.description
    if data.filters is not None: webhook.filters = data.filters.model_dump()
    if data.max_results is not None: webhook.max_results = data.max_results
    if data.include_raw is not None: webhook.include_raw = data.include_raw
    if data.is_active is not None: webhook.is_active = data.is_active
    await db.commit()
    await db.refresh(webhook)
    return webhook

@router.post("/{webhook_id}/regenerate-token", response_model=WebhookResponse)
async def regenerate_token(webhook_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    webhook.token = secrets.token_hex(32)
    await db.commit()
    await db.refresh(webhook)
    return webhook

@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    await db.delete(webhook)
    await db.commit()
