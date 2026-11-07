# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - SNMP-Ueberwachung: Geraete, Vorlagen, Abfrage, Schwellen, Alarme
# ==============================================================================
"""
SNMP-Ueberwachung fuer alles, auf dem kein Agent laeuft: Switches, Router,
Firewalls, Drucker, USVs, NAS.

**Einrichten in drei Schritten:**

1. Zugangsdaten anlegen (v1/v2c Community oder v3-Benutzer) - einmal, fuer viele
   Geraete.
2. Geraet hinzufuegen (oder aus der Netzwerksuche uebernehmen). Ein Test zeigt
   sofort sysName/sysDescr und schlaegt die passenden Vorlagen vor.
3. Fertig. Im eingestellten Takt (Standard 5 Minuten) werden die Werte gelesen,
   bewertet und bei Ueberschreitung gemeldet.

**Wer fragt ab?** Der Server selbst - oder ein RIMPERIO-Agent im Netz des
Geraets (fuer andere Standorte; der Agent kann zusaetzlich AES-192/256). In
beiden Faellen kommen dieselben Rohwerte an, und die Bewertung passiert hier.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import alerts, journal, notify, snmp
from .database import Base, async_session, engine
from .models import (SNMP_TABLES, Agent, RmmAlert, SnmpCredential, SnmpDevice, SnmpMetric,
                     SnmpSample, SnmpTemplate)
from .snmp_templates import BUILTIN_TEMPLATES, TEMPLATE_INDEX

logger = logging.getLogger("rimperio.snmpmon")

LOOP_SECONDS = 15
SAMPLE_RETENTION_DAYS = 14
DOWN_AFTER_FAILURES = 2
SYSTEM_OIDS = [snmp.SYS_DESCR, snmp.SYS_OBJECT_ID, snmp.SYS_UPTIME, snmp.SYS_CONTACT,
               snmp.SYS_NAME, snmp.SYS_LOCATION]
METRIC_TYPES = ("gauge", "rate", "ratio", "enum", "ticks", "text")


async def ensure_schema() -> None:
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=SNMP_TABLES))
        logger.info("SNMP-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("SNMP-Schema nicht angelegt: %s", exc)


# =============================================================================
# Ausgabe
# =============================================================================
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def public_credential(c: SnmpCredential) -> dict:
    return {"id": c.id, "name": c.name, "version": c.version, "username": c.username,
            "auth_proto": c.auth_proto, "priv_proto": c.priv_proto, "context": c.context,
            "port": c.port, "community_set": bool(c.community), "auth_pass_set": bool(c.auth_pass),
            "priv_pass_set": bool(c.priv_pass)}


def public_device(d: SnmpDevice, metrics: Optional[List[SnmpMetric]] = None) -> dict:
    out = {"id": d.id, "name": d.name, "host": d.host, "port": d.port, "credential_id": d.credential_id,
           "poller": d.poller, "site": d.site, "templates": d.templates or [], "custom": d.custom or [],
           "overrides": d.overrides or {}, "interval_sec": d.interval_sec, "enabled": d.enabled,
           "status": d.status, "system": d.system or {}, "last_poll_at": _iso(d.last_poll_at),
           "last_ok_at": _iso(d.last_ok_at), "last_error": d.last_error, "rtt_ms": d.rtt_ms,
           "discovered_device_id": d.discovered_device_id}
    if metrics is not None:
        out["metrics"] = [public_metric(m) for m in metrics]
    return out


def public_metric(m: SnmpMetric) -> dict:
    return {"key": m.key, "label": m.label, "group": m.group, "value": m.value, "text": m.text,
            "unit": m.unit, "status": m.status, "ts": _iso(m.ts)}


# =============================================================================
# Vorlagen
# =============================================================================
async def all_templates(db: AsyncSession) -> Dict[str, dict]:
    out = {k: {**v, "builtin": True} for k, v in TEMPLATE_INDEX.items()}
    for t in (await db.execute(select(SnmpTemplate))).scalars():
        out[t.key] = {"key": t.key, "name": t.name, "vendor": t.vendor, "description": t.description,
                      "metrics": t.metrics or [], "probe": (t.match or [None])[0] if t.match else None,
                      "builtin": False, "id": t.id}
    return out


def validate_metric(m: dict, prefix: str = "") -> dict:
    if not isinstance(m, dict):
        raise ValueError("Metrik muss ein Objekt sein.")
    key = str(m.get("key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key):
        raise ValueError(f"Metrik-Schlüssel '{key}': nur Buchstaben, Ziffern, . _ - (max. 80).")
    if prefix and not key.startswith(prefix):
        key = prefix + key
    typ = m.get("type") or "gauge"
    if typ not in METRIC_TYPES:
        raise ValueError(f"Metrik '{key}': Art {', '.join(METRIC_TYPES)}.")
    oid_re = re.compile(r"^\.?\d+(\.\d+)+$")
    oids = [m.get("oid"), m.get("num"), m.get("den")]
    table = m.get("table") or {}
    oids += [table.get(k) for k in ("column", "column_fallback", "num", "den", "label", "label_fallback")]
    if not any(oids):
        raise ValueError(f"Metrik '{key}': OID fehlt.")
    for o in oids:
        if o and not oid_re.match(str(o)):
            raise ValueError(f"Metrik '{key}': '{o}' ist keine OID.")
    out = {k: v for k, v in m.items() if k in (
        "label", "group", "type", "oid", "num", "den", "den_plus_num", "table", "unit", "scale",
        "bits", "warn", "crit", "op", "map", "ok", "alert", "history", "aggregate")}
    out["key"], out["type"] = key, typ
    out.setdefault("label", key)
    if out.get("op") not in (None, ">", "<"):
        raise ValueError(f"Metrik '{key}': Vergleich > oder <.")
    if table.get("exclude"):
        try:
            re.compile(table["exclude"])
        except re.error as exc:
            raise ValueError(f"Metrik '{key}': Ausschlussmuster ungültig ({exc}).") from exc
    return out


def needed(templates: List[dict]) -> Tuple[List[str], List[str]]:
    """Welche Einzelwerte (GET) und Tabellen (WALK) braucht diese Vorlagen-Auswahl?"""
    gets, walks = list(SYSTEM_OIDS), []
    for t in templates:
        for m in t.get("metrics") or []:
            for k in ("oid", "num", "den"):
                if m.get(k):
                    gets.append(str(m[k]).strip("."))
            table = m.get("table") or {}
            for k in ("column", "column_fallback", "num", "den", "label", "label_fallback"):
                if table.get(k):
                    walks.append(str(table[k]).strip("."))
    return sorted(set(gets)), sorted(set(walks))


def device_templates(device: SnmpDevice, catalog: Dict[str, dict]) -> List[dict]:
    chosen = [catalog[k] for k in (device.templates or []) if k in catalog]
    if device.custom:
        chosen.append({"key": "_custom", "name": "Eigene Werte", "metrics": device.custom})
    return chosen


# =============================================================================
# Abfrage
# =============================================================================
def normalize(value: Any) -> Any:
    """Wie der Agent: Zahlen bleiben Zahlen, lesbarer Text bleibt Text, sonst 0x…"""
    if value is snmp.END or value is None:
        return None
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
            if all(ch >= " " or ch in "\t\r\n\x00" for ch in text):
                return text.rstrip("\x00")
        except UnicodeDecodeError:
            pass
        return "0x" + value.hex()
    return value


def client_kwargs(cred: Optional[SnmpCredential], port: Optional[int] = None) -> dict:
    if cred is None:
        return {"version": "2c", "community": "public", "port": port or 161}
    kw = {"version": cred.version or "2c", "port": port or cred.port or 161}
    if kw["version"] == "3":
        kw.update(user=cred.username, auth_proto=cred.auth_proto or "none", auth_pass=cred.auth_pass,
                  priv_proto=cred.priv_proto or "none", priv_pass=cred.priv_pass, context=cred.context or "")
    else:
        kw["community"] = cred.community or "public"
    return kw


async def poll_server(host: str, kw: dict, gets: List[str], walks: List[str],
                      timeout: float = 3.0) -> dict:
    started = datetime.utcnow()
    raw: Dict[str, Any] = {"ok": False, "error": "", "values": {}, "walks": {}, "rtt_ms": None}
    try:
        async with snmp.SnmpClient(host, timeout=timeout, retries=1, **kw) as client:
            if kw.get("version") == "1":
                for oid in gets:                     # v1: eine fehlende OID verdirbt die ganze Anfrage
                    raw["values"].update(await client.get(oid))
            else:
                raw["values"] = await client.get(*gets)
            for base in walks:
                try:
                    rows = await client.walk(base, max_rows=2000)
                except snmp.SnmpError as exc:
                    logger.debug("%s: Tabelle %s nicht lesbar: %s", host, base, exc)
                    rows = []
                raw["walks"][base] = [[idx, normalize(v)] for idx, v in rows if v is not snmp.END]
        raw["values"] = {k: normalize(v) for k, v in raw["values"].items() if v is not snmp.END}
        raw["ok"] = bool(raw["values"])
        if not raw["ok"]:
            raw["error"] = "Gerät antwortet, liefert aber keine Werte"
    except (snmp.SnmpError, OSError) as exc:
        raw["error"] = str(exc) or exc.__class__.__name__
    raw["rtt_ms"] = int((datetime.utcnow() - started).total_seconds() * 1000)
    return raw


async def probe(host: str, kw: dict, catalog: Dict[str, dict]) -> dict:
    """Erreichbar? Was fuer ein Geraet? Welche Vorlagen passen?"""
    try:
        async with snmp.SnmpClient(host, timeout=3.0, retries=1, **kw) as client:
            values: Dict[str, Any] = {}
            probes = {t["key"]: t["probe"].strip(".") for t in catalog.values() if t.get("probe")}
            oids = SYSTEM_OIDS + sorted(set(probes.values()))
            if kw.get("version") == "1":
                for oid in oids:
                    values.update(await client.get(oid))
            else:
                values = await client.get(*oids)
    except (snmp.SnmpError, OSError) as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    if not values:
        return {"ok": False, "error": "Gerät antwortet nicht mit Systemdaten (Community/Benutzer prüfen)."}
    suggested = [k for k, t in catalog.items() if t.get("always")]
    suggested += [k for k, oid in probes.items() if values.get(oid) not in (None, snmp.END)]
    return {"ok": True, "system": _system(values), "suggested": sorted(set(suggested), key=lambda k: list(catalog).index(k))}


def _system(values: Dict[str, Any]) -> dict:
    return {"sys_descr": _t(values.get(snmp.SYS_DESCR))[:500],
            "sys_name": _t(values.get(snmp.SYS_NAME))[:255],
            "sys_object_id": _t(values.get(snmp.SYS_OBJECT_ID)),
            "sys_location": _t(values.get(snmp.SYS_LOCATION))[:255],
            "sys_contact": _t(values.get(snmp.SYS_CONTACT))[:255],
            "uptime_seconds": int(_f(values.get(snmp.SYS_UPTIME)) or 0) // 100}


def _t(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").strip("\x00").strip()
    return "" if v is None or v is snmp.END else str(v).strip()


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and not v.startswith("0x"):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


# =============================================================================
# Auswertung
# =============================================================================
def _rows(raw: dict, base: Optional[str]) -> Dict[str, Any]:
    if not base:
        return {}
    return {str(idx): v for idx, v in (raw.get("walks") or {}).get(str(base).strip("."), []) or []}


def _status(m: dict, value: Optional[float], raw_value: Any) -> str:
    if m.get("type") == "enum":
        code = str(int(raw_value)) if isinstance(raw_value, (int, float)) else str(raw_value)
        crit = m.get("crit") if isinstance(m.get("crit"), list) else []
        warn = m.get("warn") if isinstance(m.get("warn"), list) else []
        if code in [str(x) for x in crit]:
            return "critical"
        if code in [str(x) for x in warn]:
            return "warning"
        ok = m.get("ok")
        if ok and code not in [str(x) for x in ok]:
            return "warning"
        return "ok"
    if value is None:
        return "unknown"
    warn, crit, op = m.get("warn"), m.get("crit"), m.get("op") or ">"
    try:
        if crit is not None and (value >= float(crit) if op == ">" else value <= float(crit)):
            return "critical"
        if warn is not None and (value >= float(warn) if op == ">" else value <= float(warn)):
            return "warning"
    except (TypeError, ValueError):
        pass
    return "ok"


def _apply_override(m: dict, overrides: dict) -> dict:
    o = (overrides or {}).get(m["key"])
    if not isinstance(o, dict):
        return m
    m = dict(m)
    for k in ("warn", "crit", "op", "alert"):
        if k in o:
            m[k] = o[k]
    return m


def evaluate(raw: dict, templates: List[dict], overrides: dict, state: dict,
             now: datetime) -> Tuple[List[dict], dict]:
    """Rohwerte → Metriken [{key, label, group, value, text, unit, status, alert, history}]."""
    out: List[dict] = []
    new_state = dict(state or {})
    values = raw.get("values") or {}

    def emit(m: dict, key: str, label: str, raw_value: Any, row_values: Optional[Tuple] = None) -> None:
        typ = m.get("type") or "gauge"
        scale = float(m.get("scale") or 1)
        value, text = None, None
        if typ == "text":
            text = _t(raw_value)[:500]
        elif typ == "enum":
            code = str(int(raw_value)) if isinstance(raw_value, (int, float)) else _t(raw_value)
            text = str((m.get("map") or {}).get(code, code))
            value = _f(raw_value)
        elif typ == "ticks":
            n = _f(raw_value)
            value = round(n / 100 / 86400, 2) if n is not None else None
        elif typ == "ratio":
            num, den = row_values or (None, None)
            num, den = _f(num), _f(den)
            if num is None or den is None or num < 0:
                return
            if m.get("den_plus_num"):
                den = den + num
            if den <= 0:
                return
            value = round(num / den * scale, 2)
        elif typ == "rate":
            n = _f(raw_value)
            if n is None:
                return
            prev = new_state.get(key)
            new_state[key] = {"v": n, "ts": now.timestamp()}
            if not prev:
                value = None
            else:
                dt = now.timestamp() - float(prev["ts"])
                delta = n - float(prev["v"])
                if delta < 0:
                    delta += 2 ** 64 if max(n, float(prev["v"])) > 2 ** 32 else 2 ** 32
                value = round(delta / dt * (8 if m.get("bits") else 1) * scale, 2) if dt > 0 else None
        else:
            n = _f(raw_value)
            value = round(n * scale, 3) if n is not None else None
            if value is None and raw_value is not None:
                text = _t(raw_value)[:500]
        status = _status(m, value, raw_value)
        out.append({"key": key[:255], "label": label[:255], "group": (m.get("group") or "")[:64],
                    "value": value, "text": text, "unit": m.get("unit") or "", "status": status,
                    "alert": m.get("alert", True) is not False and typ != "text",
                    "history": bool(m.get("history")) and value is not None})

    for t in templates:
        for m0 in t.get("metrics") or []:
            m = _apply_override(m0, overrides)
            table = m.get("table")
            if not table:
                if m.get("type") == "ratio":
                    emit(m, m["key"], m.get("label", m["key"]), None,
                         (values.get(str(m.get("num", "")).strip(".")), values.get(str(m.get("den", "")).strip("."))))
                else:
                    v = values.get(str(m.get("oid", "")).strip("."))
                    if v is not None:
                        emit(m, m["key"], m.get("label", m["key"]), v)
                continue
            labels = _rows(raw, table.get("label")) or _rows(raw, table.get("label_fallback"))
            exclude = re.compile(table["exclude"]) if table.get("exclude") else None
            limit = int(table.get("max_rows") or 64)
            if m.get("type") == "ratio":
                nums, dens = _rows(raw, table.get("num")), _rows(raw, table.get("den"))
                rows = [(idx, None, (nums[idx], dens.get(idx))) for idx in nums]
            else:
                col = _rows(raw, table.get("column")) or _rows(raw, table.get("column_fallback"))
                rows = [(idx, v, None) for idx, v in col.items()]
            rows = [r for r in rows if not (exclude and exclude.search(_t(labels.get(r[0], ""))))][:limit]
            if m.get("aggregate") in ("avg", "max") and rows:
                nums = [x for x in (_f(v) for _, v, _ in rows) if x is not None]
                if nums:
                    agg = sum(nums) / len(nums) if m["aggregate"] == "avg" else max(nums)
                    emit({**m, "type": "gauge"}, m["key"], m.get("label", m["key"]), agg)
                continue
            for idx, v, pair in rows:
                name = _t(labels.get(idx)) or idx
                emit(m, f"{m['key']}:{idx}", f"{m.get('label', m['key'])} {name}".strip(), v, pair)
    return out, new_state


# =============================================================================
# Speichern und Melden
# =============================================================================
async def _alert(db: AsyncSession, device: SnmpDevice, kind: str, severity: str, message: str) -> None:
    existing = (await db.execute(select(RmmAlert).where(
        RmmAlert.agent_id.is_(None), RmmAlert.kind == kind, RmmAlert.active.is_(True)))).scalar_one_or_none()
    if existing is not None:
        existing.message, existing.severity = message[:512], severity
        return
    db.add(RmmAlert(agent_id=None, kind=kind, subject=device.name,
                    severity=severity, message=message[:512]))
    await alerts.dispatch("snmp_alert", f"RIMPERIO: {device.name}", message,
                          level="critical" if severity == "critical" else "warning",
                          key=f"snmp:{device.id}:{kind}",
                          detail={"device_id": device.id, "host": device.host, "kind": kind})
    if severity == "critical":
        await notify.push(db, category="snmp", level="critical", title=f"{device.name}: {message}"[:255],
                          body=f"{device.host} · {device.site}", link=f"/rmm/snmp/{device.id}",
                          data={"device_id": device.id}, commit=False)


async def _resolve(db: AsyncSession, device: SnmpDevice, kind: str) -> None:
    existing = (await db.execute(select(RmmAlert).where(
        RmmAlert.agent_id.is_(None), RmmAlert.kind == kind[:32], RmmAlert.active.is_(True)))).scalar_one_or_none()
    if existing is None:
        return
    existing.active, existing.resolved_at = False, datetime.utcnow()
    alerts.forget(f"snmp:{device.id}:{kind}")
    await alerts.dispatch("recovered", f"RIMPERIO: {device.name}", f"Behoben: {existing.message}",
                          level="info", key=f"snmp:{device.id}:{kind}:ok")


def _alert_kind(device_id: int, key: str) -> str:
    """rmm_alerts.kind ist 32 Zeichen lang - lange Schluessel werden verkuerzt."""
    kind = f"snmp:{device_id}:{key}"
    if len(kind) <= 32:
        return kind
    return f"snmp:{device_id}:" + hashlib.sha1(key.encode()).hexdigest()[:32 - len(f"snmp:{device_id}:")]


async def store(db: AsyncSession, device: SnmpDevice, raw: dict, catalog: Dict[str, dict]) -> None:
    now = datetime.utcnow()
    device.last_poll_at = now
    device.rtt_ms = raw.get("rtt_ms")
    fails = int((device.state or {}).get("_fails", 0))
    if not raw.get("ok"):
        fails += 1
        device.state = {**(device.state or {}), "_fails": fails}
        device.last_error = (raw.get("error") or "keine Antwort")[:2000]
        if fails >= DOWN_AFTER_FAILURES:
            if device.status != "down":
                journal.fire(category="rmm", event="snmp.down", level="warning", target=device.name,
                             message=f"SNMP-Gerät {device.name} ({device.host}) antwortet nicht: {device.last_error}")
            device.status = "down"
            await _alert(db, device, _alert_kind(device.id, "down"), "critical",
                         f"{device.name} antwortet nicht per SNMP ({device.last_error[:120]})")
        await db.commit()
        return
    device.last_ok_at, device.last_error = now, None
    device.system = {**(device.system or {}), **_system(raw.get("values") or {})}
    await _resolve(db, device, _alert_kind(device.id, "down"))
    metrics, state = evaluate(raw, device_templates(device, catalog), device.overrides or {},
                              {k: v for k, v in (device.state or {}).items() if k != "_fails"}, now)
    device.state = {**state, "_fails": 0}
    existing = {m.key: m for m in (await db.execute(select(SnmpMetric).where(
        SnmpMetric.device_id == device.id))).scalars()}
    seen = set()
    worst, worst_rank = "up", 0
    rank = {"ok": 0, "unknown": 0, "warning": 1, "critical": 2}
    for m in metrics:
        seen.add(m["key"])
        row = existing.get(m["key"])
        if row is None:
            row = SnmpMetric(device_id=device.id, key=m["key"])
            db.add(row)
        row.label, row.group, row.value, row.text = m["label"], m["group"], m["value"], m["text"]
        row.unit, row.status, row.ts = m["unit"], m["status"], now
        if m["history"]:
            db.add(SnmpSample(device_id=device.id, key=m["key"], ts=now, value=float(m["value"])))
        kind = _alert_kind(device.id, m["key"])
        if m["alert"] and m["status"] in ("warning", "critical"):
            shown = m["text"] if m["text"] else f"{m['value']} {m['unit']}".strip()
            await _alert(db, device, kind, m["status"], f"{device.name}: {m['label']} = {shown}")
            if rank[m["status"]] > worst_rank:
                worst, worst_rank = m["status"], rank[m["status"]]
        elif m["status"] == "ok":
            await _resolve(db, device, kind)
    for key, row in existing.items():
        if key not in seen:
            await db.delete(row)
    device.status = worst
    await db.commit()


# =============================================================================
# Agent als Poller
# =============================================================================
async def tasks_for_agent(db: AsyncSession, agent: Agent) -> List[dict]:
    rows = (await db.execute(select(SnmpDevice).where(
        SnmpDevice.enabled.is_(True), SnmpDevice.poller == agent.agent_uid))).scalars().all()
    if not rows:
        return []
    catalog = await all_templates(db)
    tasks = []
    for d in rows:
        cred = await db.get(SnmpCredential, d.credential_id) if d.credential_id else None
        kw = client_kwargs(cred, d.port)
        gets, walks = needed(device_templates(d, catalog))
        task = {"device_id": d.id, "host": d.host, "port": kw["port"], "version": kw["version"],
                "community": kw.get("community", ""), "user": kw.get("user", ""),
                "auth_proto": kw.get("auth_proto", "none"), "auth_pass": kw.get("auth_pass", ""),
                "priv_proto": kw.get("priv_proto", "none"), "priv_pass": kw.get("priv_pass", ""),
                "context": kw.get("context", ""), "timeout": 3, "retries": 1,
                "interval": max(30, d.interval_sec or 300), "gets": gets, "walks": walks, "max_rows": 2000}
        force = str((d.state or {}).get("_force", ""))
        task["revision"] = hashlib.sha1((json.dumps(task, sort_keys=True) + force).encode()).hexdigest()[:12]
        tasks.append(task)
    return tasks


async def ingest_agent_results(db: AsyncSession, agent: Agent, items: List[dict]) -> int:
    catalog = await all_templates(db)
    done = 0
    for item in items or []:
        try:
            device_id = int(item.get("device_id"))
        except (TypeError, ValueError):
            continue
        d = await db.get(SnmpDevice, device_id)
        if d is None or d.poller != agent.agent_uid:
            continue
        raw = {"ok": bool(item.get("ok")), "error": item.get("error") or "",
               "rtt_ms": item.get("rtt_ms"), "values": item.get("values") or {},
               "walks": item.get("walks") or {}}
        await store(db, d, raw, catalog)
        done += 1
    return done


# =============================================================================
# Takt
# =============================================================================
async def poll_device(db: AsyncSession, d: SnmpDevice, catalog: Dict[str, dict]) -> dict:
    cred = await db.get(SnmpCredential, d.credential_id) if d.credential_id else None
    gets, walks = needed(device_templates(d, catalog))
    try:
        kw = client_kwargs(cred, d.port)
        raw = await poll_server(d.host, kw, gets, walks)
    except snmp.SnmpError as exc:
        raw = {"ok": False, "error": str(exc), "values": {}, "walks": {}, "rtt_ms": None}
    await store(db, d, raw, catalog)
    return raw


async def tick() -> None:
    now = datetime.utcnow()
    async with async_session() as db:
        rows = (await db.execute(select(SnmpDevice).where(
            SnmpDevice.enabled.is_(True), SnmpDevice.poller == "server"))).scalars().all()
        due = [d for d in rows if d.last_poll_at is None
               or (now - d.last_poll_at).total_seconds() >= max(30, d.interval_sec or 300)]
        if not due:
            return
        catalog = await all_templates(db)
        ids = [d.id for d in due[:64]]
    gate = asyncio.Semaphore(16)

    async def one(device_id: int) -> None:
        async with gate:
            async with async_session() as db:
                d = await db.get(SnmpDevice, device_id)
                if d is not None:
                    await poll_device(db, d, catalog)

    await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)


async def cleanup() -> None:
    async with async_session() as db:
        await db.execute(delete(SnmpSample).where(
            SnmpSample.ts < datetime.utcnow() - timedelta(days=SAMPLE_RETENTION_DAYS)))
        await db.commit()


async def loop() -> None:
    await asyncio.sleep(20)
    last_cleanup = datetime.min
    while True:
        try:
            await tick()
            if datetime.utcnow() - last_cleanup > timedelta(hours=6):
                last_cleanup = datetime.utcnow()
                await cleanup()
        except Exception as exc:                                    # defensiv
            logger.warning("SNMP-Takt: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)
