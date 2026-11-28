# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Abrechnung je Gerät: Preise, Monatsabrechnung, Export, API
# ==============================================================================
"""
Abrechnung für Systemhäuser: Was kostet ein überwachtes Gerät im Monat?

Die Preise legt das Systemhaus komplett selbst fest - RIMPERIO rechnet nur.
Ein Preis wird in dieser Reihenfolge gesucht, der erste Treffer gilt:

  1. eigener Preis am Gerät                       (license_devices.price_override)
  2. Preis der Geräteklasse beim Mandanten         (tenants.<m>.class_prices)
  3. Standardpreis des Mandanten                   (tenants.<m>.default_price)
  4. Preis der Geräteklasse                        (class_prices)
  5. Standardpreis                                 (default_price)

Gezählt wird aus `license_device_days` (ein Eintrag je Gerät und aktivem Tag).
Zählweisen (`count_method`):

  month    volles Gerät, wenn es im Monat an mindestens `min_days` Tagen aktiv
           war (min_days = 1: jeder angefangene Monat zählt)
  prorata  anteilig: aktive Tage / Tage im Monat
  peak     Höchststand: die meisten gleichzeitig aktiven Geräte an einem Tag

Je Mandant kommen dazu: Inklusiv-Geräte (`free_devices`, abgezogen bei den
günstigsten zuerst), Rabatt in Prozent, Grundgebühr, Mindestbetrag, Steuer.

Am Monatsersten wird der Vormonat festgeschrieben (`billing_statements`) -
spätere Preisänderungen ändern eine gestellte Rechnung nicht mehr.
"""

import calendar
import copy
import csv
import hashlib
import hmac
import io
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import journal
from .licensing import (DEFAULT_RELEASE_AFTER_DAYS, DEFAULT_TENANT, DEVICE_CLASSES, _iso,
                        get_setting, has_feature, put_setting)
from .models import BillingStatement, LicenseDevice, LicenseDeviceDay

CONFIG_KEY = "billing_config"
API_KEYS_KEY = "billing_api_keys"
KEEP_DAYS = 800                     # gut zwei Jahre Nachweis
METHODS = {"month": "je angefangenem Monat (ab Mindesttagen)",
           "prorata": "anteilig nach aktiven Tagen",
           "peak": "Höchststand im Monat"}

DEFAULT_CONFIG: Dict[str, Any] = {
    "currency": "EUR",
    "count_method": "month",
    "min_days": 1,
    "release_after_days": DEFAULT_RELEASE_AFTER_DAYS,
    "default_price": 0.0,
    "class_prices": {},
    "class_billable": {key: True for key in DEVICE_CLASSES},
    "tax_percent": 0.0,
    # Grundgebuehr je Mandant und Monat - fuer jeden angelegten Mandanten
    # (app/tenancy.py), auch ohne Geraete. Je Mandant ueberschreibbar.
    "tenant_fee": 0.0,
    "tenants": {},
}

TENANT_DEFAULTS: Dict[str, Any] = {
    "display_name": "", "customer_no": "", "default_price": None, "class_prices": {},
    # flat_fee None = Standard (tenant_fee bei echten Mandanten, sonst 0)
    "flat_fee": None, "minimum_fee": 0.0, "discount_percent": 0.0, "free_devices": 0,
    "billable": True, "note": "",
}


# ==============================================================================
# Einstellungen
# ==============================================================================
async def get_config(db: AsyncSession) -> dict:
    stored = await get_setting(db, CONFIG_KEY, {})
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key, val in stored.items():
        if key in cfg:
            cfg[key] = val
    cfg["class_billable"] = {**DEFAULT_CONFIG["class_billable"], **(cfg.get("class_billable") or {})}
    cfg["tenants"] = {name: {**TENANT_DEFAULTS, **(t or {})} for name, t in (cfg.get("tenants") or {}).items()}
    return cfg


def _money(value: Any, label: str, allow_none: bool = False) -> Optional[float]:
    if value in (None, ""):
        if allow_none:
            return None
        value = 0
    try:
        out = round(float(value), 4)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: Zahl erwartet.")
    if out < 0 or out > 1_000_000:
        raise ValueError(f"{label}: zwischen 0 und 1.000.000.")
    return out


def _prices(values: Any, label: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for cls, price in (values or {}).items():
        if cls not in DEVICE_CLASSES:
            raise ValueError(f"Unbekannte Geräteklasse '{cls}'.")
        if price in (None, ""):
            continue
        out[cls] = _money(price, f"{label} {DEVICE_CLASSES[cls]}")
    return out


def clean_config(values: dict) -> dict:
    """Prüft eine neue Preis-Einstellung. Wirft ValueError mit Klartext."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cur = str(values.get("currency") or "EUR").strip().upper()
    if not cur.isalpha() or len(cur) != 3:
        raise ValueError("Währung als Kürzel mit drei Buchstaben (z. B. EUR).")
    cfg["currency"] = cur
    method = values.get("count_method", "month")
    if method not in METHODS:
        raise ValueError("Zählweise muss month, prorata oder peak sein.")
    cfg["count_method"] = method
    cfg["min_days"] = max(1, min(31, int(values.get("min_days") or 1)))
    cfg["release_after_days"] = max(1, min(365, int(values.get("release_after_days")
                                                     or DEFAULT_RELEASE_AFTER_DAYS)))
    cfg["default_price"] = _money(values.get("default_price"), "Standardpreis")
    cfg["class_prices"] = _prices(values.get("class_prices"), "Preis")
    billable = values.get("class_billable") or {}
    cfg["class_billable"] = {cls: bool(billable.get(cls, True)) for cls in DEVICE_CLASSES}
    tax = _money(values.get("tax_percent"), "Steuersatz")
    if tax > 100:
        raise ValueError("Steuersatz höchstens 100 %.")
    cfg["tax_percent"] = tax
    cfg["tenant_fee"] = _money(values.get("tenant_fee"), "Grundgebühr je Mandant")
    tenants: Dict[str, dict] = {}
    for name, t in (values.get("tenants") or {}).items():
        name = str(name).strip()[:128]
        if not name:
            continue
        t = t or {}
        discount = _money(t.get("discount_percent"), f"{name}: Rabatt")
        if discount > 100:
            raise ValueError(f"{name}: Rabatt höchstens 100 %.")
        tenants[name] = {
            "display_name": str(t.get("display_name") or "")[:200],
            "customer_no": str(t.get("customer_no") or "")[:64],
            "default_price": _money(t.get("default_price"), f"{name}: Standardpreis", allow_none=True),
            "class_prices": _prices(t.get("class_prices"), f"{name}: Preis"),
            "flat_fee": _money(t.get("flat_fee"), f"{name}: Grundgebühr", allow_none=True),
            "minimum_fee": _money(t.get("minimum_fee"), f"{name}: Mindestbetrag"),
            "discount_percent": discount,
            "free_devices": max(0, min(100000, int(t.get("free_devices") or 0))),
            "billable": bool(t.get("billable", True)),
            "note": str(t.get("note") or "")[:500],
        }
    cfg["tenants"] = tenants
    return cfg


async def save_config(db: AsyncSession, values: dict, actor: str) -> dict:
    cfg = clean_config(values)
    await put_setting(db, CONFIG_KEY, cfg, "Abrechnung: Preise je Gerät, Mandant und Klasse")
    await journal.record(db, category="license", event="billing.config", level="notice",
                         message=(f"Abrechnungspreise geändert: Standard {cfg['default_price']} "
                                  f"{cfg['currency']}, Zählweise {METHODS[cfg['count_method']]}, "
                                  f"{len(cfg['tenants'])} Mandant(en) mit eigenen Preisen."),
                         actor=actor, commit=False)
    await db.commit()
    return cfg


def unit_price(cfg: dict, tenant: str, cls: str, override: Optional[float] = None) -> float:
    if override is not None:
        return float(override)
    t = (cfg.get("tenants") or {}).get(tenant) or {}
    if cls in (t.get("class_prices") or {}):
        return float(t["class_prices"][cls])
    if t.get("default_price") is not None:
        return float(t["default_price"])
    if cls in (cfg.get("class_prices") or {}):
        return float(cfg["class_prices"][cls])
    return float(cfg.get("default_price") or 0)


# ==============================================================================
# Monatsabrechnung
# ==============================================================================
def month_bounds(month: str) -> Tuple[date, date]:
    try:
        year, mon = (int(p) for p in month.split("-"))
        first = date(year, mon, 1)
    except (ValueError, TypeError):
        raise ValueError("Monat im Format JJJJ-MM angeben.")
    return first, first.replace(day=calendar.monthrange(year, mon)[1])


def previous_month(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def build_report(month: str, day_rows: List[Tuple[date, str, str, str]], devices: Dict[str, dict],
                 cfg: dict, today: Optional[date] = None,
                 tenant_names: Optional[List[str]] = None) -> dict:
    """Rechnet die Abrechnung eines Monats - rein rechnerisch, ohne Datenbank.

    day_rows:     (Tag, Geräteschlüssel, Mandant, Klasse) - nur abrechenbare Tage.
    devices:      Geräteschlüssel -> {"name", "price_override"}
    tenant_names: angelegte Mandanten (app/tenancy.py). Sie stehen auch ohne
                  Geräte auf der Abrechnung (Grundgebühr, Mindestbetrag), und
                  für sie gilt die Grundgebühr je Mandant (`tenant_fee`).
    """
    first, last = month_bounds(month)
    today = today or datetime.utcnow().date()
    days_in_month = (last - first).days + 1
    method, min_days = cfg["count_method"], int(cfg.get("min_days") or 1)

    # (Mandant, Gerät) -> aktive Tage und zuletzt gesehene Klasse
    per: Dict[Tuple[str, str], dict] = {}
    for day, key, tenant, cls in sorted(day_rows):
        tenant = tenant or DEFAULT_TENANT
        entry = per.setdefault((tenant, key), {"days": set(), "cls": cls})
        entry["days"].add(day)
        entry["cls"] = cls or "other"

    tenants: Dict[str, dict] = {}
    for (tenant, key), entry in per.items():
        info = devices.get(key) or {}
        price = unit_price(cfg, tenant, entry["cls"], info.get("price_override"))
        n_days = len(entry["days"])
        if method == "prorata":
            qty = round(n_days / days_in_month, 4)
        elif method == "month":
            qty = 1.0 if n_days >= min_days else 0.0
        else:
            qty = None                                  # peak: erst je Gruppe
        tenants.setdefault(tenant, []).append({
            "device_key": key, "name": info.get("name") or key, "device_class": entry["cls"],
            "class_label": DEVICE_CLASSES.get(entry["cls"], entry["cls"]), "days": n_days,
            "active_days": sorted(d.isoformat() for d in entry["days"]),
            "unit_price": price, "quantity": qty})

    real = set(tenant_names or [])
    for name in real:
        tenants.setdefault(name, [])

    out_tenants = []
    excluded = []
    totals = {"devices": 0, "net": 0.0, "tax": 0.0, "gross": 0.0}
    for tenant, devs in tenants.items():
        tcfg = {**TENANT_DEFAULTS, **((cfg.get("tenants") or {}).get(tenant) or {})}
        if not tcfg.get("billable", True):
            excluded.append(tenant)
            continue
        groups: Dict[Tuple[str, float], dict] = {}
        for d in devs:
            g = groups.setdefault((d["device_class"], d["unit_price"]), {
                "device_class": d["device_class"], "class_label": d["class_label"],
                "unit_price": d["unit_price"], "quantity": 0.0, "devices": []})
            g["devices"].append(d)
        for g in groups.values():
            if method == "peak":
                per_day: Dict[str, int] = {}
                for d in g["devices"]:
                    for day in d["active_days"]:
                        per_day[day] = per_day.get(day, 0) + 1
                g["quantity"] = float(max(per_day.values(), default=0))
            else:
                g["quantity"] = round(sum(d["quantity"] for d in g["devices"]), 4)
        lines = sorted(groups.values(), key=lambda g: (g["unit_price"], g["device_class"]))
        free = int(tcfg.get("free_devices") or 0)
        free_used = 0.0
        for g in lines:                                 # günstigste zuerst
            if free <= free_used:
                break
            take = min(g["quantity"], free - free_used)
            g["free"] = take
            g["quantity"] = round(g["quantity"] - take, 4)
            free_used += take
        for g in lines:
            g.setdefault("free", 0.0)
            g["amount"] = round(g["quantity"] * g["unit_price"], 2)
            g["device_count"] = len(g["devices"])
            for d in g["devices"]:
                d.pop("active_days", None)
        subtotal = round(sum(g["amount"] for g in lines), 2)
        discount = round(subtotal * float(tcfg.get("discount_percent") or 0) / 100, 2)
        own_flat = tcfg.get("flat_fee")
        if own_flat is None:
            own_flat = cfg.get("tenant_fee") if tenant in real else 0
        flat = round(float(own_flat or 0), 2)
        net = round(subtotal - discount + flat, 2)
        minimum = round(float(tcfg.get("minimum_fee") or 0), 2)
        minimum_applied = (bool(devs) or tenant in real) and net < minimum
        if minimum_applied:
            net = minimum
        if not devs and not net:
            continue                                    # Mandant ohne Geräte und ohne Gebühr
        tax = round(net * float(cfg.get("tax_percent") or 0) / 100, 2)
        out_tenants.append({
            "tenant": tenant, "display_name": tcfg.get("display_name") or tenant,
            "customer_no": tcfg.get("customer_no") or "", "device_count": len(devs),
            "lines": [{k: v for k, v in g.items() if k != "devices"} | {"devices": g["devices"]}
                      for g in lines],
            "free_devices": free, "free_applied": round(free_used, 4),
            "subtotal": subtotal, "discount_percent": float(tcfg.get("discount_percent") or 0),
            "discount": discount, "flat_fee": flat, "minimum_fee": minimum,
            "minimum_applied": minimum_applied, "net": net, "tax": tax, "gross": round(net + tax, 2)})
        totals["devices"] += len(devs)
        totals["net"] = round(totals["net"] + net, 2)
        totals["tax"] = round(totals["tax"] + tax, 2)
        totals["gross"] = round(totals["gross"] + net + tax, 2)
    out_tenants.sort(key=lambda t: t["display_name"].lower())
    return {"month": month, "from": first.isoformat(), "to": last.isoformat(),
            "partial": today <= last, "currency": cfg["currency"], "count_method": method,
            "count_method_label": METHODS[method], "min_days": min_days,
            "tax_percent": float(cfg.get("tax_percent") or 0), "tenants": out_tenants,
            "excluded_tenants": sorted(excluded), "totals": totals,
            "generated_at": _iso(datetime.utcnow())}


async def compute(db: AsyncSession, month: str, cfg: Optional[dict] = None) -> dict:
    first, last = month_bounds(month)
    cfg = cfg or await get_config(db)
    rows = (await db.execute(
        select(LicenseDeviceDay.day, LicenseDeviceDay.device_key, LicenseDeviceDay.tenant,
               LicenseDeviceDay.device_class)
        .where(LicenseDeviceDay.day >= first, LicenseDeviceDay.day <= last,
               LicenseDeviceDay.billable.is_(True)))).all()
    keys = {r[1] for r in rows}
    devices: Dict[str, dict] = {}
    if keys:
        for d in (await db.execute(select(LicenseDevice).where(LicenseDevice.device_key.in_(keys)))).scalars():
            # Wer gerade "nicht abrechnen" hat, fällt auch rückwirkend heraus.
            devices[d.device_key] = {"name": d.name, "price_override": d.price_override,
                                     "billable": d.billable}
    class_billable = cfg.get("class_billable") or {}
    rows = [tuple(r) for r in rows
            if devices.get(r[1], {}).get("billable", True) and class_billable.get(r[3], True)]
    from .licensing import real_tenants
    return build_report(month, rows, devices, cfg, tenant_names=await real_tenants(db))


async def report(db: AsyncSession, month: str, live: bool = False) -> dict:
    """Festgeschriebene Abrechnung, wenn es eine gibt - sonst frisch gerechnet."""
    if not live:
        st = (await db.execute(select(BillingStatement).where(BillingStatement.month == month))
              ).scalar_one_or_none()
        if st is not None:
            data = dict(st.data or {})
            data["statement"] = {"created_at": _iso(st.created_at), "created_by": st.created_by}
            return data
    data = await compute(db, month)
    data["statement"] = None
    return data


async def close_month(db: AsyncSession, month: str, actor: str) -> dict:
    """Monat festschreiben (oder neu festschreiben)."""
    _, last = month_bounds(month)
    if last >= datetime.utcnow().date():
        raise ValueError("Nur abgeschlossene Monate lassen sich festschreiben.")
    data = await compute(db, month)
    st = (await db.execute(select(BillingStatement).where(BillingStatement.month == month))
          ).scalar_one_or_none()
    if st is None:
        db.add(BillingStatement(month=month, data=data, created_by=actor))
    else:
        st.data, st.created_by, st.created_at = data, actor, datetime.utcnow()
    await journal.record(db, category="license", event="billing.closed", level="notice",
                         message=(f"Abrechnung {month} festgeschrieben: {data['totals']['devices']} Gerät(e), "
                                  f"{len(data['tenants'])} Mandant(en), netto {data['totals']['net']:.2f} "
                                  f"{data['currency']}."),
                         actor=actor, commit=False)
    await db.commit()
    data["statement"] = {"created_at": _iso(datetime.utcnow()), "created_by": actor}
    return data


async def statements(db: AsyncSession) -> List[dict]:
    rows = (await db.execute(select(BillingStatement).order_by(BillingStatement.month.desc()))).scalars().all()
    return [{"month": s.month, "created_at": _iso(s.created_at), "created_by": s.created_by,
             "devices": (s.data or {}).get("totals", {}).get("devices", 0),
             "net": (s.data or {}).get("totals", {}).get("net", 0),
             "gross": (s.data or {}).get("totals", {}).get("gross", 0),
             "currency": (s.data or {}).get("currency", "")} for s in rows]


async def daily(db: AsyncSession) -> None:
    """Einmal am Tag: alten Nachweis aufräumen, Vormonat festschreiben."""
    cutoff = datetime.utcnow().date() - timedelta(days=KEEP_DAYS)
    await db.execute(delete(LicenseDeviceDay).where(LicenseDeviceDay.day < cutoff))
    await db.commit()
    if not await has_feature(db, "billing"):
        return
    month = previous_month(datetime.utcnow().date())
    exists = (await db.execute(select(BillingStatement.id).where(BillingStatement.month == month))).first()
    if exists is None:
        await close_month(db, month, "system")


# ==============================================================================
# Export
# ==============================================================================
def _num(value: Any, places: int = 2) -> str:
    """Zahl für deutsches Excel: Komma statt Punkt."""
    return f"{float(value or 0):.{places}f}".replace(".", ",")


def to_csv(data: dict, detail: bool = False) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    cur, month = data["currency"], data["month"]
    if detail:
        w.writerow(["Monat", "Mandant", "Kundennummer", "Gerät", "Schlüssel", "Geräteklasse",
                    "Aktive Tage", "Einzelpreis", "Menge", "Betrag", "Währung"])
        for t in data["tenants"]:
            for line in t["lines"]:
                for d in line["devices"]:
                    qty = d.get("quantity")
                    w.writerow([month, t["display_name"], t["customer_no"], d["name"], d["device_key"],
                                d["class_label"], d["days"], _num(d["unit_price"]),
                                "" if qty is None else _num(qty, 4),
                                "" if qty is None else _num(qty * d["unit_price"]), cur])
    else:
        w.writerow(["Monat", "Mandant", "Kundennummer", "Position", "Einzelpreis", "Menge",
                    "Inklusiv", "Betrag", "Währung"])
        for t in data["tenants"]:
            base = [month, t["display_name"], t["customer_no"]]
            for line in t["lines"]:
                w.writerow(base + [line["class_label"], _num(line["unit_price"]), _num(line["quantity"], 4),
                                   _num(line.get("free", 0), 4), _num(line["amount"]), cur])
            if t["discount"]:
                w.writerow(base + [f"Rabatt {_num(t['discount_percent'], 1)} %", "", "", "",
                                   _num(-t["discount"]), cur])
            if t["flat_fee"]:
                w.writerow(base + ["Grundgebühr", "", "", "", _num(t["flat_fee"]), cur])
            if t["minimum_applied"]:
                w.writerow(base + ["Mindestbetrag angewendet", "", "", "", _num(t["minimum_fee"]), cur])
            w.writerow(base + ["Summe netto", "", "", "", _num(t["net"]), cur])
            if t["tax"]:
                w.writerow(base + [f"Steuer {_num(data['tax_percent'], 1)} %", "", "", "", _num(t["tax"]), cur])
                w.writerow(base + ["Summe brutto", "", "", "", _num(t["gross"]), cur])
    # BOM: Excel erkennt sonst die Umlaute nicht.
    return "﻿" + buf.getvalue()


# ==============================================================================
# API-Schlüssel für externe Abrechnungssysteme
# ==============================================================================
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_key(entry: dict) -> dict:
    return {k: entry.get(k) for k in ("id", "name", "prefix", "created_at", "created_by", "last_used_at")}


async def list_api_keys(db: AsyncSession) -> List[dict]:
    return [_public_key(e) for e in await get_setting(db, API_KEYS_KEY, [])]


async def create_api_key(db: AsyncSession, name: str, actor: str) -> Tuple[str, dict]:
    plain = "rbk_" + secrets.token_urlsafe(32)
    entry = {"id": secrets.token_hex(6), "name": (name or "Abrechnung")[:100], "prefix": plain[:10],
             "hash": _hash(plain), "created_at": _iso(datetime.utcnow()), "created_by": actor,
             "last_used_at": None}
    keys = await get_setting(db, API_KEYS_KEY, [])
    keys.append(entry)
    await put_setting(db, API_KEYS_KEY, keys, "Abrechnung: Schlüssel für die Abrechnungs-API (nur Prüfsummen)")
    await journal.record(db, category="token", event="billing.apikey.created", level="notice",
                         message=f"Schlüssel für die Abrechnungs-API angelegt: {entry['name']} ({entry['prefix']}…)",
                         actor=actor, commit=False)
    await db.commit()
    return plain, _public_key(entry)


async def revoke_api_key(db: AsyncSession, key_id: str, actor: str) -> bool:
    keys = await get_setting(db, API_KEYS_KEY, [])
    left = [k for k in keys if k.get("id") != key_id]
    if len(left) == len(keys):
        return False
    await put_setting(db, API_KEYS_KEY, left, "Abrechnung: Schlüssel für die Abrechnungs-API (nur Prüfsummen)")
    await journal.record(db, category="token", event="billing.apikey.revoked", level="notice",
                         message=f"Schlüssel für die Abrechnungs-API widerrufen ({key_id}).",
                         actor=actor, commit=False)
    await db.commit()
    return True


async def resolve_api_key(db: AsyncSession, token: str) -> Optional[dict]:
    if not token or not token.startswith("rbk_"):
        return None
    digest = _hash(token)
    keys = await get_setting(db, API_KEYS_KEY, [])
    for entry in keys:
        if hmac.compare_digest(str(entry.get("hash") or ""), digest):
            now = datetime.utcnow()
            last = entry.get("last_used_at")
            if not last or (now - datetime.fromisoformat(last.rstrip("Z"))).total_seconds() > 60:
                entry["last_used_at"] = _iso(now)
                await put_setting(db, API_KEYS_KEY, keys,
                                  "Abrechnung: Schlüssel für die Abrechnungs-API (nur Prüfsummen)")
                await db.commit()
            return entry
    return None
