# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Logs von einer KI auswerten lassen (nativ oder ueber n8n)
# ==============================================================================
"""
Logs an eine KI geben - auf dem Weg, den man selbst waehlt.

Vier Wege, und keiner davon ist Pflicht:

* `off`          - nichts verlaesst den Server. Voreinstellung.
* `n8n_external` - die Auswahl geht per Webhook an ein n8n, das woanders laeuft.
                   Was dort passiert, bestimmt der Workflow. Der fertige
                   Workflow liegt unter `n8n/` im Repository.
* `n8n_local`    - dasselbe, nur laeuft n8n als Container neben RIMPERIO
                   (`server/deploy/optional.yml`, Profil `n8n`). Die Adresse ist dann
                   intern und verlaesst den Server gar nicht.
* `anthropic` / `openai` - direkt an die KI, ohne Zwischenstation. Ein
                   API-Schluessel genuegt.

Warum alle vier: n8n kann mehr (Telegram, Ticketsystem, Datenbanken), ist aber
ein weiteres System, das laufen und gepflegt werden muss. Wer nur "erklaer mir
diese 200 Zeilen" will, braucht das nicht.

**Was den Server verlaesst:** bei jedem Weg ausser `off` gehen die ausgewaehlten
Logzeilen an den eingestellten Empfaenger. Das steht auch so in der Oberflaeche,
und `preview_payload()` zeigt vorher genau, was gesendet wuerde.

Der API-Schluessel wird nie zurueckgegeben - weder in der Konfiguration noch in
einer Fehlermeldung. Herausgegeben wird nur, ob einer hinterlegt ist.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import logparse
from .models import Setting

logger = logging.getLogger("rimperio.ai")

SETTING_KEY = "ai_config"

# Wie viele Zeilen hoechstens an die KI gehen. Ohne Grenze schickt ein
# unbedachter Klick den halben Logbestand ins Internet - und die Rechnung kommt
# spaeter.
MAX_LOGS = 500
DEFAULT_LOGS = 100
REQUEST_TIMEOUT = 120.0

PROVIDERS: Dict[str, dict] = {
    "off": {
        "label": "Aus",
        "label_en": "Off",
        "hint": "Keine Logdaten verlassen den Server.",
        "needs_key": False,
        "needs_url": False,
    },
    "anthropic": {
        "label": "Claude (Anthropic) — direkt",
        "label_en": "Claude (Anthropic) — direct",
        "hint": "Direkt an die Anthropic-API. Nur ein API-Schlüssel nötig.",
        "needs_key": True,
        "needs_url": False,
        "default_model": "claude-sonnet-4-5",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "label": "ChatGPT (OpenAI) — direkt",
        "label_en": "ChatGPT (OpenAI) — direct",
        "hint": "Direkt an die OpenAI-API. Nur ein API-Schlüssel nötig.",
        "needs_key": True,
        "needs_url": False,
        "default_model": "gpt-4o-mini",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "n8n_external": {
        "label": "n8n — extern per Webhook",
        "label_en": "n8n — external webhook",
        "hint": ("Die Auswahl geht an einen n8n-Webhook außerhalb dieses Servers. "
                 "Was dort passiert, bestimmt der Workflow."),
        "needs_key": False,
        "needs_url": True,
    },
    "n8n_local": {
        "label": "n8n — Container auf diesem Server",
        "label_en": "n8n — container on this server",
        "hint": ("n8n läuft als Zusatz-Container neben RIMPERIO. Die Daten bleiben "
                 "im Docker-Netz, solange der Workflow sie nicht weitergibt."),
        "needs_key": False,
        "needs_url": True,
        "default_url": "http://rimperio-n8n:5678/webhook/rimperio",
    },
    "openwebui": {
        "label": "Open WebUI — eigene KI, auch lokal",
        "label_en": "Open WebUI — self-hosted, can be local",
        "hint": ("Open WebUI spricht dieselbe Sprache wie OpenAI, hat dahinter aber "
                 "das Modell, das man selbst wählt — auch ein lokales über Ollama. "
                 "Läuft es auf diesem Server, verlässt keine Logzeile das Haus."),
        "needs_key": True,
        "needs_url": True,
        "default_model": "llama3.1",
        "default_url": "http://rimperio-openwebui:8080",
        "key_url": "",
        "key_hint": ("In Open WebUI: Profil → Einstellungen → Konto → API-Schlüssel. "
                     "Ein Lese-Schlüssel genügt."),
        "local": True,
    },
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": "off",
    "model": "",
    "webhook_url": "",
    "webhook_header": "",          # z.B. "X-Auth: geheim" fuer den n8n-Webhook
    "system_prompt": "",
    "max_logs": DEFAULT_LOGS,
    "include_raw": False,           # Rohzeilen mitschicken (mehr Kontext, mehr Daten)
    "enabled_for_users": False,     # nur Admins, oder alle Angemeldeten?
    "api_key": "",                  # wird nie ausgeliefert
}

DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein Assistent für Log-Analyse. Du bekommst Logzeilen eines "
    "zentralen Log-Servers. Antworte knapp und auf Deutsch. Nenne zuerst, was "
    "auffällt, dann die wahrscheinliche Ursache, dann was zu prüfen ist. "
    "Erfinde nichts: was nicht in den Logs steht, sagst du nicht."
)


# =============================================================================
# Konfiguration
# =============================================================================
async def load_config(db: AsyncSession) -> dict:
    """Liest die Einstellung. Fehlende Werte kommen aus der Vorgabe."""
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    config = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        config.update({k: v for k, v in row.value.items() if k in DEFAULT_CONFIG})
    if config["provider"] not in PROVIDERS:
        config["provider"] = "off"
    return config


async def save_config(db: AsyncSession, incoming: dict) -> dict:
    """Schreibt die Einstellung. Ein leerer Schluessel loescht nicht den alten."""
    current = await load_config(db)
    provider = (incoming.get("provider") or current["provider"]).strip()
    if provider not in PROVIDERS:
        raise ValueError(f"Unbekannter Weg '{provider}'. Erlaubt: {', '.join(PROVIDERS)}.")

    config = dict(current)
    for key in ("model", "webhook_url", "webhook_header", "system_prompt"):
        if key in incoming and incoming[key] is not None:
            config[key] = str(incoming[key]).strip()
    for key in ("include_raw", "enabled_for_users"):
        if key in incoming and incoming[key] is not None:
            config[key] = bool(incoming[key])
    if incoming.get("max_logs") is not None:
        config["max_logs"] = max(1, min(int(incoming["max_logs"]), MAX_LOGS))

    config["provider"] = provider

    # Ein leeres Feld heisst "unveraendert lassen" - sonst waere der Schluessel
    # nach jedem Speichern weg, weil die Oberflaeche ihn nie zurueckbekommt.
    incoming_key = (incoming.get("api_key") or "").strip()
    if incoming_key:
        config["api_key"] = incoming_key
    if incoming.get("clear_api_key"):
        config["api_key"] = ""

    spec = PROVIDERS[provider]
    if spec.get("needs_key") and not config["api_key"]:
        raise ValueError(f"Für '{spec['label']}' fehlt der API-Schlüssel.")
    if spec.get("needs_url") and not config["webhook_url"]:
        raise ValueError(f"Für '{spec['label']}' fehlt die Webhook-Adresse.")
    if config["webhook_url"] and not config["webhook_url"].startswith(("http://", "https://")):
        raise ValueError("Die Webhook-Adresse muss mit http:// oder https:// beginnen.")
    if not config["model"] and spec.get("default_model"):
        config["model"] = spec["default_model"]

    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = config
    else:
        db.add(Setting(key=SETTING_KEY, value=config,
                       description="Anbindung an eine KI zur Log-Auswertung"))
    await db.commit()
    logger.warning("KI-Anbindung gespeichert: %s", provider)
    return public_config(config)


def public_config(config: dict) -> dict:
    """Die Konfiguration ohne Geheimnisse — so geht sie an die Oberflaeche."""
    safe = {k: v for k, v in config.items() if k != "api_key"}
    safe["api_key_set"] = bool(config.get("api_key"))
    safe["provider_label"] = PROVIDERS.get(config.get("provider", "off"), {}).get("label", "")
    return safe


def catalog() -> List[dict]:
    """Die vier Wege als Liste fuer die Oberflaeche."""
    return [
        {"id": key, "label": spec["label"], "label_en": spec["label_en"],
         "hint": spec["hint"], "needs_key": spec.get("needs_key", False),
         "needs_url": spec.get("needs_url", False),
         "default_model": spec.get("default_model", ""),
         "default_url": spec.get("default_url", ""),
         "key_url": spec.get("key_url", ""),
         "key_hint": spec.get("key_hint", ""),
         # 'local' heisst: die Logzeilen verlassen den Server nicht, sofern der
         # Dienst hier laeuft. Die Oberflaeche hebt das hervor.
         "local": bool(spec.get("local"))}
        for key, spec in PROVIDERS.items()
    ]


# =============================================================================
# Logs aufbereiten
# =============================================================================
def build_payload(logs: List[Any], question: str, config: dict) -> dict:
    """Bringt die Logzeilen in eine Form, mit der eine KI etwas anfangen kann.

    Statt der Rohzeilen geht die *zerlegte* Fassung raus: eine Firewall-Zeile
    mit dreissig `key=value`-Paaren kostet sonst mehr Zeichen, als sie an
    Erkenntnis bringt.
    """
    include_raw = bool(config.get("include_raw"))
    entries = []

    for log in logs[: int(config.get("max_logs") or DEFAULT_LOGS)]:
        parsed = logparse.parse_log_row(log)
        entry = {
            "ts": (log.timestamp.isoformat() if getattr(log, "timestamp", None) else None),
            "host": getattr(log, "hostname", None),
            "level": getattr(log, "level", None),
            "source": getattr(log, "source", None),
            "text": parsed["summary"] or getattr(log, "message", "") or "",
        }
        if parsed["fields"]:
            entry["fields"] = parsed["fields"]
        if include_raw and getattr(log, "raw_message", None):
            entry["raw"] = log.raw_message
        entries.append(entry)

    return {
        "question": (question or "").strip(),
        "generated_at": datetime.utcnow().isoformat(),
        "count": len(entries),
        "logs": entries,
    }


def _as_text(payload: dict) -> str:
    """Die Logs als Text - das, was am Ende im Prompt landet."""
    lines = []
    for entry in payload["logs"]:
        parts = [entry.get("ts") or "", (entry.get("level") or "").upper(),
                 entry.get("host") or "", entry.get("source") or "", entry.get("text") or ""]
        line = " | ".join(part for part in parts if part)
        if entry.get("fields"):
            line += " | " + " ".join(f"{k}={v}" for k, v in list(entry["fields"].items())[:15])
        lines.append(line)
    return "\n".join(lines)


# =============================================================================
# Die einzelnen Wege
# =============================================================================
async def _ask_anthropic(config: dict, payload: dict) -> dict:
    spec = PROVIDERS["anthropic"]
    model = config.get("model") or spec["default_model"]
    system = config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    question = payload["question"] or "Was fällt an diesen Logs auf?"

    body = {
        "model": model,
        "max_tokens": 2000,
        "system": system,
        "messages": [{
            "role": "user",
            "content": (f"{question}\n\nHier sind {payload['count']} Logzeilen:\n\n"
                        f"{_as_text(payload)}"),
        }],
    }
    headers = {
        "x-api-key": config["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(spec["endpoint"], json=body, headers=headers)
        data = _json_or_error(response, "Anthropic")

    text = "".join(block.get("text", "") for block in data.get("content", [])
                   if block.get("type") == "text")
    return {"answer": text.strip(), "model": data.get("model", model),
            "usage": data.get("usage", {})}


async def _ask_openai(config: dict, payload: dict) -> dict:
    spec = PROVIDERS["openai"]
    model = config.get("model") or spec["default_model"]
    system = config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    question = payload["question"] or "Was fällt an diesen Logs auf?"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",
             "content": (f"{question}\n\nHier sind {payload['count']} Logzeilen:\n\n"
                         f"{_as_text(payload)}")},
        ],
    }
    headers = {"Authorization": f"Bearer {config['api_key']}",
               "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(spec["endpoint"], json=body, headers=headers)
        data = _json_or_error(response, "OpenAI")

    choices = data.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    return {"answer": text, "model": data.get("model", model),
            "usage": data.get("usage", {})}


async def _ask_openwebui(config: dict, payload: dict) -> dict:
    """Open WebUI - dieselbe Schnittstelle wie OpenAI, nur eben die eigene.

    Der Unterschied zu `_ask_openai` ist genau einer: die Adresse. Deshalb
    steht hier auch keine zweite Auswertung der Antwort, sondern dieselbe.

    Warum das so wichtig ist: mit Open WebUI und einem lokalen Modell (Ollama)
    bleibt die Auswertung komplett auf dem eigenen Server. Fuer Logdaten, in
    denen Benutzernamen, interne Adressen und Fehlermeldungen stehen, ist das
    der Unterschied zwischen "geht" und "geht nicht".
    """
    base = (config.get("webhook_url") or "").rstrip("/")
    if not base:
        raise ValueError("Für Open WebUI fehlt die Adresse.")
    # Nachsichtig: der vollstaendige Pfad darf mit angegeben werden.
    endpoint = base if base.endswith("/chat/completions") else f"{base}/api/chat/completions"

    model = config.get("model") or PROVIDERS["openwebui"]["default_model"]
    system = config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    question = payload["question"] or "Was fällt an diesen Logs auf?"

    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",
             "content": (f"{question}\n\nHier sind {payload['count']} Logzeilen:\n\n"
                         f"{_as_text(payload)}")},
        ],
    }
    headers = {"Authorization": f"Bearer {config['api_key']}",
               "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(endpoint, json=body, headers=headers)
        data = _json_or_error(response, "Open WebUI")

    choices = data.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError("Open WebUI hat geantwortet, aber ohne Text. Stimmt der "
                           "Modellname? Die verfügbaren Modelle stehen dort unter "
                           "Einstellungen → Modelle.")
    return {"answer": text, "model": data.get("model", model),
            "usage": data.get("usage", {})}


async def list_openwebui_models(config: dict) -> list:
    """Welche Modelle bietet dieses Open WebUI an?

    Damit man den Modellnamen nicht abtippen muss - und vor allem nicht raet.
    """
    base = (config.get("webhook_url") or "").rstrip("/")
    key = config.get("api_key") or ""
    if not base or not key:
        return []
    url = base if base.endswith("/models") else f"{base}/api/models"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
            if response.status_code >= 400:
                return []
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Modellliste von Open WebUI nicht abrufbar: %s", exc)
        return []

    entries = data.get("data") if isinstance(data, dict) else data
    models = []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("id"):
            models.append({"id": entry["id"], "label": entry.get("name") or entry["id"]})
    return models


async def _ask_n8n(config: dict, payload: dict) -> dict:
    """Schickt die Auswahl an einen n8n-Webhook und nimmt entgegen, was zurueckkommt.

    n8n-Workflows antworten sehr unterschiedlich - mal ein Objekt, mal eine
    Liste mit einem Objekt, mal einfach Text. Deshalb wird die Antwort hier
    nachsichtig ausgewertet statt streng.
    """
    url = config["webhook_url"]
    headers = {"Content-Type": "application/json"}

    # "Name: Wert" - so lassen sich einfache Absicherungen des Webhooks setzen,
    # ohne dafuer ein eigenes Feld pro Kopfzeile zu bauen.
    raw_header = (config.get("webhook_header") or "").strip()
    if ":" in raw_header:
        name, _, value = raw_header.partition(":")
        headers[name.strip()] = value.strip()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"n8n hat mit HTTP {response.status_code} geantwortet: "
                f"{response.text[:300]}")
        try:
            data = response.json()
        except ValueError:
            return {"answer": response.text.strip(), "model": "n8n", "usage": {}}

    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, str):
        return {"answer": data.strip(), "model": "n8n", "usage": {}}
    if not isinstance(data, dict):
        return {"answer": json.dumps(data, ensure_ascii=False)[:4000],
                "model": "n8n", "usage": {}}

    for key in ("answer", "text", "output", "result", "message", "response", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return {"answer": value.strip(), "model": data.get("model", "n8n"),
                    "usage": data.get("usage", {})}

    return {"answer": json.dumps(data, ensure_ascii=False, indent=2)[:4000],
            "model": "n8n", "usage": {}}


def _json_or_error(response: httpx.Response, who: str) -> dict:
    """Antwort auswerten - und im Fehlerfall sagen, was der Anbieter meldet.

    Wichtig fuer die Fehlersuche: "HTTP 401" allein hilft niemandem,
    "invalid x-api-key" schon.
    """
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            detail = ((body.get("error") or {}).get("message")
                      if isinstance(body.get("error"), dict) else body.get("error")) or ""
        except ValueError:
            detail = response.text[:300]
        hint = ""
        if response.status_code == 401:
            hint = " Der API-Schlüssel stimmt nicht oder ist abgelaufen."
        elif response.status_code == 429:
            hint = " Zu viele Anfragen oder das Guthaben ist aufgebraucht."
        raise RuntimeError(f"{who} hat mit HTTP {response.status_code} geantwortet: "
                           f"{detail or 'ohne nähere Angabe'}.{hint}")
    return response.json()


# =============================================================================
# Fragen stellen
# =============================================================================
async def ask(config: dict, logs: List[Any], question: str = "") -> dict:
    """Schickt die Logzeilen auf dem eingestellten Weg los."""
    provider = config.get("provider", "off")
    if provider == "off":
        raise ValueError("Die KI-Anbindung ist ausgeschaltet. "
                         "Einzurichten unter Automatisierung → KI-Auswertung.")
    if not logs:
        raise ValueError("Es wurden keine Logzeilen ausgewählt.")

    payload = build_payload(logs, question, config)
    started = time.time()

    handlers = {
        "anthropic": _ask_anthropic,
        "openai": _ask_openai,
        "openwebui": _ask_openwebui,
        "n8n_external": _ask_n8n,
        "n8n_local": _ask_n8n,
    }
    handler = handlers.get(provider)
    if not handler:
        raise ValueError(f"Für '{provider}' gibt es keinen Weg.")

    try:
        result = await handler(config, payload)
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"Keine Antwort innerhalb von {REQUEST_TIMEOUT:.0f} Sekunden. "
            f"Weniger Zeilen auswählen oder später erneut versuchen."
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Verbindung fehlgeschlagen: {exc}") from exc

    result.update({
        "provider": provider,
        "logs_sent": payload["count"],
        "duration_seconds": round(time.time() - started, 1),
        "asked_at": datetime.utcnow().isoformat(),
    })
    logger.info("KI-Auswertung über %s: %s Zeilen in %.1fs",
                provider, payload["count"], result["duration_seconds"])
    return result


async def test_connection(config: dict) -> dict:
    """Ein kurzer Probelauf mit einer erfundenen Logzeile.

    Bewusst mit einer Beispielzeile statt mit echten Daten: der Test soll die
    Verbindung pruefen, nicht schon Logs weitergeben.
    """
    provider = config.get("provider", "off")
    if provider == "off":
        return {"ok": False, "message": "Die KI-Anbindung ist ausgeschaltet — nichts zu testen."}

    class _Sample:
        timestamp = datetime.utcnow()
        hostname = "rimperio-test"
        level = "warning"
        source = "rimperio"
        message = "Verbindungstest der KI-Anbindung — dies ist keine echte Logzeile."
        raw_message = message
        facility = 1
        extra_data: dict = {}

    try:
        result = await ask(config, [_Sample()],
                           "Antworte in einem Satz, dass die Verbindung steht.")
    except Exception as exc:
        return {"ok": False, "message": str(exc), "provider": provider}

    return {
        "ok": True,
        "provider": provider,
        "model": result.get("model"),
        "answer": (result.get("answer") or "")[:500],
        "duration_seconds": result.get("duration_seconds"),
        "message": "Die Verbindung steht.",
    }


# =============================================================================
# Freie Textanfrage (z. B. Update-Einschaetzung im Patchmanagement)
# =============================================================================
# Modelle, die die neuere Websuche (mit dynamischer Filterung) kennen; alle
# anderen Claude-Modelle bekommen die einfache Variante.
_WEB_SEARCH_NEW = ("claude-opus-5", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
                   "claude-sonnet-5", "claude-sonnet-4-6")
# Modelle, fuer die Anthropic bei einer Ablehnung serverseitig auf ein anderes
# Modell ausweichen kann ("fallbacks": "default").
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")
TEXT_TIMEOUT = 240.0


def supports_native_web_search(config: dict) -> bool:
    return config.get("provider") == "anthropic"


async def _text_anthropic(config: dict, system: str, prompt: str, web_search: bool,
                          max_tokens: int) -> dict:
    spec = PROVIDERS["anthropic"]
    model = config.get("model") or spec["default_model"]
    headers = {"x-api-key": config["api_key"], "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    body: Dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system,
                            "messages": [{"role": "user", "content": prompt}]}
    if model in _FALLBACK_MODELS:
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        body["fallbacks"] = "default"
    if web_search:
        tool_type = ("web_search_20260209" if model.startswith(_WEB_SEARCH_NEW)
                     else "web_search_20250305")
        body["tools"] = [{"type": tool_type, "name": "web_search", "max_uses": 5}]

    texts: List[str] = []
    sources: List[dict] = []
    data: dict = {}
    async with httpx.AsyncClient(timeout=TEXT_TIMEOUT) as client:
        for _ in range(4):                       # pause_turn: Server setzt die Suche fort
            response = await client.post(spec["endpoint"], json=body, headers=headers)
            data = _json_or_error(response, "Anthropic")
            if data.get("stop_reason") == "refusal":
                raise RuntimeError("Das Modell hat die Anfrage abgelehnt.")
            for block in data.get("content", []):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "web_search_tool_result" and isinstance(block.get("content"), list):
                    for r in block["content"]:
                        if r.get("type") == "web_search_result":
                            sources.append({"title": r.get("title", ""), "url": r.get("url", "")})
            if data.get("stop_reason") != "pause_turn":
                break
            body["messages"] = [{"role": "user", "content": prompt},
                                {"role": "assistant", "content": data.get("content", [])}]
            texts = []
    return {"answer": "".join(texts).strip(), "model": data.get("model", model),
            "usage": data.get("usage", {}), "sources": sources[:20]}


async def _text_openai_like(config: dict, system: str, prompt: str, endpoint: str,
                            default_model: str, who: str) -> dict:
    model = config.get("model") or default_model
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": prompt}]}
    if who == "Open WebUI":
        body["stream"] = False
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=TEXT_TIMEOUT) as client:
        response = await client.post(endpoint, json=body, headers=headers)
        data = _json_or_error(response, who)
    choices = data.get("choices") or [{}]
    return {"answer": ((choices[0].get("message") or {}).get("content") or "").strip(),
            "model": data.get("model", model), "usage": data.get("usage", {}), "sources": []}


async def ask_text(config: dict, system: str, prompt: str, web_search: bool = False,
                   max_tokens: int = 8000) -> dict:
    """Eine freie Frage an die eingestellte KI. `web_search` nutzt - nur bei
    Claude - die eingebaute Websuche des Modells."""
    provider = config.get("provider", "off")
    if provider == "off":
        raise ValueError("Die KI-Anbindung ist ausgeschaltet.")
    started = time.time()
    try:
        if provider == "anthropic":
            result = await _text_anthropic(config, system, prompt, web_search, max_tokens)
        elif provider == "openai":
            result = await _text_openai_like(config, system, prompt, PROVIDERS["openai"]["endpoint"],
                                             PROVIDERS["openai"]["default_model"], "OpenAI")
        elif provider == "openwebui":
            base = (config.get("webhook_url") or "").rstrip("/")
            endpoint = base if base.endswith("/chat/completions") else f"{base}/api/chat/completions"
            result = await _text_openai_like(config, system, prompt, endpoint,
                                             PROVIDERS["openwebui"]["default_model"], "Open WebUI")
        elif provider in ("n8n_external", "n8n_local"):
            result = await _ask_n8n(config, {"kind": "text", "system": system, "question": prompt,
                                             "count": 0, "logs": []})
            result.setdefault("sources", [])
        else:
            raise ValueError(f"Für '{provider}' gibt es keinen Weg.")
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Keine Antwort innerhalb von {TEXT_TIMEOUT:.0f} Sekunden.") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Verbindung fehlgeschlagen: {exc}") from exc
    result["provider"] = provider
    result["duration_seconds"] = round(time.time() - started, 1)
    return result


def parse_json_answer(text: str) -> Optional[Any]:
    """Holt das erste JSON-Objekt aus einer Antwort - auch aus ```json-Bloecken."""
    if not text:
        return None
    fence = text.find("```")
    if fence >= 0:
        inner = text[fence + 3:]
        if inner.lower().startswith("json"):
            inner = inner[4:]
        end = inner.find("```")
        if end >= 0:
            try:
                return json.loads(inner[:end].strip())
            except ValueError:
                pass
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def preview_payload(logs: List[Any], question: str, config: dict) -> dict:
    """Was ginge raus? Zum Nachsehen, bevor Daten den Server verlassen."""
    payload = build_payload(logs, question, config)
    text = _as_text(payload)
    return {
        "provider": config.get("provider", "off"),
        "count": payload["count"],
        "characters": len(text),
        "approx_tokens": len(text) // 4,       # grobe Hausnummer, kein Anspruch
        "includes_raw": bool(config.get("include_raw")),
        "sample": text[:2000],
        "target": (config.get("webhook_url")
                   or PROVIDERS.get(config.get("provider", "off"), {}).get("endpoint", "")),
    }
