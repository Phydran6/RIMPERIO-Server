# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer das Log-Handling (Regeln, Rauschen,
#               Aufbewahrung nach Stufen, Kategorien, KI-Erklaerung)
# ==============================================================================
"""
Alles rund um "was wird ueberhaupt gespeichert und wie lange":

    GET    /api/log-handling/overview          Fristen, Dubletten, Regeln, Verteilung
    PUT    /api/log-handling/tiers             Aufbewahrung je Stufe        (Admin)
    PUT    /api/log-handling/dedup             Dubletten-Fenster            (Admin)
    GET    /api/log-handling/noise             Rausch-Erkennung
    GET    /api/log-handling/categories        Juengste Zeilen nach Themen
    GET    /api/log-handling/category/{key}    Zeilen eines Themas
    GET    /api/log-handling/rules             Sperrregeln
    POST   /api/log-handling/rules             neue Regel                   (Admin)
    POST   /api/log-handling/rules/preview     wie viel wuerde sie treffen?
    PUT    /api/log-handling/rules/{id}        aendern                      (Admin)
    DELETE /api/log-handling/rules/{id}        loeschen                     (Admin)
    POST   /api/log-handling/rules/{id}/purge  vorhandene Zeilen loeschen   (Admin)
    POST   /api/log-handling/explain/{log_id}  KI erklaert eine Zeile
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ai, journal, logexplain, loghandling, logparse
from ..auth import get_current_admin, get_current_user
from ..database import get_db
from ..models import Log, LogRule

logger = logging.getLogger("rimperio.routes.loghandling")

router = APIRouter(prefix="/api/log-handling", tags=["Log-Handling"])


class RuleIn(BaseModel):
    name: str = ""
    enabled: bool = True
    action: str = Field(default="drop", pattern="^(drop|downgrade)$")
    hostname: str = ""
    source: str = ""
    levels: List[str] = Field(default_factory=list)
    match: str = Field(default="contains", pattern="^(contains|regex|template)$")
    pattern: str
    sample: str = ""
    note: str = ""
    purge_existing: bool = False


class FromLogIn(BaseModel):
    """Regel direkt aus einer Logzeile ("Diese Meldung sperren")."""
    log_id: int
    scope: str = Field(default="host", pattern="^(host|all)$")
    action: str = Field(default="drop", pattern="^(drop|downgrade)$")
    purge_existing: bool = True


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    since = datetime.utcnow() - timedelta(hours=24)
    by_level = dict((await db.execute(
        select(func.lower(Log.level), func.count(Log.id)).where(Log.timestamp >= since)
        .group_by(func.lower(Log.level)))).all())
    rules = (await db.execute(select(LogRule).order_by(LogRule.id))).scalars().all()
    from ..diskguard import _retention_days
    return {
        "tiers": await loghandling.load_tiers(db),
        "default_tiers": loghandling.DEFAULT_TIERS,
        "global_days": await _retention_days(db),
        "dedup": await loghandling.load_dedup(db),
        "rules": [loghandling.public_rule(r) for r in rules],
        "last_24h": by_level,
        "categories": logexplain.CATEGORIES,
    }


@router.put("/tiers")
async def put_tiers(data: dict, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    try:
        tiers = await loghandling.save_tiers(db, data.get("tiers", data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    journal.fire(category="retention", event="retention.tiers", actor=admin.username,
                 message=f"Aufbewahrung je Stufe geändert: {tiers}")
    return {"tiers": tiers}


@router.put("/dedup")
async def put_dedup(data: dict, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    try:
        cfg = await loghandling.save_dedup(db, data)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Fenster bitte in Sekunden (0–3600).") from exc
    journal.fire(category="settings", event="logs.dedup", actor=admin.username,
                 message=f"Dubletten-Erkennung: {'an' if cfg['enabled'] else 'aus'}, {cfg['window_seconds']} s")
    return cfg


@router.get("/noise")
async def noise(hours: int = Query(24, ge=1, le=720), hostname: Optional[str] = Query(None),
                db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await loghandling.noise_report(db, hours=hours, hostname=hostname)


@router.get("/categories")
async def categories(hours: int = Query(24, ge=1, le=720), problems_only: bool = Query(True),
                     db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await loghandling.categories_report(db, hours=hours, problems_only=problems_only)


@router.get("/category/{key}")
async def category_items(key: str, hours: int = Query(72, ge=1, le=720),
                         problems_only: bool = Query(True), limit: int = Query(100, ge=1, le=300),
                         db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Juengste Zeilen eines Themas - die Kategorie steht nicht in der
    Datenbank, sie entsteht beim Lesen. Darum eine Stichprobe."""
    since = datetime.utcnow() - timedelta(hours=hours)
    q = select(Log.id, Log.timestamp, Log.hostname, Log.ip_address, Log.level, Log.source,
               Log.message, Log.facility, Log.extra_data).where(Log.timestamp >= since)
    if problems_only:
        q = q.where(func.lower(Log.level).notin_(loghandling.LOW_LEVELS))
    rows = (await db.execute(q.order_by(desc(Log.id)).limit(5000))).mappings().all()
    out = []
    for row in rows:
        parsed = logparse.parse(raw=row["message"] or "", message=row["message"] or "",
                                level=row["level"] or "", source=row["source"] or "",
                                facility=row["facility"], extra=row["extra_data"] or {})
        if parsed.get("category") != key:
            continue
        out.append({"id": row["id"], "ts": row["timestamp"].isoformat() if row["timestamp"] else None,
                    "host": row["hostname"], "ip": row["ip_address"], "level": row["level"],
                    "source": row["source"], "message": row["message"], "summary": parsed["summary"],
                    "hint": (parsed.get("explain") or {}).get("hint"),
                    "repeat": (row["extra_data"] or {}).get("repeat")})
        if len(out) >= limit:
            break
    return {"key": key, "label": logexplain.category_label(key), "items": out}


@router.get("/rules")
async def list_rules(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    rows = (await db.execute(select(LogRule).order_by(LogRule.id))).scalars().all()
    return {"items": [loghandling.public_rule(r) for r in rows]}


@router.post("/rules/preview")
async def preview_rule(data: RuleIn, hours: int = Query(24, ge=1, le=168),
                       db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    try:
        hits = await loghandling.count_matching(db, data.model_dump(), hours=hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"hits_in_sample": hits, "hours": hours}


async def _create(db: AsyncSession, payload: dict, user: str, purge: bool) -> dict:
    try:
        clean = loghandling.validate_rule(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rule = LogRule(**clean, created_by=user)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    purged = await loghandling.delete_matching(db, rule) if purge else 0
    await loghandling.refresh_rules(force=True)
    journal.fire(category="settings", event="logs.rule.created", actor=user, target=rule.name,
                 message=f"Sperrregel „{rule.name}“ angelegt ({rule.action})"
                 + (f", {purged} vorhandene Zeilen gelöscht" if purged else "") + ".")
    return {**loghandling.public_rule(rule), "purged": purged}


@router.post("/rules", status_code=201)
async def create_rule(data: RuleIn, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    payload = data.model_dump()
    purge = payload.pop("purge_existing", False)
    return await _create(db, payload, admin.username, purge)


@router.post("/rules/from-log", status_code=201)
async def rule_from_log(data: FromLogIn, db: AsyncSession = Depends(get_db),
                        admin=Depends(get_current_admin)):
    """"Diese Meldung brauche ich nicht": Regel aus einer Zeile ableiten. Die
    Vorlage (ohne Zahlen/IPs) trifft auch die Geschwister der Zeile."""
    log = await db.get(Log, data.log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Diese Logzeile gibt es nicht.")
    level = (log.level or "info").lower()
    if level not in loghandling.LOW_LEVELS and level not in ("warning", "warn"):
        raise HTTPException(status_code=400,
                            detail="Fehler und kritische Meldungen lassen sich hier nicht sperren – "
                                   "das wäre ein blinder Fleck. Bitte die Ursache beheben.")
    tpl = logexplain.template(log.message or "")
    exp = logexplain.explain(log.message or "", app=log.source or "") or {}
    payload = {"name": (exp.get("title") or tpl)[:150], "action": data.action,
               "hostname": log.hostname if data.scope == "host" else "",
               "source": log.source or "", "levels": [level], "match": "template",
               "pattern": tpl, "sample": (log.message or "")[:2000],
               "note": f"Aus Logzeile #{log.id} angelegt."}
    return await _create(db, payload, admin.username, data.purge_existing)


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, data: RuleIn, db: AsyncSession = Depends(get_db),
                      admin=Depends(get_current_admin)):
    rule = await db.get(LogRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unbekannte Regel.")
    payload = data.model_dump()
    payload.pop("purge_existing", None)
    try:
        clean = loghandling.validate_rule(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for k, v in clean.items():
        setattr(rule, k, v)
    await db.commit()
    await loghandling.refresh_rules(force=True)
    return loghandling.public_rule(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    rule = await db.get(LogRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unbekannte Regel.")
    name = rule.name
    await db.delete(rule)
    await db.commit()
    await loghandling.refresh_rules(force=True)
    journal.fire(category="settings", event="logs.rule.deleted", actor=admin.username, target=name,
                 message=f"Sperrregel „{name}“ gelöscht.")
    return {"deleted": True}


@router.post("/rules/{rule_id}/purge")
async def purge_rule(rule_id: int, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    rule = await db.get(LogRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unbekannte Regel.")
    n = await loghandling.delete_matching(db, rule)
    journal.fire(category="retention", event="logs.rule.purge", actor=admin.username, target=rule.name,
                 message=f"{n} Zeilen der Sperrregel „{rule.name}“ gelöscht.")
    return {"deleted": n}


EXPLAIN_SYSTEM = (
    "Du bist ein erfahrener Linux-, Windows- und Netzwerk-Administrator. Erkläre eine einzelne "
    "Logzeile für einen Admin, der wenig Zeit hat – auf Deutsch, klar und ohne Fachchinesisch. "
    "Antworte ausschließlich mit JSON: {\"was\": \"ein Satz, was passiert ist\", "
    "\"bedeutung\": \"ein bis zwei Sätze, ob das normal oder bedenklich ist\", "
    "\"tun\": \"konkret, was zu tun ist – oder 'nichts'\", \"rauschen\": true|false}"
)


@router.post("/explain/{log_id}")
async def explain_with_ai(log_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    log = await db.get(Log, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Diese Logzeile gibt es nicht.")
    parsed = logparse.parse_log_row(log)
    cfg = await ai.load_config(db)
    if cfg.get("provider", "off") == "off":
        return {"available": False, "rule_based": parsed.get("explain"),
                "message": "Keine KI eingerichtet (Automatisierung → KI). Die Regel-Erklärung steht oben."}
    prompt = (f"Gerät: {log.hostname} · Programm: {log.source} · Stufe: {log.level}\n"
              f"Zeile: {(log.raw_message or log.message or '')[:3000]}")
    try:
        result = await ai.ask_text(cfg, EXPLAIN_SYSTEM, prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"KI nicht erreichbar: {exc}") from exc
    answer = ai.parse_json_answer(result.get("answer", "")) or {"was": result.get("answer", "")[:800]}
    return {"available": True, "model": result.get("model"), "answer": answer,
            "rule_based": parsed.get("explain")}
