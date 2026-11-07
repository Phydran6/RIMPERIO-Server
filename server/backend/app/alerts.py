# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Meldungen nach aussen: Mail, Telegram, Webhook (n8n)
# ==============================================================================
"""
Meldungen nach aussen.

**Wozu.** Am 2026-10-10 hat RIMPERIO 17 Tage lang nichts gespeichert, und niemand
hat es gemerkt: die Platte war voll, die Datenbank stand, die Oberflaeche sah
aber "gesund" aus. Ein Log-Sammler, der still stehen bleibt, ist schlimmer als
keiner - man verlaesst sich auf ihn. Deshalb meldet RIMPERIO jetzt selbst, wenn
etwas nicht stimmt.

**Drei Wege, je nachdem was der Betreiber hat:**

* **mail**     - ueber den Mailversand, der unter System -> Mail eingestellt ist
* **telegram** - direkt an einen Telegram-Bot (Bot-Token + Chat-ID), ohne
                 Zusatzdienst
* **webhook**  - ein JSON-POST an eine beliebige Adresse. Gedacht fuer n8n:
                 dort laesst sich daraus alles bauen (Telegram, Teams, Signal,
                 Ticket ...), ohne dass RIMPERIO jeden Dienst kennen muss.

**Funktioniert auch, wenn die Datenbank weg ist.** Genau dann wird eine Meldung
am dringendsten gebraucht. Die Einstellungen liegen deshalb zusaetzlich als
Datei im Datenverzeichnis (`alerts.json`, 0600) - samt einer Kopie der
Mail-Zugangsdaten. Wer gar keine Datenbank hat, kann Telegram/Webhook auch per
Umgebungsvariable setzen (ALERT_TELEGRAM_BOT_TOKEN, ALERT_TELEGRAM_CHAT_ID,
ALERT_WEBHOOK_URL).

**Kein Dauerfeuer.** Jede Meldungsart hat eine Sperrfrist (Standard 60 min).
Sobald das Problem weg ist, kommt eine Entwarnung.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import mail
from .config import settings
from .models import Setting

logger = logging.getLogger("rimperio.alerts")

SETTING_KEY = "alert_config"
DATA_DIR = Path(os.getenv("RIMPERIO_DATA_DIR", "/app/data"))
CACHE_FILE = DATA_DIR / "alerts.json"

# Was gemeldet werden kann. Reihenfolge = Reihenfolge in der Oberflaeche.
EVENTS: Dict[str, Dict[str, Any]] = {
    "db_down": {
        "label": "Datenbank nicht erreichbar",
        "hint": "Dann wird nichts mehr gespeichert - der wichtigste Alarm.",
        "default": True,
    },
    "disk": {
        "label": "Platte wird voll",
        "hint": "Ab der hohen Stufe des Plattenwächters, und wenn die Annahme stoppt.",
        "default": True,
    },
    "silence": {
        "label": "Keine neuen Logs mehr",
        "hint": "Seit der eingestellten Zeit ist nichts mehr angekommen.",
        "default": True,
    },
    "flood": {
        "label": "Ein Gerät flutet",
        "hint": "Ein Gerät schickt mehr als die eingestellte Menge pro Minute.",
        "default": True,
    },
    "agent_offline": {
        "label": "Gerät meldet sich nicht mehr",
        "hint": "Ein Gerät, das bisher geliefert hat, ist verstummt.",
        "default": False,
    },
    "rmm_offline": {
        "label": "Rechner (RMM-Agent) offline",
        "hint": "Ein Rechner mit RIMPERIO-Agent meldet sich nicht mehr.",
        "default": True,
    },
    "rmm_threshold": {
        "label": "Rechner über Schwelle",
        "hint": "CPU, Arbeitsspeicher oder Platte über der eingestellten Schwelle.",
        "default": False,
    },
    "rmm_check": {
        "label": "Check fehlgeschlagen",
        "hint": "Eine Prüfung (Dienst, Port, Ping, Skript …) meldet einen Fehler.",
        "default": True,
    },
    "rmm_patches": {
        "label": "Updates verfügbar",
        "hint": "Ein Patch-Scan hat ausstehende Updates gefunden.",
        "default": False,
    },
    "patch_approval": {
        "label": "Updates brauchen eine Freigabe",
        "hint": "Ein Patch-Scan hat Updates gefunden, die nicht automatisch installiert werden.",
        "default": True,
    },
    "patch_failed": {
        "label": "Patch-Lauf fehlgeschlagen",
        "hint": "Installation oder Prüfung danach ist schiefgegangen (Snapshot bleibt erhalten).",
        "default": True,
    },
    "patch_done": {
        "label": "Patch-Lauf erfolgreich",
        "hint": "Updates installiert und Prüfung bestanden.",
        "default": False,
    },
    "snmp_alert": {
        "label": "SNMP-Gerät meldet ein Problem",
        "hint": "Gerät nicht erreichbar oder ein Wert über der Schwelle (Toner, USV, RAID, Port …).",
        "default": True,
    },
    "update_available": {
        "label": "Neuer Stand auf GitHub",
        "hint": "Ein Update steht bereit.",
        "default": True,
    },
    "recovered": {
        "label": "Entwarnung",
        "hint": "Meldung, sobald ein Problem von oben wieder behoben ist.",
        "default": True,
    },
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "channels": {
        # Empfaenger leer = die Standard-Empfaenger aus System -> Mail.
        "mail": {"enabled": False, "recipients": []},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": "", "secret": ""},
    },
    "events": {key: spec["default"] for key, spec in EVENTS.items()},
    "silence_minutes": 60,          # 0 = nicht ueberwachen
    "flood_per_minute": 2000,       # 0 = nicht ueberwachen
    "agent_offline_minutes": 30,
    "cooldown_minutes": 60,
}

SECRET_FIELDS = (("telegram", "bot_token"), ("webhook", "secret"))

# Zwischenspeicher: was zuletzt aus der Datenbank kam. Ist sie weg, gilt die
# Datei, ist auch die weg, die Voreinstellung (+ Umgebungsvariablen).
_cache: Optional[Dict[str, Any]] = None
_last_sent: Dict[str, datetime] = {}


# =============================================================================
# Konfiguration
# =============================================================================
def _defaults() -> dict:
    return deepcopy(DEFAULT_CONFIG)


def _merge(base: dict, incoming: Any) -> dict:
    """Gespeichertes ueber die Voreinstellung legen - nur bekannte Schluessel."""
    if not isinstance(incoming, dict):
        return base
    for key, value in incoming.items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def _apply_env(config: dict) -> dict:
    """Umgebungsvariablen als Rueckfall - fuer Betrieb ganz ohne Datenbank."""
    telegram = config["channels"]["telegram"]
    webhook = config["channels"]["webhook"]
    env_token = os.getenv("ALERT_TELEGRAM_BOT_TOKEN", "").strip()
    env_chat = os.getenv("ALERT_TELEGRAM_CHAT_ID", "").strip()
    env_url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if env_token and env_chat and not telegram.get("bot_token"):
        telegram.update({"enabled": True, "bot_token": env_token, "chat_id": env_chat})
    if env_url and not webhook.get("url"):
        webhook.update({"enabled": True, "url": env_url})
    return config


def _read_file() -> Optional[dict]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:                                        # defensiv
        logger.warning("alerts.json unlesbar: %s", exc)
        return None


def _write_file(payload: dict) -> None:
    """Spiegel fuer den Fall, dass die Datenbank nicht erreichbar ist."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        # 0600 schon beim Anlegen: da stehen Bot-Token und Mail-Passwort drin.
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, default=str)
        os.replace(tmp, CACHE_FILE)
    except Exception as exc:                                        # defensiv
        logger.warning("alerts.json nicht geschrieben: %s", exc)


async def load_config(db: AsyncSession) -> dict:
    """Aus der Datenbank. Gibt es noch nichts, wird aus der Mail-Einstellung
    uebernommen, was dort schon an Benachrichtigungen angehakt war."""
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    config = _defaults()
    if row and isinstance(row.value, dict):
        return _merge(config, row.value)

    # Erster Start: die alten Haken unter "Mail" hatten nie eine Wirkung (es
    # gab keinen Aufrufer). Wer sie gesetzt hat, wollte aber genau das hier.
    try:
        old = await mail.load_config(db)
        if old.get("mode") != "off" and old.get("default_recipients"):
            config["channels"]["mail"]["enabled"] = True
        notify_on = old.get("notify_on") or {}
        for key in ("update_available", "agent_offline"):
            if key in notify_on:
                config["events"][key] = bool(notify_on[key])
        if "disk_pressure" in notify_on:
            config["events"]["disk"] = bool(notify_on["disk_pressure"])
    except Exception:                                               # defensiv
        pass
    return config


async def refresh(db: AsyncSession) -> dict:
    """Neu aus der Datenbank laden und als Datei spiegeln."""
    global _cache
    config = await load_config(db)
    mail_config = await mail.load_config(db)
    snapshot = {"config": config, "mail": mail_config}
    # Nur schreiben, wenn sich etwas geaendert hat - der Wachhund ruft das
    # jede Minute auf.
    if snapshot != _cache:
        _write_file(snapshot)
    _cache = snapshot
    return config


def current() -> dict:
    """Was gerade gilt - ohne Datenbank."""
    global _cache
    if _cache is None:
        _cache = _read_file() or {"config": _defaults(), "mail": None}
    config = _merge(_defaults(), _cache.get("config"))
    return _apply_env(config)


def _current_mail() -> Optional[dict]:
    if _cache is None:
        current()
    return (_cache or {}).get("mail")


def public_config(config: dict) -> dict:
    """Ohne Geheimnisse - so geht es an die Oberflaeche."""
    safe = deepcopy(config)
    for channel, field in SECRET_FIELDS:
        value = safe["channels"][channel].pop(field, "")
        safe["channels"][channel][f"{field}_set"] = bool(value)
    return safe


async def save_config(db: AsyncSession, incoming: dict) -> dict:
    """Pruefen und speichern. Leere Geheimnis-Felder lassen das alte stehen."""
    config = await load_config(db)
    channels = incoming.get("channels") or {}

    for name in ("mail", "telegram", "webhook"):
        part = channels.get(name) or {}
        target = config["channels"][name]
        if "enabled" in part:
            target["enabled"] = bool(part["enabled"])
        for field, value in part.items():
            if field in ("enabled",) or field.endswith("_set") or field.startswith("clear_"):
                continue
            if field not in target:
                continue
            if (name, field) in SECRET_FIELDS:
                value = str(value or "").strip()
                if value:
                    target[field] = value
                continue
            if field == "recipients":
                target[field] = [str(a).strip() for a in (value or []) if str(a).strip()]
            else:
                target[field] = str(value or "").strip()
        for channel, field in SECRET_FIELDS:
            if channel == name and part.get(f"clear_{field}"):
                target[field] = ""

    for key, value in (incoming.get("events") or {}).items():
        if key in EVENTS:
            config["events"][key] = bool(value)

    for key, low, high in (("silence_minutes", 0, 10080), ("flood_per_minute", 0, 1_000_000),
                           ("agent_offline_minutes", 5, 10080), ("cooldown_minutes", 1, 1440)):
        if incoming.get(key) is not None:
            try:
                number = int(incoming[key])
            except (TypeError, ValueError):
                raise ValueError(f"'{key}' muss eine Zahl sein.")
            if not low <= number <= high:
                raise ValueError(f"'{key}' muss zwischen {low} und {high} liegen.")
            config[key] = number

    telegram = config["channels"]["telegram"]
    if telegram["enabled"] and not (telegram["bot_token"] and telegram["chat_id"]):
        raise ValueError("Für Telegram fehlen Bot-Token oder Chat-ID.")
    webhook = config["channels"]["webhook"]
    if webhook["enabled"] and not webhook["url"].startswith(("http://", "https://")):
        raise ValueError("Die Webhook-Adresse muss mit http:// oder https:// beginnen.")

    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = config
    else:
        db.add(Setting(key=SETTING_KEY, value=config, description="Meldungen nach aussen"))
    await db.commit()
    await refresh(db)
    logger.warning("Meldungs-Einstellung gespeichert")
    return config


# =============================================================================
# Senden
# =============================================================================
def _server_name() -> str:
    site = (os.getenv("SITE_URL") or "").strip().rstrip("/")
    if site:
        return site.split("://", 1)[-1]
    return os.getenv("RIMPERIO_SERVER_NAME", "") or socket.gethostname()


async def _send_telegram(cfg: dict, text: str) -> None:
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json={
            "chat_id": cfg["chat_id"],
            "text": text[:4000],
            "disable_web_page_preview": True,
        })
    if response.status_code != 200:
        # Telegram erklaert im Klartext, was nicht passt (falsche Chat-ID, Bot
        # nicht im Chat ...) - das soll beim Testen auch so ankommen.
        try:
            detail = response.json().get("description", "")
        except Exception:
            detail = response.text[:200]
        raise RuntimeError(f"Telegram antwortet {response.status_code}: {detail}")


async def _send_webhook(cfg: dict, payload: dict) -> None:
    headers = {"Content-Type": "application/json", "User-Agent": "RIMPERIO-Alerts"}
    if cfg.get("secret"):
        headers["X-RIMPERIO-Secret"] = cfg["secret"]
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(cfg["url"], json=payload, headers=headers)
    if response.status_code >= 300:
        raise RuntimeError(f"Webhook antwortet {response.status_code}: {response.text[:200]}")


async def _send_mail(cfg: dict, subject: str, text: str) -> None:
    mail_config = _current_mail()
    if not mail_config or mail_config.get("mode") == "off":
        raise RuntimeError("Der Mailversand ist nicht eingerichtet (System → Mail).")
    recipients = cfg.get("recipients") or mail_config.get("default_recipients") or []
    if not recipients:
        raise RuntimeError("Es ist kein Empfänger hinterlegt.")
    await mail.send(mail_config, recipients, subject, text)


async def _deliver(config: dict, payload: dict, only: Optional[str] = None) -> Dict[str, str]:
    """Schickt an alle eingeschalteten Wege. Gibt je Weg 'ok' oder den Fehler."""
    subject = f"[RIMPERIO] {payload['title']}"
    text = (f"{'⚠️' if payload['level'] != 'info' else 'ℹ️'} {payload['title']}\n\n"
            f"{payload['text']}\n\n"
            f"Server: {payload['server']} · {payload['time']} UTC")
    results: Dict[str, str] = {}
    channels = config["channels"]
    jobs = []
    for name in ("telegram", "webhook", "mail"):
        if only and name != only:
            continue
        if not only and not channels[name].get("enabled"):
            continue
        if name == "telegram":
            jobs.append((name, _send_telegram(channels[name], text)))
        elif name == "webhook":
            jobs.append((name, _send_webhook(channels[name], payload)))
        else:
            jobs.append((name, _send_mail(channels[name], subject, text)))

    outcomes = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
    for (name, _), outcome in zip(jobs, outcomes):
        if isinstance(outcome, Exception):
            results[name] = str(outcome) or outcome.__class__.__name__
            logger.warning("Meldung über %s nicht zugestellt: %s", name, results[name])
        else:
            results[name] = "ok"
    return results


def _payload(event: str, title: str, text: str, level: str, detail: Optional[dict]) -> dict:
    return {
        "source": "rimperio",
        "event": event,
        "level": level,
        "title": title,
        "text": text,
        "server": _server_name(),
        "version": settings.app_version,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "detail": detail or {},
    }


async def dispatch(event: str, title: str, text: str, level: str = "warning",
                   key: Optional[str] = None, detail: Optional[dict] = None) -> Dict[str, str]:
    """Meldet ein Ereignis - wenn gewuenscht und nicht gerade erst gemeldet.

    Wirft nie: eine Meldung, die nicht rausgeht, darf den Wachhund nicht
    umwerfen. Gibt je Weg das Ergebnis zurueck (leer = nichts verschickt).
    """
    try:
        config = current()
        if not config["events"].get(event, False):
            return {}
        cooldown_key = key or event
        now = datetime.utcnow()
        last = _last_sent.get(cooldown_key)
        if last and (now - last).total_seconds() < config["cooldown_minutes"] * 60:
            return {}
        _last_sent[cooldown_key] = now
        results = await _deliver(config, _payload(event, title, text, level, detail))
        if results:
            logger.warning("Meldung '%s' (%s): %s", title, event, results)
        return results
    except Exception as exc:                                        # defensiv
        logger.error("Meldung '%s' fehlgeschlagen: %s", event, exc)
        return {}


def forget(key: str) -> None:
    """Sperrfrist aufheben - nach einer Entwarnung darf das Problem neu melden."""
    _last_sent.pop(key, None)


async def send_test(channel: str) -> Dict[str, str]:
    """Probemeldung ueber genau einen Weg - auch wenn er noch aus ist."""
    if channel not in DEFAULT_CONFIG["channels"]:
        raise ValueError(f"Unbekannter Weg '{channel}'.")
    payload = _payload("test", "Probemeldung",
                       "Kommt diese Nachricht an, funktioniert der Weg.", "info", None)
    return await _deliver(current(), payload, only=channel)


def describe() -> dict:
    """Ereignisse fuer die Oberflaeche (Beschriftung + Hinweis)."""
    return {key: {"label": spec["label"], "hint": spec["hint"]} for key, spec in EVENTS.items()}


def recipients_preview() -> List[str]:
    mail_config = _current_mail() or {}
    return list(mail_config.get("default_recipients") or [])
