# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Log-Handling: Sperrregeln, Rauschen, Dubletten,
#               Aufbewahrung nach Stufen
# ==============================================================================
"""
Die Beobachtung aus dem Betrieb: von Info- und Hinweis-Zeilen gibt es viel zu
viele, von allem anderen wenig bis keine. Wer jede Info-Zeile so lange behaelt
wie einen Fehler und jede Wiederholung einzeln speichert, fuellt die Platte mit
Rauschen und findet die wichtigen Zeilen nicht mehr.

Vier Werkzeuge dagegen:

1. **Aufbewahrung nach Stufen** (`log_retention_tiers`). Jede Stufe hat ihre
   eigene Frist - Debug einen Tag, Info eine Woche, Fehler ein halbes Jahr.
   Die allgemeine Aufbewahrung (`log_retention_days`) bleibt die Obergrenze.

2. **Dubletten ueber Stapel hinweg.** Dieselbe Meldung (bis auf Zahlen, IPs,
   IDs) innerhalb des Dublettenfensters (Standard 5 min) wird keine neue
   Zeile, sondern zaehlt die erste hoch (`extra_data.repeat`). Nur fuer
   debug/info/notice - Warnungen und Fehler bleiben immer einzeln sichtbar.

3. **Rausch-Erkennung.** Wertet die juengsten Info/Hinweis-Zeilen nach Vorlage
   aus und zeigt, was am meisten Platz frisst - samt Klartext (logexplain)
   und Einschaetzung, ob es typisches Grundrauschen ist.

4. **Sperrregeln** (`log_rules`). Was als unnoetig erkannt ist, wird beim
   Empfang verworfen (`drop`) oder nur noch als Debug gespeichert
   (`downgrade`). Gilt fuer HTTPS-Agents hier und fuer den Syslog-Empfaenger
   (der liest die Regeln selbst aus der Datenbank, siehe
   server/syslog/syslog_server.py - dort dieselbe Logik in Kurzform).
"""

from __future__ import annotations

import fnmatch
import logging
import re
import time
from collections import OrderedDict, Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import logexplain
from .database import Base, async_session, engine
from .models import APP_TABLES, LOG_TABLES, Log, LogRule, Setting

logger = logging.getLogger("rimperio.loghandling")

LOW_LEVELS = ("debug", "info", "informational", "notice")

# Stufen-Gruppen und ihre Schreibweisen in der Datenbank.
LEVEL_GROUPS: Dict[str, Tuple[str, ...]] = {
    "debug": ("debug",),
    "info": ("info", "informational"),
    "notice": ("notice",),
    "warning": ("warning", "warn"),
    "error": ("error", "err"),
    "critical": ("critical", "crit", "alert", "emergency", "emerg", "panic"),
}

TIERS_KEY = "log_retention_tiers"
DEDUP_KEY = "log_dedup"

# Logische Stufen: je unwichtiger, desto kuerzer. Fehler und Kritisches lange,
# weil man sie oft erst Wochen spaeter braucht (Ursachenforschung).
DEFAULT_TIERS: Dict[str, int] = {
    "debug": 1, "info": 7, "notice": 14, "warning": 60, "error": 180, "critical": 365,
}
DEFAULT_DEDUP = {"enabled": True, "window_seconds": 300}


# =============================================================================
# Schema
# =============================================================================
async def ensure_schema() -> None:
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=LOG_TABLES + APP_TABLES))
        logger.info("log_rules bereit")
    except Exception as exc:                                        # defensiv
        logger.error("log_rules nicht angelegt: %s", exc)


# =============================================================================
# Einstellungen
# =============================================================================
async def _setting(db: AsyncSession, key: str) -> Any:
    row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    return row.value if row is not None else None


async def _save_setting(db: AsyncSession, key: str, value: Any, description: str = "") -> None:
    row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(Setting(key=key, value=value, description=description))
    else:
        row.value = value
    await db.commit()


async def load_tiers(db: AsyncSession) -> Dict[str, int]:
    """Fristen je Stufe. Ohne eigene Einstellung: Voreinstellung, wobei ein
    frueher gesetztes 'log_retention_days_info' fuer info/notice/debug gilt."""
    saved = await _setting(db, TIERS_KEY)
    tiers = dict(DEFAULT_TIERS)
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k in tiers:
                try:
                    tiers[k] = max(0, int(v))
                except (TypeError, ValueError):
                    pass
        return tiers
    legacy = await _setting(db, "log_retention_days_info")
    try:
        legacy = int(legacy or 0)
    except (TypeError, ValueError):
        legacy = 0
    if legacy > 0:
        tiers.update({"info": legacy, "notice": legacy, "debug": min(tiers["debug"], legacy)})
    return tiers


async def save_tiers(db: AsyncSession, incoming: Dict[str, Any]) -> Dict[str, int]:
    tiers = await load_tiers(db)
    for k, v in (incoming or {}).items():
        if k not in tiers:
            continue
        try:
            tiers[k] = max(0, min(int(v), 3650))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Frist für {k}: bitte eine Zahl (Tage).") from exc
    order = ["debug", "info", "notice", "warning", "error", "critical"]
    # Unwichtigeres darf nicht laenger leben als Wichtigeres - sonst ist die
    # Logik der Stufen auf den Kopf gestellt.
    for low, high in zip(order, order[1:]):
        if tiers[low] and tiers[high] and tiers[low] > tiers[high]:
            raise ValueError(f"{low} ({tiers[low]} Tage) darf nicht länger aufbewahrt werden als "
                             f"{high} ({tiers[high]} Tage).")
    await _save_setting(db, TIERS_KEY, tiers, "Aufbewahrung je Log-Stufe in Tagen (0 = wie allgemein)")
    return tiers


async def load_dedup(db: AsyncSession) -> dict:
    saved = await _setting(db, DEDUP_KEY)
    cfg = dict(DEFAULT_DEDUP)
    if isinstance(saved, dict):
        cfg["enabled"] = bool(saved.get("enabled", cfg["enabled"]))
        try:
            cfg["window_seconds"] = max(0, min(int(saved.get("window_seconds", cfg["window_seconds"])), 3600))
        except (TypeError, ValueError):
            pass
    return cfg


async def save_dedup(db: AsyncSession, incoming: dict) -> dict:
    cfg = await load_dedup(db)
    if "enabled" in incoming:
        cfg["enabled"] = bool(incoming["enabled"])
    if "window_seconds" in incoming:
        cfg["window_seconds"] = max(0, min(int(incoming["window_seconds"]), 3600))
    await _save_setting(db, DEDUP_KEY, cfg, "Dubletten zusammenfassen (Info/Hinweis/Debug)")
    _dedup_cfg_cache.update(cfg)
    return cfg


async def enforce_tiers(db: AsyncSession, global_days: int, delete_older_than) -> Tuple[int, List[str]]:
    """Loescht je Stufe, was aelter als deren Frist ist (wird von diskguard
    aufgerufen, der das haeppchenweise Loeschen mitbringt)."""
    tiers = await load_tiers(db)
    deleted, steps = 0, []
    for group, days in tiers.items():
        if not days or days >= global_days:
            continue
        names = LEVEL_GROUPS[group]
        count = await delete_older_than(db, datetime.utcnow() - timedelta(days=days),
                                        levels=tuple(names) + tuple(n.upper() for n in names)
                                        + tuple(n.capitalize() for n in names))
        if count:
            deleted += count
            steps.append(f"{count:,} {group.upper()}-Zeilen älter als {days} Tage entfernt".replace(",", "."))
    return deleted, steps


# =============================================================================
# Sperrregeln
# =============================================================================
_rules_cache: List[LogRule] = []
_rules_loaded_at = 0.0
_compiled: Dict[int, "re.Pattern[str]"] = {}
_hits: Counter = Counter()
RULE_CACHE_SECONDS = 30


def public_rule(r: LogRule) -> dict:
    return {"id": r.id, "name": r.name, "enabled": r.enabled, "action": r.action,
            "hostname": r.hostname or "", "source": r.source or "", "levels": r.levels or [],
            "match": r.match, "pattern": r.pattern, "sample": r.sample or "", "note": r.note or "",
            "hits": r.hits or 0, "last_hit_at": _iso(r.last_hit_at), "created_by": r.created_by,
            "created_at": _iso(r.created_at)}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def validate_rule(data: dict) -> dict:
    out = {
        "name": str(data.get("name") or "").strip()[:200],
        "enabled": bool(data.get("enabled", True)),
        "action": str(data.get("action") or "drop"),
        "hostname": str(data.get("hostname") or "").strip()[:255],
        "source": str(data.get("source") or "").strip()[:255],
        "levels": [str(x).lower() for x in (data.get("levels") or []) if str(x).strip()][:10],
        "match": str(data.get("match") or "contains"),
        "pattern": str(data.get("pattern") or "").strip(),
        "sample": str(data.get("sample") or "")[:2000],
        "note": str(data.get("note") or "")[:2000],
    }
    if out["action"] not in ("drop", "downgrade"):
        raise ValueError("Aktion: drop (nicht speichern) oder downgrade (nur kurz als Debug).")
    if out["match"] not in ("contains", "regex", "template"):
        raise ValueError("Vergleich: contains, regex oder template.")
    if len(out["pattern"]) < 3:
        raise ValueError("Das Muster ist zu kurz (mindestens 3 Zeichen) – sonst trifft die Regel fast alles.")
    if out["match"] == "regex":
        try:
            re.compile(out["pattern"])
        except re.error as exc:
            raise ValueError(f"Ungültiger regulärer Ausdruck: {exc}") from exc
    if not out["name"]:
        out["name"] = out["pattern"][:80]
    # Wichtige Stufen nie still wegwerfen: eine Sperre auf Fehler waere ein
    # blinder Fleck. Wer das wirklich will, muss die Stufen ausdruecklich nennen.
    if not out["levels"]:
        out["levels"] = ["debug", "info", "informational", "notice"]
    return out


async def refresh_rules(force: bool = False) -> List[LogRule]:
    global _rules_cache, _rules_loaded_at
    if not force and time.monotonic() - _rules_loaded_at < RULE_CACHE_SECONDS:
        return _rules_cache
    try:
        async with async_session() as db:
            rows = (await db.execute(select(LogRule).where(LogRule.enabled.is_(True))
                                     .order_by(LogRule.id))).scalars().all()
            for r in rows:
                db.expunge(r)
        _rules_cache = list(rows)
        _compiled.clear()
        for r in _rules_cache:
            if r.match == "regex":
                try:
                    _compiled[r.id] = re.compile(r.pattern)
                except re.error:
                    pass
        _rules_loaded_at = time.monotonic()
        await _flush_hits()
    except Exception as exc:                                        # defensiv
        logger.debug("Sperrregeln nicht geladen: %s", exc)
        _rules_loaded_at = time.monotonic()
    return _rules_cache


async def _flush_hits() -> None:
    if not _hits:
        return
    pending = dict(_hits)
    _hits.clear()
    try:
        async with async_session() as db:
            now = datetime.utcnow()
            for rid, n in pending.items():
                await db.execute(text("UPDATE log_rules SET hits = hits + :n, last_hit_at = :t WHERE id = :id"),
                                 {"n": n, "t": now, "id": rid})
            await db.commit()
    except Exception as exc:                                        # defensiv
        logger.debug("Treffer nicht gespeichert: %s", exc)


def rule_matches(r: LogRule, hostname: str, source: str, level: str, message: str,
                 tpl: Optional[str] = None) -> bool:
    lv = (level or "").lower()
    if r.levels and lv not in r.levels:
        return False
    if r.hostname and not fnmatch.fnmatch((hostname or "").lower(), r.hostname.lower()):
        return False
    if r.source and not fnmatch.fnmatch((source or "").lower(), r.source.lower()):
        return False
    msg = message or ""
    if r.match == "contains":
        return r.pattern.lower() in msg.lower()
    if r.match == "regex":
        pat = _compiled.get(r.id)
        return bool(pat and pat.search(msg))
    if r.match == "template":
        return (tpl if tpl is not None else logexplain.template(msg)) == r.pattern
    return False


def check(hostname: str, source: str, level: str, message: str) -> Optional[str]:
    """None = normal speichern, 'drop' = verwerfen, 'downgrade' = als debug."""
    if not _rules_cache:
        return None
    tpl = None
    for r in _rules_cache:
        if r.match == "template" and tpl is None:
            tpl = logexplain.template(message)
        if rule_matches(r, hostname, source, level, message, tpl):
            _hits[r.id] += 1
            return r.action
    return None


def template_to_like(tpl: str) -> str:
    """Vorlage -> LIKE-Muster fuer das Loeschen vorhandener Zeilen."""
    like = tpl.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    for ph in ("<uuid>", "<mac>", "<ip>", "<hex>", "<ts>", "#"):
        like = like.replace(ph, "%")
    while "%%" in like:
        like = like.replace("%%", "%")
    return like


async def delete_matching(db: AsyncSession, r: LogRule, limit_batches: int = 200) -> int:
    """Vorhandene Zeilen loeschen, die eine Regel trifft (haeppchenweise)."""
    cond = []
    params: Dict[str, Any] = {}
    if r.levels:
        cond.append("lower(level) = ANY(:levels)")
        params["levels"] = list(r.levels)
    if r.hostname:
        cond.append("hostname ILIKE :host")
        params["host"] = r.hostname.replace("*", "%").replace("?", "_")
    if r.source:
        cond.append("source ILIKE :src")
        params["src"] = r.source.replace("*", "%").replace("?", "_")
    if r.match == "contains":
        cond.append("message ILIKE :pat")
        params["pat"] = "%" + r.pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    elif r.match == "regex":
        cond.append("message ~ :pat")
        params["pat"] = r.pattern
    else:
        cond.append("message LIKE :pat")
        params["pat"] = template_to_like(r.pattern)
    where = " AND ".join(cond)
    total = 0
    for _ in range(limit_batches):
        res = await db.execute(text(
            f"DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE {where} LIMIT 5000)"), params)
        await db.commit()
        n = res.rowcount or 0
        total += n
        if n < 5000:
            break
    return total


async def count_matching(db: AsyncSession, data: dict, hours: int = 24) -> int:
    """Wie viele Zeilen der letzten `hours` haette diese Regel getroffen? (Vorschau)"""
    r = LogRule(**validate_rule(data))
    rows = (await db.execute(
        select(Log.hostname, Log.source, Log.level, Log.message)
        .where(Log.timestamp >= datetime.utcnow() - timedelta(hours=hours))
        .order_by(desc(Log.id)).limit(20000))).all()
    if r.match == "regex":
        _compiled[-1] = re.compile(r.pattern)
        r.id = -1
    return sum(1 for h, s, lv, m in rows if rule_matches(r, h or "", s or "", lv or "", m or ""))


# =============================================================================
# Dubletten ueber Lieferungen hinweg (HTTPS-Ingest)
# =============================================================================
_dedup_cfg_cache: dict = dict(DEFAULT_DEDUP)


class Deduper:
    """Merkt sich die juengsten Vorlagen je Geraet/Stufe/Programm.

    Kommt dieselbe Vorlage im Fenster wieder, wird sie nicht gespeichert,
    sondern bei der ersten Zeile hochgezaehlt (gesammelt, beim naechsten
    Abgleich in einem Rutsch). So bleibt die Information - "1.200x in 5 min" -
    ohne 1.200 Zeilen.
    """

    def __init__(self, max_keys: int = 50000) -> None:
        self.recent: "OrderedDict[tuple, list]" = OrderedDict()
        self.max_keys = max_keys

    def seen(self, agent_id: Optional[int], level: str, source: str, message: str,
             now: Optional[float] = None) -> bool:
        cfg = _dedup_cfg_cache
        if not cfg.get("enabled") or not cfg.get("window_seconds"):
            return False
        if (level or "").lower() not in LOW_LEVELS:
            return False
        now = now or time.monotonic()
        key = (agent_id, (level or "").lower(), source or "", logexplain.template(message, 200))
        entry = self.recent.get(key)
        if entry and now - entry[0] <= cfg["window_seconds"]:
            entry[1] += 1
            entry[3] = datetime.utcnow()
            return True
        # [erstes Auftreten (monotonic), ausstehende Wiederholungen, Originaltext, zuletzt]
        self.recent[key] = [now, 0, message, None]
        self.recent.move_to_end(key)
        while len(self.recent) > self.max_keys:
            self.recent.popitem(last=False)
        return False

    def pending(self) -> List[Tuple[Optional[int], str, int, datetime]]:
        out = []
        for key, entry in self.recent.items():
            if entry[1]:
                out.append((key[0], entry[2], entry[1], entry[3] or datetime.utcnow()))
                entry[1] = 0
        return out


ingest_deduper = Deduper()


async def flush_repeats(db: AsyncSession, deduper: Deduper = ingest_deduper) -> int:
    """Ausstehende Wiederholungen auf die jeweils juengste Zeile buchen."""
    done = 0
    for agent_id, message, n, last in deduper.pending():
        try:
            await db.execute(text(
                "UPDATE logs SET extra_data = (jsonb_set(jsonb_set(jsonb_set(COALESCE(extra_data::jsonb, '{}'::jsonb), "
                "'{repeat}', to_jsonb(COALESCE((extra_data::jsonb->>'repeat')::int, 1) + :n)), "
                "'{repeat_last}', to_jsonb(CAST(:last AS text))), '{repeat_similar}', 'true'::jsonb))::json "
                "WHERE id = (SELECT id FROM logs WHERE agent_id IS NOT DISTINCT FROM :aid AND message = :msg "
                "ORDER BY id DESC LIMIT 1)"),
                {"n": n, "last": last.isoformat(), "aid": agent_id, "msg": message})
            done += 1
        except Exception as exc:                                    # defensiv
            logger.debug("Wiederholung nicht gebucht: %s", exc)
            await db.rollback()
    if done:
        await db.commit()
    return done


CRITICAL_NAMES = LEVEL_GROUPS["critical"]
ERROR_NAMES = LEVEL_GROUPS["error"]
_problem_last_id: Optional[int] = None


async def scan_problem_logs(db: AsyncSession) -> int:
    """Neue kritische/Fehler-Zeilen melden (App + eingeschaltete Wege).

    Gebuendelt je Geraet und Stufe; die Sperrfrist in alerts.py verhindert
    Dauerfeuer. Beim Start wird nur der Stand gemerkt - keine Flut alter Zeilen.
    """
    global _problem_last_id
    from . import alerts
    if _problem_last_id is None:
        _problem_last_id = (await db.execute(select(func.max(Log.id)))).scalar() or 0
        return 0
    rows = (await db.execute(
        select(Log.id, Log.hostname, Log.level, Log.source, Log.message)
        .where(Log.id > _problem_last_id,
               func.lower(Log.level).in_(CRITICAL_NAMES + ERROR_NAMES))
        .order_by(Log.id).limit(1000))).all()
    newest = (await db.execute(select(func.max(Log.id)))).scalar() or _problem_last_id
    _problem_last_id = max(_problem_last_id, newest)
    groups: Dict[Tuple[str, str], List[str]] = {}
    for lid, host, lv, src, msg in rows:
        group = "critical" if (lv or "").lower() in CRITICAL_NAMES else "error"
        exp = logexplain.explain(msg or "", app=src or "") or {}
        groups.setdefault((host or "?", group), []).append(exp.get("title") or (msg or "")[:200])
    for (host, group), titles in groups.items():
        word = "kritische Logzeile(n)" if group == "critical" else "Fehler-Logzeile(n)"
        text = "\n".join(f"• {t}" for t in titles[:3]) + (f"\n… und {len(titles) - 3} weitere" if len(titles) > 3 else "")
        await alerts.dispatch("log_critical" if group == "critical" else "log_error",
                              f"RIMPERIO: {host}: {len(titles)} {word}", text,
                              level="critical" if group == "critical" else "warning",
                              key=f"log:{group}:{host}",
                              detail={"hostname": host, "link": f"/logs?hostname={host}"})
    return len(rows)


async def housekeeping_loop() -> None:
    """Regeln neu laden, Treffer zaehlen, Wiederholungen buchen, Probleme melden."""
    import asyncio
    await asyncio.sleep(10)
    while True:
        try:
            async with async_session() as db:
                _dedup_cfg_cache.update(await load_dedup(db))
                await flush_repeats(db)
                await scan_problem_logs(db)
            await refresh_rules(force=True)
        except Exception as exc:                                    # defensiv
            logger.debug("Log-Handling-Takt: %s", exc)
        await asyncio.sleep(RULE_CACHE_SECONDS)


# =============================================================================
# Rausch-Erkennung
# =============================================================================
async def noise_report(db: AsyncSession, hours: int = 24, sample: int = 20000,
                       hostname: Optional[str] = None) -> dict:
    """Was frisst den meisten Platz? Gruppiert die juengsten Info/Hinweis/Debug-
    Zeilen nach Vorlage. Eine Stichprobe statt der ganzen Tabelle - das reicht,
    um Rauschen zu finden, und dauert Sekundenbruchteile statt Minuten."""
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (select(Log.id, Log.hostname, Log.source, Log.level, Log.message, Log.extra_data)
         .where(Log.timestamp >= since, func.lower(Log.level).in_(LOW_LEVELS))
         .order_by(desc(Log.id)).limit(sample))
    if hostname:
        q = q.where(func.lower(Log.hostname) == hostname.lower())
    rows = (await db.execute(q)).all()

    level_counts = dict((await db.execute(
        select(func.lower(Log.level), func.count(Log.id)).where(Log.timestamp >= since)
        .group_by(func.lower(Log.level)))).all())
    total_window = sum(level_counts.values()) or 0
    low_window = sum(v for k, v in level_counts.items() if k in LOW_LEVELS)

    await refresh_rules()
    groups: Dict[tuple, dict] = {}
    for lid, host, src, lv, msg, extra in rows:
        tpl = logexplain.template(msg or "", 200)
        key = ((host or "").lower(), (src or "").lower(), tpl)
        g = groups.get(key)
        repeat = 1
        if isinstance(extra, dict):
            try:
                repeat = max(1, int(extra.get("repeat") or 1))
            except (TypeError, ValueError):
                repeat = 1
        if g is None:
            g = groups[key] = {"hostname": host, "source": src, "level": (lv or "").lower(),
                               "template": tpl, "sample": msg or "", "sample_id": lid,
                               "rows": 0, "events": 0, "hosts": set()}
        g["rows"] += 1
        g["events"] += repeat
        g["hosts"].add(host)

    scale = (low_window / len(rows)) if rows and low_window else 1.0
    out = []
    for g in sorted(groups.values(), key=lambda x: x["rows"], reverse=True)[:60]:
        exp = logexplain.explain(g["sample"], app=g["source"] or "") or {}
        share = g["rows"] / len(rows) if rows else 0
        blocked = any(rule_matches(r, g["hostname"] or "", g["source"] or "", g["level"],
                                   g["sample"]) for r in _rules_cache)
        score = min(100, int(share * 400) + (30 if exp.get("noise") else 0) + (10 if g["events"] > g["rows"] else 0))
        out.append({
            "hostname": g["hostname"], "source": g["source"], "level": g["level"],
            "template": g["template"], "sample": g["sample"][:500], "sample_id": g["sample_id"],
            "rows_in_sample": g["rows"], "events_in_sample": g["events"],
            "estimated_rows": int(g["rows"] * scale), "share_percent": round(share * 100, 1),
            "title": exp.get("title") or "", "category": exp.get("category") or "other",
            "category_label": logexplain.category_label(exp.get("category")),
            "known_noise": bool(exp.get("noise")), "score": score, "blocked": blocked,
            "suggestion": ("sperren" if score >= 60 else "beobachten" if score >= 30 else ""),
        })
    return {"hours": hours, "sample_size": len(rows), "window_total": total_window,
            "window_low": low_window,
            "low_share_percent": round(100 * low_window / total_window, 1) if total_window else 0,
            "by_level": level_counts, "groups": out}


async def categories_report(db: AsyncSession, hours: int = 24, sample: int = 5000,
                            problems_only: bool = True) -> dict:
    """Juengste Zeilen nach Themenbereich (Anmeldung, Netzwerk, Dienste …)."""
    since = datetime.utcnow() - timedelta(hours=hours)
    q = select(Log.id, Log.hostname, Log.source, Log.level, Log.message).where(Log.timestamp >= since)
    if problems_only:
        q = q.where(func.lower(Log.level).notin_(LOW_LEVELS))
    rows = (await db.execute(q.order_by(desc(Log.id)).limit(sample))).all()
    cats: Dict[str, dict] = {}
    for lid, host, src, lv, msg in rows:
        exp = logexplain.explain(msg or "", app=src or "") or {}
        cat = exp.get("category") or "other"
        c = cats.setdefault(cat, {"key": cat, "label": logexplain.category_label(cat), "count": 0,
                                  "hosts": Counter(), "latest": None})
        c["count"] += 1
        c["hosts"][host] += 1
        if c["latest"] is None:
            c["latest"] = {"id": lid, "host": host, "level": lv,
                           "title": exp.get("title") or (msg or "")[:200]}
    order = list(logexplain.CATEGORIES)
    items = sorted(cats.values(), key=lambda c: (-c["count"], order.index(c["key"]) if c["key"] in order else 99))
    for c in items:
        c["top_hosts"] = [{"host": h, "count": n} for h, n in c.pop("hosts").most_common(5)]
    return {"hours": hours, "sample_size": len(rows), "problems_only": problems_only, "items": items}
