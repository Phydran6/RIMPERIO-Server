# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Lizenz: Instanz, signierte Lizenzdatei, Geräte-Kontingent
# ==============================================================================
"""
Lizenzierung von RIMPERIO.

Grundsatz: **Der Server bleibt lizenzfrei.** Wer RIMPERIO selbst betreibt,
bekommt ohne Lizenz die Community-Ausgabe - Logs, Fernwartung, Patchen,
SNMP, alles. Nichts wird abgeschaltet, kein Gerät wird abgewiesen.

Eine Lizenz braucht, wer RIMPERIO gewerblich als Dienst einsetzt (Enterprise,
Systemhaus). Sie schaltet die Enterprise-Funktionen frei (Abrechnung je
Gerät, Abrechnungs-API, später Mandanten) und kennt drei Modelle:

  per_device   je Gerät - optional mit Höchstzahl (`max_devices`). Ohne
               Höchstzahl wird nach tatsächlicher Nutzung abgerechnet.
  per_instance je Instanz - pauschal, beliebig viele Geräte.
  per_tenant   je Mandant - optional mit Höchstzahl (`max_tenants`).

Woher die Lizenz kommt (Einstellung `license_source`):

  github  aus einem dafür vorgesehenen GitHub-Repository: die Datei
          `<pfad>/<instanz-id>.json`. Der Herausgeber steuert dort alles -
          ausstellen, verlängern, Kontingent ändern, widerrufen.
  url     von einem eigenen Lizenz-API-Endpunkt (POST mit Instanz-ID und
          Nutzung, Antwort ist die signierte Lizenz).
  manual  von Hand hochgeladen (ohne Internet).

Die Datei ist mit Ed25519 signiert (Schlüssel in `app/license_keys.py`).
Eine online bezogene Lizenz muss sich regelmäßig bestätigen; ist die Quelle
länger als `grace_days` nicht erreichbar, fällt RIMPERIO auf Community
zurück - die Überwachung läuft auch dann ohne Unterbrechung weiter.

Geräte lizenzieren sich selbst: Jedes angeschlossene Gerät bekommt eine Zeile
in `license_devices` und - solange das Kontingent reicht - einen Platz.
Wer zuerst da war, behält seinen Platz. Ist das Kontingent voll, werden neue
Geräte als "über Kontingent" geführt (gemeldet, aber weiter überwacht).
Für die Monatsabrechnung wird je Gerät jeder aktive Tag festgehalten
(`license_device_days`, siehe app/billing.py).

Mandanten: Gehört ein Gerät einem Mandanten (app/tenancy.py), zählt es für
diesen Mandanten (sein Name). Sonst gilt wie bisher der Standort (`site`).
`tenant_of()` ist die einzige Stelle, die das entscheidet.
"""

import asyncio
import base64
import copy
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import flag_modified

from . import journal
from .config import settings as app_settings
from .database import Base, async_session, engine
from .license_keys import TRUSTED_KEYS
from .models import (LICENSE_TABLES, Agent, LicenseDevice, LicenseDeviceDay, PveHost, Setting,
                     SnmpDevice)

logger = logging.getLogger("rimperio.license")

ENVELOPE_FORMAT = "rimperio-license/1"
INSTANCE_KEY = "license_instance"
SOURCE_KEY = "license_source"
STATE_KEY = "license_state"

LOOP_SECONDS = 300
DEFAULT_TENANT = "Standard"

MODELS = {
    "per_device": "je Gerät",
    "per_instance": "je Instanz",
    "per_tenant": "je Mandant",
}

FEATURES = {
    "billing": "Abrechnung je Gerät (Preise, Monatsabrechnung, Export)",
    "billing_api": "Abrechnungs-API für externe Systeme",
    "tenants": "Mandanten",
}
# Was eine Lizenz freischaltet, wenn sie nichts anderes sagt.
DEFAULT_FEATURES = ["billing", "billing_api", "tenants"]
# Was ohne Lizenz geht. Die Überwachung selbst ist nie eine "Funktion" hier -
# sie ist immer da. Wer Community großzügiger will: hier eintragen.
COMMUNITY_FEATURES: List[str] = []

DEVICE_CLASSES = {
    "windows_server": "Windows-Server",
    "windows_client": "Windows-Arbeitsplatz",
    "linux": "Linux",
    "macos": "macOS",
    "network": "Netzwerkgerät (SNMP)",
    "hypervisor": "Proxmox-Host",
    "log_source": "Log-Quelle (nur Logs)",
    "other": "Sonstiges",
}

STATES = {
    "free": "frei (Community)",
    "licensed": "lizenziert",
    "over_limit": "über Kontingent",
    "excluded": "nicht abgerechnet",
    "inactive": "inaktiv",
    "removed": "entfernt",
}

DEFAULT_SOURCE = {
    "mode": "github",                       # github | url | manual
    "github_repo": os.getenv("RIMPERIO_LICENSE_REPO", "Phydran6/RIMPERIO-Licenses"),
    "github_branch": os.getenv("RIMPERIO_LICENSE_BRANCH", "main"),
    "github_path": os.getenv("RIMPERIO_LICENSE_PATH", "licenses"),
    "url": os.getenv("RIMPERIO_LICENSE_URL", ""),
    "url_token": "",
    "report_usage": True,
    "check_hours": 12,
}

# Wie lange ein Gerät ohne Lebenszeichen seinen Platz behält.
DEFAULT_RELEASE_AFTER_DAYS = 30
ONLINE_WARN_DAYS = 3
EXPIRY_WARN_DAYS = 30

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]{0,200}$")


# ==============================================================================
# Schema
# ==============================================================================
async def ensure_schema() -> None:
    """Tabellen anlegen. Wirft nie."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=LICENSE_TABLES))
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_license_device_days_day ON license_device_days (day)")
        logger.info("Lizenz-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("Lizenz-Schema nicht angelegt: %s", exc)


# ==============================================================================
# Einstellungen
# ==============================================================================
async def get_setting(db: AsyncSession, key: str, default: Any) -> Any:
    row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row is None or row.value is None:
        return copy.deepcopy(default)
    return copy.deepcopy(row.value)


async def put_setting(db: AsyncSession, key: str, value: Any, description: str = "") -> None:
    row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(Setting(key=key, value=value, description=description))
    else:
        row.value = value
        flag_modified(row, "value")


async def instance_id(db: AsyncSession) -> str:
    """Die Kennung dieser Installation. Einmal gewürfelt, danach fest.

    Eine Rücksicherung bringt dieselbe Kennung mit - die Lizenz passt dann
    weiter. Ein Klon (zweiter Server aus derselben Sicherung) hätte dieselbe;
    er bekommt mit "Neue Kennung" eine eigene.
    """
    data = await get_setting(db, INSTANCE_KEY, {})
    iid = str(data.get("instance_id") or "")
    if not re.fullmatch(r"[0-9a-f-]{36}", iid):
        iid = str(uuid.uuid4())
        await put_setting(db, INSTANCE_KEY, {"instance_id": iid,
                                             "created_at": _iso(datetime.utcnow())},
                          "Lizenz: Kennung dieser RIMPERIO-Instanz")
        await db.commit()
    return iid


async def regenerate_instance_id(db: AsyncSession) -> str:
    iid = str(uuid.uuid4())
    await put_setting(db, INSTANCE_KEY, {"instance_id": iid, "created_at": _iso(datetime.utcnow())},
                      "Lizenz: Kennung dieser RIMPERIO-Instanz")
    state = await get_setting(db, STATE_KEY, {})
    state.pop("envelope", None)
    state["origin"] = ""
    await put_setting(db, STATE_KEY, state, "Lizenz: aktuelle Lizenz und letzte Prüfung")
    await db.commit()
    return iid


async def get_source(db: AsyncSession) -> dict:
    cfg = {**DEFAULT_SOURCE, **(await get_setting(db, SOURCE_KEY, {}))}
    return cfg


def public_source(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k != "url_token"}
    out["url_token_set"] = bool(cfg.get("url_token"))
    return out


def clean_source(values: dict, current: dict) -> dict:
    """Prüft eine neue Quellen-Einstellung. Wirft ValueError mit Klartext."""
    cfg = {**DEFAULT_SOURCE, **current}
    mode = str(values.get("mode", cfg["mode"])).strip()
    if mode not in ("github", "url", "manual"):
        raise ValueError("Quelle muss github, url oder manual sein.")
    cfg["mode"] = mode
    for key, rx, label in (("github_repo", _REPO_RE, "Repository (owner/name)"),
                           ("github_branch", _BRANCH_RE, "Branch"),
                           ("github_path", _PATH_RE, "Pfad")):
        if key in values:
            val = str(values[key] or "").strip().strip("/")
            if val and (not rx.fullmatch(val) or ".." in val):
                raise ValueError(f"{label} ist ungültig.")
            cfg[key] = val
    if mode == "github" and not cfg["github_repo"]:
        raise ValueError("Für GitHub bitte ein Repository angeben (owner/name).")
    if "url" in values:
        url = str(values["url"] or "").strip()
        if url and not url.startswith("https://") and not re.match(r"^http://(localhost|127\.0\.0\.1)(:|/|$)", url):
            raise ValueError("Der Lizenz-Endpunkt muss mit https:// beginnen.")
        cfg["url"] = url
    if mode == "url" and not cfg["url"]:
        raise ValueError("Für einen eigenen Lizenz-Endpunkt bitte die Adresse angeben.")
    if values.get("url_token"):
        cfg["url_token"] = str(values["url_token"]).strip()[:500]
    if values.get("clear_url_token"):
        cfg["url_token"] = ""
    if "report_usage" in values:
        cfg["report_usage"] = bool(values["report_usage"])
    if "check_hours" in values:
        try:
            cfg["check_hours"] = max(1, min(168, int(values["check_hours"])))
        except (TypeError, ValueError):
            raise ValueError("Prüfabstand in Stunden (1–168).")
    return cfg


# ==============================================================================
# Signatur
# ==============================================================================
def canonical(obj: Any) -> bytes:
    """Die Bytes, über die signiert wird. Muss mit licensing/rimperio_license.py übereinstimmen."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


def normalize_license(lic: dict) -> dict:
    """Pflichtfelder prüfen, Vorgaben ergänzen. Wirft ValueError."""
    if not isinstance(lic, dict):
        raise ValueError("Lizenzinhalt fehlt.")
    out = dict(lic)
    for field in ("license_id", "instance_id"):
        if not str(out.get(field) or "").strip():
            raise ValueError(f"Feld '{field}' fehlt.")
    model = out.get("model") or "per_device"
    if model not in MODELS:
        raise ValueError(f"Unbekanntes Modell '{model}'.")
    out["model"] = model
    for field in ("max_devices", "max_tenants"):
        val = out.get(field)
        out[field] = None if val in (None, "", 0) else int(val)
        if out[field] is not None and out[field] < 0:
            raise ValueError(f"'{field}' darf nicht negativ sein.")
    try:
        out["valid_from"] = str(_parse_date(out.get("valid_from")) or "") or None
        out["valid_until"] = str(_parse_date(out.get("valid_until")) or "") or None
    except ValueError:
        raise ValueError("Datum im Format JJJJ-MM-TT angeben (valid_from / valid_until).")
    feats = out.get("features")
    out["features"] = sorted({str(f) for f in feats}) if isinstance(feats, list) else list(DEFAULT_FEATURES)
    out["grace_days"] = max(0, int(out.get("grace_days") if out.get("grace_days") is not None else 14))
    out["status"] = str(out.get("status") or "active")
    if out["status"] not in ("active", "suspended", "revoked"):
        raise ValueError("Status muss active, suspended oder revoked sein.")
    out.setdefault("licensee", "")
    return out


def verify_envelope(envelope: Any, keys: Optional[Dict[str, str]] = None) -> Tuple[Optional[dict], str]:
    """(Lizenz, "") bei gültiger Signatur, sonst (None, Grund)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = TRUSTED_KEYS if keys is None else keys
    if isinstance(envelope, (str, bytes)):
        try:
            envelope = json.loads(envelope)
        except ValueError:
            return None, "Keine gültige Lizenzdatei (kein JSON)."
    if not isinstance(envelope, dict) or envelope.get("format") != ENVELOPE_FORMAT:
        return None, f"Keine RIMPERIO-Lizenzdatei (erwartet format = {ENVELOPE_FORMAT})."
    lic, sig, key_id = envelope.get("license"), envelope.get("signature"), envelope.get("key_id")
    if not isinstance(lic, dict) or not isinstance(sig, str):
        return None, "Lizenzdatei unvollständig (license/signature fehlt)."
    if not keys:
        return None, ("In dieser RIMPERIO-Fassung ist kein Lizenz-Schlüssel hinterlegt "
                      "(app/license_keys.py) - Lizenzen können nicht geprüft werden.")
    candidates = [keys[key_id]] if key_id in keys else ([] if key_id else list(keys.values()))
    if not candidates:
        return None, f"Unbekannter Signaturschlüssel '{key_id}'."
    payload = canonical(lic)
    try:
        signature = base64.b64decode(sig, validate=True)
    except ValueError:
        return None, "Signatur ist kein Base64."
    for pub in candidates:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(signature, payload)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        return None, "Signatur ungültig - die Datei wurde verändert oder nicht vom Herausgeber signiert."
    try:
        return normalize_license(lic), ""
    except (ValueError, TypeError) as exc:
        return None, f"Lizenzinhalt ungültig: {exc}"


# ==============================================================================
# Bewertung: was gilt gerade?
# ==============================================================================
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _from_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except ValueError:
        return None


def evaluate(state: dict, iid: str, now: Optional[datetime] = None,
             keys: Optional[Dict[str, str]] = None) -> dict:
    """Aus gespeicherter Lizenz + letzter Prüfung die gültige Ausgabe machen.

    Rein rechnerisch (keine Datenbank, kein Netz) - damit testbar.
    """
    now = now or datetime.utcnow()
    today = now.date()
    out = {"edition": "community", "edition_label": "Community", "model": None, "model_label": "",
           "features": sorted(COMMUNITY_FEATURES), "license": None, "warnings": [], "errors": [],
           "max_devices": None, "max_tenants": None}
    envelope = state.get("envelope")
    if not envelope:
        return out
    lic, err = verify_envelope(envelope, keys)
    if lic is None:
        out["errors"].append(err)
        return out
    out["license"] = lic
    if lic["instance_id"] != iid:
        out["errors"].append("Die Lizenz gehört zu einer anderen Instanz "
                             f"({lic['instance_id']}).")
        return out
    if lic["status"] == "revoked":
        out["errors"].append("Die Lizenz wurde vom Herausgeber widerrufen.")
        return out
    if lic["status"] == "suspended":
        out["errors"].append("Die Lizenz ist vom Herausgeber ausgesetzt.")
        return out
    start, end = _parse_date(lic.get("valid_from")), _parse_date(lic.get("valid_until"))
    if start and today < start:
        out["errors"].append(f"Die Lizenz gilt erst ab {start.isoformat()}.")
        return out
    grace = timedelta(days=lic["grace_days"])
    if end and today > end + grace:
        out["errors"].append(f"Die Lizenz ist am {end.isoformat()} abgelaufen.")
        return out
    if end and today > end:
        out["warnings"].append(f"Die Lizenz ist am {end.isoformat()} abgelaufen - Kulanz bis "
                               f"{(end + grace).isoformat()}.")
    elif end and (end - today).days <= EXPIRY_WARN_DAYS:
        out["warnings"].append(f"Die Lizenz läuft am {end.isoformat()} ab.")
    # Online bezogen: regelmäßig bestätigen lassen. Eine hochgeladene Datei
    # gilt bis zu ihrem Ablaufdatum, ganz ohne Netz.
    if state.get("origin") in ("github", "url"):
        last_ok = _from_iso(state.get("last_ok_at"))
        if last_ok is None or now - last_ok > grace:
            out["errors"].append(
                "Die Lizenz konnte seit "
                + (f"{last_ok:%d.%m.%Y}" if last_ok else "der Installation")
                + f" nicht mehr bei der Lizenzquelle bestätigt werden (Kulanz {lic['grace_days']} Tage).")
            return out
        if now - last_ok > timedelta(days=ONLINE_WARN_DAYS):
            out["warnings"].append(f"Lizenzquelle seit {last_ok:%d.%m.%Y} nicht erreichbar - "
                                   f"Lizenz gilt noch bis {(last_ok + grace):%d.%m.%Y}.")
    out.update({"edition": "enterprise", "edition_label": "Enterprise", "model": lic["model"],
                "model_label": MODELS[lic["model"]],
                "features": sorted(set(lic["features"]) | set(COMMUNITY_FEATURES)),
                "max_devices": lic["max_devices"] if lic["model"] == "per_device" else None,
                "max_tenants": lic["max_tenants"] if lic["model"] == "per_tenant" else None})
    return out


async def current(db: AsyncSession) -> dict:
    """Die gerade gültige Ausgabe samt Instanz-ID."""
    iid = await instance_id(db)
    state = await get_setting(db, STATE_KEY, {})
    result = evaluate(state, iid)
    result["instance_id"] = iid
    return result


async def has_feature(db: AsyncSession, feature: str) -> bool:
    return feature in (await current(db))["features"]


# ==============================================================================
# Lizenz beziehen
# ==============================================================================
def _github_urls(cfg: dict, iid: str) -> List[Tuple[str, dict]]:
    path = "/".join(p for p in (cfg.get("github_path") or "").split("/") if p)
    file = f"{path}/{iid}.json" if path else f"{iid}.json"
    repo, branch = cfg["github_repo"], cfg.get("github_branch") or "main"
    headers = {"User-Agent": "RIMPERIO-License", "Cache-Control": "no-cache"}
    urls = []
    token = (os.getenv("RIMPERIO_LICENSE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
    if token:
        # Privates Repository: nur über die API mit Lesezugriff.
        urls.append((f"https://api.github.com/repos/{repo}/contents/{file}?ref={branch}",
                     {**headers, "Accept": "application/vnd.github.raw+json",
                      "Authorization": f"Bearer {token}"}))
    urls.append((f"https://raw.githubusercontent.com/{repo}/{branch}/{file}", headers))
    return urls


async def fetch(cfg: dict, iid: str, usage: Optional[dict] = None) -> Tuple[Optional[dict], str]:
    """Holt die Lizenzdatei. (Umschlag, "") / (None, "not_found") / (None, Fehler)."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if cfg["mode"] == "github":
                last = ""
                for url, headers in _github_urls(cfg, iid):
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json(), ""
                    last = "not_found" if resp.status_code == 404 else f"GitHub antwortet {resp.status_code}"
                    if resp.status_code not in (401, 403, 404):
                        break
                return None, last
            if cfg["mode"] == "url":
                headers = {"User-Agent": "RIMPERIO-License", "Accept": "application/json"}
                if cfg.get("url_token"):
                    headers["Authorization"] = f"Bearer {cfg['url_token']}"
                body = {"instance_id": iid, "version": app_settings.app_version}
                if cfg.get("report_usage", True) and usage is not None:
                    body["usage"] = usage
                resp = await client.post(cfg["url"], json=body, headers=headers)
                if resp.status_code == 404:
                    return None, "not_found"
                if resp.status_code >= 400:
                    return None, f"Lizenz-Endpunkt antwortet {resp.status_code}"
                data = resp.json()
                # Erlaubt: der Umschlag direkt oder {"envelope": {...}}
                return (data.get("envelope") if isinstance(data, dict) and "envelope" in data else data), ""
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"Lizenzquelle nicht erreichbar: {exc.__class__.__name__}: {exc}"
    return None, "Keine Online-Quelle eingestellt."


async def check_now(db: AsyncSession, actor: str = "system") -> dict:
    """Bei der Quelle nachfragen und das Ergebnis speichern."""
    iid = await instance_id(db)
    cfg = await get_source(db)
    state = await get_setting(db, STATE_KEY, {})
    before = evaluate(state, iid)
    state["last_check_at"] = _iso(datetime.utcnow())
    if cfg["mode"] == "manual":
        state["last_error"] = ""
        await put_setting(db, STATE_KEY, state, "Lizenz: aktuelle Lizenz und letzte Prüfung")
        await db.commit()
        return await _after_change(db, before, actor)

    usage = await usage_snapshot(db) if cfg["mode"] == "url" else None
    envelope, err = await fetch(cfg, iid, usage)
    if envelope is not None:
        lic, verr = verify_envelope(envelope)
        if lic is None:
            state["last_error"] = verr
        elif lic["instance_id"] != iid:
            state["last_error"] = "Die Quelle lieferte eine Lizenz für eine andere Instanz."
        else:
            cur_lic, _ = verify_envelope(state.get("envelope")) if state.get("envelope") else (None, "")
            older = (cur_lic and cur_lic.get("license_id") == lic.get("license_id")
                     and str(lic.get("issued_at") or "") < str(cur_lic.get("issued_at") or ""))
            if older:
                state["last_error"] = "Die Quelle lieferte einen älteren Stand dieser Lizenz - ignoriert."
            else:
                state["envelope"] = envelope
                state["origin"] = cfg["mode"]
                state["last_ok_at"] = state["last_check_at"]
                state["last_error"] = ""
    elif err == "not_found":
        state["last_error"] = f"Für diese Instanz liegt bei der Quelle keine Lizenz ({iid}.json)."
        if state.get("origin") in ("github", "url") and state.get("envelope"):
            state["last_error"] += " Die bisherige Lizenz gilt bis zum Ende der Kulanzzeit."
        elif not state.get("envelope"):
            state["last_error"] += " RIMPERIO läuft als Community-Ausgabe."
    else:
        state["last_error"] = err
    await put_setting(db, STATE_KEY, state, "Lizenz: aktuelle Lizenz und letzte Prüfung")
    await db.commit()
    return await _after_change(db, before, actor)


async def install_envelope(db: AsyncSession, envelope: Any, actor: str) -> dict:
    """Von Hand hochgeladene Lizenz übernehmen. Wirft ValueError."""
    iid = await instance_id(db)
    if isinstance(envelope, (str, bytes)):
        try:
            envelope = json.loads(envelope)
        except ValueError:
            raise ValueError("Keine gültige Lizenzdatei (kein JSON).")
    lic, err = verify_envelope(envelope)
    if lic is None:
        raise ValueError(err)
    if lic["instance_id"] != iid:
        raise ValueError(f"Diese Lizenz ist für die Instanz {lic['instance_id']} ausgestellt, "
                         f"diese hier ist {iid}.")
    state = await get_setting(db, STATE_KEY, {})
    before = evaluate(state, iid)
    state.update({"envelope": envelope, "origin": "upload", "last_error": "",
                  "last_ok_at": _iso(datetime.utcnow()), "installed_at": _iso(datetime.utcnow()),
                  "installed_by": actor})
    await put_setting(db, STATE_KEY, state, "Lizenz: aktuelle Lizenz und letzte Prüfung")
    await db.commit()
    return await _after_change(db, before, actor)


async def remove_license(db: AsyncSession, actor: str) -> dict:
    iid = await instance_id(db)
    state = await get_setting(db, STATE_KEY, {})
    before = evaluate(state, iid)
    state.pop("envelope", None)
    state["origin"] = ""
    await put_setting(db, STATE_KEY, state, "Lizenz: aktuelle Lizenz und letzte Prüfung")
    await db.commit()
    return await _after_change(db, before, actor)


async def _after_change(db: AsyncSession, before: dict, actor: str) -> dict:
    now = await current(db)
    b_lic, n_lic = before.get("license") or {}, now.get("license") or {}
    changed = (before["edition"] != now["edition"]
               or b_lic.get("license_id") != n_lic.get("license_id")
               or b_lic.get("issued_at") != n_lic.get("issued_at"))
    if changed:
        if now["edition"] == "enterprise":
            msg = (f"Lizenz {n_lic.get('license_id')} aktiv: {now['model_label']}"
                   + (f", bis {now['max_devices']} Geräte" if now.get("max_devices") else "")
                   + (f", bis {now['max_tenants']} Mandanten" if now.get("max_tenants") else "")
                   + (f", gültig bis {n_lic['valid_until']}" if n_lic.get("valid_until") else "") + ".")
            level = "notice"
        else:
            msg = "Keine gültige Lizenz - RIMPERIO läuft als Community-Ausgabe." + (
                f" Grund: {now['errors'][0]}" if now["errors"] else "")
            level = "warning" if before["edition"] == "enterprise" else "info"
        await journal.record(db, category="license", event="license.changed", level=level,
                             message=msg, actor=actor,
                             detail={"edition": now["edition"], "license_id": n_lic.get("license_id")})
        # Kontingent sofort neu verteilen.
        await sync_devices(db, lic_state=now)
    return now


# ==============================================================================
# Geräte: erkennen, zählen, Plätze verteilen
# ==============================================================================
async def real_tenants(db: AsyncSession) -> List[str]:
    """Namen der aktiven Mandanten (app/tenancy.py) in Anlage-Reihenfolge.

    Leer, solange die Mandantenfaehigkeit aus ist - dann gelten wie bisher die
    Standorte als Mandanten. Ist sie an, zaehlt die Lizenz "je Mandant" genau
    diese Mandanten (auch ohne Geraete); Geraete des Betreibers belegen keinen
    Mandanten-Platz.
    """
    from . import tenancy
    from .models import Tenant
    if not tenancy.enabled():
        return []
    rows = (await db.execute(select(Tenant.name).where(Tenant.is_active.is_(True)).order_by(Tenant.id)
                             .execution_options(**tenancy.ALL_TENANTS))).scalars().all()
    return [str(n)[:128] for n in rows]


def tenant_of(row: Any, names: Optional[Dict[int, str]] = None) -> str:
    """Zu welchem Mandanten gehört ein Gerät?

    Gehört es einem Mandanten (`tenant_id`, app/tenancy.py), dessen Name aus
    `names` ({id: Name}). Sonst - Betreiber-Geräte oder ohne Mandanten - der
    Standort (`site`).
    """
    for attr in ("tenant_key", "tenant"):
        val = getattr(row, attr, None)
        if val not in (None, ""):
            return str(val)[:128]
    tid = getattr(row, "tenant_id", None)
    if tid is not None:
        return str((names or {}).get(tid) or f"Mandant {tid}")[:128]
    return (str(getattr(row, "site", "") or "").strip() or DEFAULT_TENANT)[:128]


def classify_agent(agent: Any) -> str:
    platform = (getattr(agent, "platform", "") or "").lower()
    os_name = (getattr(agent, "os", "") or "").lower()
    if getattr(agent, "agent_uid", None) or platform:
        if platform == "windows" or "windows" in os_name:
            return "windows_server" if "server" in os_name else "windows_client"
        if platform == "darwin" or "mac" in os_name:
            return "macos"
        return "linux"
    return "log_source"


def _latest(*values: Optional[datetime]) -> Optional[datetime]:
    present = [v for v in values if v]
    return max(present) if present else None


async def discover(db: AsyncSession) -> List[dict]:
    """Alle überwachten Geräte mit Klasse, Mandant und letztem Lebenszeichen.

    Immer über alle Mandanten (ALL_TENANTS) - die Lizenz zählt die ganze
    Instanz, auch wenn der Betreiber gerade auf einen Mandanten umgeschaltet hat.
    """
    from . import tenancy
    from .models import Tenant
    every = tenancy.ALL_TENANTS
    names = {tid: name for tid, name in (await db.execute(
        select(Tenant.id, Tenant.name).execution_options(**every))).all()}
    found: List[dict] = []
    agents = (await db.execute(select(Agent).options(
        defer(Agent.inventory), defer(Agent.docker_inventory), defer(Agent.extra_data))
        .execution_options(**every))).scalars().all()
    for a in agents:
        found.append({"device_key": f"agent:{a.id}", "kind": "agent", "ref_id": a.id,
                      "name": a.hostname or f"Gerät {a.id}", "device_class": classify_agent(a),
                      "tenant": tenant_of(a, names), "last_active": _latest(a.last_seen, a.rmm_last_seen)})
    snmp = (await db.execute(select(SnmpDevice).options(
        defer(SnmpDevice.state), defer(SnmpDevice.custom), defer(SnmpDevice.overrides))
        .execution_options(**every))).scalars().all()
    for s in snmp:
        found.append({"device_key": f"snmp:{s.id}", "kind": "snmp", "ref_id": s.id,
                      "name": s.name or s.host, "device_class": "network", "tenant": tenant_of(s, names),
                      # Abgeschaltet = nicht überwacht = nicht gezählt.
                      "last_active": s.last_poll_at if s.enabled else None})
    hosts = (await db.execute(select(PveHost).options(defer(PveHost.guests))
                              .execution_options(**every))).scalars().all()
    for h in hosts:
        found.append({"device_key": f"pve:{h.id}", "kind": "pve", "ref_id": h.id,
                      "name": h.name or h.url, "device_class": "hypervisor", "tenant": tenant_of(h, names),
                      # Proxmox meldet sich nicht von selbst: angebunden = überwacht.
                      "last_active": datetime.utcnow() if h.enabled else None})
    return found


def counts_for_billing(d: Any, class_billable: Optional[Dict[str, bool]] = None) -> bool:
    """Wird dieses Gerät abgerechnet? Nein, wenn es von Hand ausgenommen ist
    oder seine Geräteklasse in den Preisen auf "nicht abrechnen" steht."""
    cls = getattr(d, "class_override", None) or getattr(d, "device_class", None) or "other"
    return bool(d.billable) and (class_billable or {}).get(cls, True)


def allocate(devices: List[Any], lic_state: dict, now: datetime,
             release_after_days: int = DEFAULT_RELEASE_AFTER_DAYS,
             class_billable: Optional[Dict[str, bool]] = None) -> Dict[str, int]:
    """Plätze verteilen - rein rechnerisch auf LicenseDevice-Objekten.

    Reihenfolge: wer seinen Platz schon hat (licensed_at), behält ihn; danach
    nach erstem Auftauchen. Gibt die Anzahl je Zustand zurück.
    """
    cutoff = now - timedelta(days=release_after_days)
    enterprise = lic_state.get("edition") == "enterprise"
    model = lic_state.get("model")
    candidates = []
    for d in devices:
        if d.removed_at is not None:
            d.state, d.licensed_at = "removed", None
        elif not counts_for_billing(d, class_billable):
            d.state, d.licensed_at = "excluded", None
        elif d.last_active_at is None or d.last_active_at < cutoff:
            d.state, d.licensed_at = "inactive", None
        else:
            candidates.append(d)
    candidates.sort(key=lambda d: (d.licensed_at is None, d.licensed_at or d.first_seen_at or now,
                                   d.first_seen_at or now, d.id or 0))
    if not enterprise:
        for d in candidates:
            d.state, d.licensed_at = "free", None
    elif model == "per_device" and lic_state.get("max_devices"):
        limit = int(lic_state["max_devices"])
        for i, d in enumerate(candidates):
            if i < limit:
                d.state, d.licensed_at = "licensed", d.licensed_at or now
            else:
                d.state, d.licensed_at = "over_limit", None
    elif model == "per_tenant" and lic_state.get("max_tenants"):
        real = list(lic_state.get("tenant_order") or [])
        if real:
            # Echte Mandanten: die zuerst angelegten bekommen die Plaetze.
            # Geraete des Betreibers (kein Mandant) belegen keinen.
            allowed = set(real[:int(lic_state["max_tenants"])])
            known = set(real)
            for d in candidates:
                t = d.tenant_override or d.tenant or DEFAULT_TENANT
                if t not in known or t in allowed:
                    d.state, d.licensed_at = "licensed", d.licensed_at or now
                else:
                    d.state, d.licensed_at = "over_limit", None
        else:
            order: List[str] = []
            for d in candidates:
                t = d.tenant_override or d.tenant or DEFAULT_TENANT
                if t not in order:
                    order.append(t)
            allowed = set(order[:int(lic_state["max_tenants"])])
            for d in candidates:
                if (d.tenant_override or d.tenant or DEFAULT_TENANT) in allowed:
                    d.state, d.licensed_at = "licensed", d.licensed_at or now
                else:
                    d.state, d.licensed_at = "over_limit", None
    else:
        for d in candidates:
            d.state, d.licensed_at = "licensed", d.licensed_at or now
    counts: Dict[str, int] = {}
    for d in devices:
        counts[d.state] = counts.get(d.state, 0) + 1
    return counts


async def sync_devices(db: AsyncSession, lic_state: Optional[dict] = None) -> dict:
    """Geräte abgleichen, Plätze verteilen, aktive Tage festhalten."""
    from . import billing        # Kreisimport vermeiden

    lic_state = lic_state or await current(db)
    cfg = await billing.get_config(db)
    now = datetime.utcnow()
    today = now.date()
    found = {f["device_key"]: f for f in await discover(db)}
    rows = {d.device_key: d for d in (await db.execute(select(LicenseDevice))).scalars().all()}
    new_devices = []
    for key, f in found.items():
        row = rows.get(key)
        if row is None:
            row = LicenseDevice(device_key=key, kind=f["kind"], ref_id=f["ref_id"], first_seen_at=now,
                                billable=True)
            db.add(row)
            rows[key] = row
            new_devices.append(f["name"])
        row.name = f["name"][:255]
        row.device_class = f["device_class"]
        row.tenant = f["tenant"]
        row.removed_at = None
        if f["last_active"] and (row.last_active_at is None or f["last_active"] > row.last_active_at):
            row.last_active_at = f["last_active"]
    for key, row in rows.items():
        if key not in found and row.removed_at is None:
            row.removed_at = now
    await db.flush()

    before = {k: r.state for k, r in rows.items()}
    counts = allocate(list(rows.values()), {**lic_state, "tenant_order": await real_tenants(db)}, now,
                      int(cfg.get("release_after_days") or DEFAULT_RELEASE_AFTER_DAYS),
                      cfg.get("class_billable"))

    # Aktive Tage: ein Gerät zählt an dem Tag, an dem es sich zuletzt gemeldet
    # hat. Der Takt läuft alle paar Minuten - so entsteht für jeden Tag mit
    # Lebenszeichen eine Zeile. Gestern wird mitgenommen, damit die letzten
    # Minuten vor Mitternacht nicht verloren gehen.
    day_rows = []
    for row in rows.values():
        if row.state in ("removed",) or row.last_active_at is None:
            continue
        day = row.last_active_at.date()
        if day < today - timedelta(days=1) or day > today:
            continue
        day_rows.append({"day": day, "device_key": row.device_key,
                         "tenant": (row.tenant_override or row.tenant or DEFAULT_TENANT)[:128],
                         "device_class": row.class_override or row.device_class or "other",
                         "billable": counts_for_billing(row, cfg.get("class_billable")),
                         "licensed": row.state in ("licensed", "free")})
    for start in range(0, len(day_rows), 500):
        chunk = day_rows[start:start + 500]
        stmt = pg_insert(LicenseDeviceDay).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LicenseDeviceDay.day, LicenseDeviceDay.device_key],
            set_={"tenant": stmt.excluded.tenant, "device_class": stmt.excluded.device_class,
                  "billable": stmt.excluded.billable, "licensed": stmt.excluded.licensed})
        await db.execute(stmt)

    newly_over = [r.name for k, r in rows.items() if r.state == "over_limit" and before.get(k) != "over_limit"]
    await db.commit()
    if new_devices:
        await journal.record(db, category="license", event="license.devices.added", level="info",
                             message=(f"{len(new_devices)} neue(s) Gerät(e) erfasst: "
                                      + ", ".join(new_devices[:10])
                                      + (" …" if len(new_devices) > 10 else "")),
                             actor="system", detail={"devices": new_devices[:50]})
    if newly_over:
        msg = (f"Lizenz-Kontingent erschöpft: {len(newly_over)} Gerät(e) über Kontingent ("
               + ", ".join(newly_over[:10]) + (" …" if len(newly_over) > 10 else "")
               + "). Die Überwachung läuft weiter.")
        await journal.record(db, category="license", event="license.over_limit", level="warning",
                             message=msg, actor="system", detail={"devices": newly_over[:50]})
        try:
            from . import alerts
            await alerts.dispatch("license", "RIMPERIO: Lizenz-Kontingent", msg,
                                  level="warning", key="license:over_limit")
        except Exception as exc:                                    # Meldeweg optional
            logger.debug("Lizenz-Meldung nicht verschickt: %s", exc)
    return counts


async def counts(db: AsyncSession) -> dict:
    """Wie viele Geräte gerade in welchem Zustand sind - für Anzeige und Lizenz-API."""
    rows = (await db.execute(select(LicenseDevice.state, func.count())
                             .group_by(LicenseDevice.state))).all()
    by_state = {state: n for state, n in rows}
    real = await real_tenants(db)
    if real:
        # Mandantenfaehigkeit an: gezaehlt werden die angelegten, aktiven Mandanten.
        tenants = len(real)
    else:
        tenants = (await db.execute(
            select(func.count(func.distinct(func.coalesce(LicenseDevice.tenant_override, LicenseDevice.tenant))))
            .where(LicenseDevice.state.in_(("licensed", "free", "over_limit"))))).scalar() or 0
    by_class_rows = (await db.execute(
        select(func.coalesce(LicenseDevice.class_override, LicenseDevice.device_class), func.count())
        .where(LicenseDevice.state.in_(("licensed", "free", "over_limit")))
        .group_by(func.coalesce(LicenseDevice.class_override, LicenseDevice.device_class)))).all()
    used = by_state.get("licensed", 0) + by_state.get("free", 0)
    return {"by_state": by_state, "used": used, "over_limit": by_state.get("over_limit", 0),
            "active": used + by_state.get("over_limit", 0), "excluded": by_state.get("excluded", 0),
            "inactive": by_state.get("inactive", 0), "tenants": tenants,
            "by_class": {c: n for c, n in by_class_rows}}


async def usage_snapshot(db: AsyncSession) -> dict:
    """Was an den Lizenz-Endpunkt geht: Nutzung jetzt und Spitze im Monat."""
    c = await counts(db)
    today = datetime.utcnow().date()
    first = today.replace(day=1)
    per_day = (await db.execute(
        select(LicenseDeviceDay.day, func.count())
        .where(LicenseDeviceDay.day >= first, LicenseDeviceDay.billable.is_(True))
        .group_by(LicenseDeviceDay.day))).all()
    month_devices = (await db.execute(
        select(func.count(func.distinct(LicenseDeviceDay.device_key)))
        .where(LicenseDeviceDay.day >= first, LicenseDeviceDay.billable.is_(True)))).scalar() or 0
    return {"month": first.strftime("%Y-%m"), "devices_active": c["active"],
            "devices_licensed": c["used"], "devices_over_limit": c["over_limit"],
            "devices_excluded": c["excluded"], "tenants": c["tenants"], "by_class": c["by_class"],
            "peak_devices_month": max((n for _, n in per_day), default=0),
            "devices_month": month_devices, "reported_at": _iso(datetime.utcnow())}


async def summary(db: AsyncSession) -> dict:
    """Kurzfassung für Kopfzeile, Übersicht und die App."""
    lic = await current(db)
    c = await counts(db)
    warnings = list(lic["warnings"])
    if c["over_limit"]:
        warnings.append(f"{c['over_limit']} Gerät(e) über dem Lizenz-Kontingent.")
    if lic["max_tenants"] and c["tenants"] > lic["max_tenants"]:
        warnings.append(f"{c['tenants']} Mandanten, lizenziert sind {lic['max_tenants']}.")
    info = lic.get("license") or {}
    return {"edition": lic["edition"], "edition_label": lic["edition_label"],
            "model": lic["model"], "model_label": lic["model_label"],
            "licensee": info.get("licensee", "") if lic["edition"] == "enterprise" else "",
            "valid_until": info.get("valid_until") if lic["edition"] == "enterprise" else None,
            "features": lic["features"], "devices_used": c["used"], "devices_active": c["active"],
            "devices_limit": lic["max_devices"], "devices_over_limit": c["over_limit"],
            "tenants_used": c["tenants"], "tenants_limit": lic["max_tenants"],
            "warnings": warnings, "errors": lic["errors"]}


def device_public(d: LicenseDevice) -> dict:
    cls = d.class_override or d.device_class or "other"
    return {"id": d.id, "device_key": d.device_key, "kind": d.kind, "ref_id": d.ref_id, "name": d.name,
            "device_class": cls, "class_label": DEVICE_CLASSES.get(cls, cls),
            "detected_class": d.device_class, "class_override": d.class_override,
            "tenant": d.tenant_override or d.tenant or DEFAULT_TENANT, "detected_tenant": d.tenant,
            "tenant_override": d.tenant_override, "state": d.state,
            "state_label": STATES.get(d.state, d.state), "billable": d.billable,
            "price_override": d.price_override, "note": d.note or "",
            "first_seen_at": _iso(d.first_seen_at), "licensed_at": _iso(d.licensed_at),
            "last_active_at": _iso(d.last_active_at), "removed_at": _iso(d.removed_at)}


# ==============================================================================
# Hintergrund
# ==============================================================================
async def loop() -> None:
    """Geräte alle 5 Minuten abgleichen, Lizenz im eingestellten Takt prüfen,
    Monatsabrechnung am Monatsanfang festschreiben."""
    from . import billing

    await asyncio.sleep(30)
    last_check: Optional[datetime] = None
    last_daily: Optional[date] = None
    while True:
        try:
            async with async_session() as db:
                cfg = await get_source(db)
                due = last_check is None or (
                    datetime.utcnow() - last_check > timedelta(hours=int(cfg.get("check_hours") or 12)))
                if due and cfg["mode"] != "manual":
                    last_check = datetime.utcnow()
                    await check_now(db)
                await sync_devices(db)
                today = datetime.utcnow().date()
                if last_daily != today:
                    last_daily = today
                    await billing.daily(db)
        except Exception as exc:                                    # defensiv
            logger.warning("Lizenz-Takt: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)
