# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - HTTPS-Weiterleitung über einen Agent am Standort
#               (NAS & Co. an einem anderen Ort, ohne offenen Port)
# ==============================================================================
"""
Ein NAS an einem anderen Standort ist vom RIMPERIO-Server aus meist nicht
erreichbar: anderes Netz, keine Portweiterleitung, kein VPN. Ein Agent an
diesem Standort ist es dagegen schon - er baut die Verbindung selbst auf, nur
ausgehend per HTTPS, und hält mit ``/api/rmm/agent/wait`` ständig eine offen.

Über genau diese Verbindung laufen hier HTTP-Anfragen: Der Server legt eine
Anfrage für den Agent in die Warteschlange, weckt die wartende Verbindung, der
Agent stellt die Anfrage im eigenen Netz (oder auf dem NAS selbst, wenn er dort
als Paket läuft) und schickt die Antwort mit ``/api/rmm/agent/relay-results``
zurück.

Für den NAS-Code sieht das aus wie eine gewöhnliche Verbindung:
``AgentTransport`` ist ein httpx-Transport. ``httpx.AsyncClient(transport=…)``
- alles andere bleibt, wie es ist.

Wie ``agentwake`` lebt das im Speicher des einen Backend-Prozesses. Fällt eine
Anfrage aus (Agent neu gestartet, Server neu gestartet), läuft sie in die
Zeitüberschreitung; der NAS-Takt versucht es beim nächsten Mal wieder.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from . import agentwake

# Was ein Agent ankündigen muss, damit der Server ihm Anfragen schickt.
CAPABILITY = "relay"
# Obergrenze für eine Antwort (Konfigurationssicherung eines DSM: wenige MB).
MAX_BODY = 32 * 1024 * 1024
# Anfragen, die so lange niemand abgeholt hat, verfallen.
QUEUE_TTL = 60.0
# Luft über der Zeitgrenze des Agents: Weg hin und zurück, Antwort hochladen.
REPLY_SLACK = 20.0
# Kopfzeilen, die der Agent selbst setzt bzw. die nach dem Weg nicht mehr stimmen.
_DROP_REQUEST = {"host", "content-length", "transfer-encoding", "connection", "accept-encoding", "keep-alive"}
_DROP_RESPONSE = {"content-length", "transfer-encoding", "content-encoding", "connection", "keep-alive"}

_queues: Dict[int, List[dict]] = {}
_pending: Dict[str, Tuple[int, asyncio.Future]] = {}
_caps: Dict[int, Set[str]] = {}
_stats: Dict[int, dict] = {}


class RelayError(Exception):
    """Der Weg über den Agent ist nicht möglich."""


class RelayUnavailable(RelayError):
    """Der Agent ist gerade nicht verbunden oder kann keine Weiterleitung."""


# =============================================================================
# Fähigkeiten und Zustand
# =============================================================================
def set_caps(agent_id: int, caps: Any) -> None:
    """Merkt sich, was der Agent bei seiner Warte-Anfrage angekündigt hat."""
    if isinstance(caps, (list, tuple, set)):
        _caps[agent_id] = {str(c) for c in caps if isinstance(c, str)}


def capable(agent_id: int) -> bool:
    """Kann der Agent weiterleiten (angekündigt) - unabhängig davon, ob er gerade wartet."""
    return CAPABILITY in _caps.get(agent_id, set())


def ready(agent_id: Optional[int]) -> bool:
    """Wartet der Agent gerade und kann weiterleiten?"""
    return bool(agent_id) and capable(agent_id) and agentwake.is_live(agent_id)


def status(agent_id: Optional[int]) -> dict:
    """Für die Oberfläche: Ist der Weg offen, wie lange dauern Anfragen?"""
    if not agent_id:
        return {"ready": False, "live": False, "capable": False}
    s = _stats.get(agent_id, {})
    return {"ready": ready(agent_id), "live": agentwake.is_live(agent_id), "capable": capable(agent_id),
            "requests": s.get("requests", 0), "errors": s.get("errors", 0),
            "last_ms": s.get("last_ms"), "last_error": s.get("last_error"), "last_at": s.get("last_at")}


def forget(agent_id: int) -> None:
    _caps.pop(agent_id, None)
    _queues.pop(agent_id, None)
    _stats.pop(agent_id, None)
    for rid, (aid, fut) in list(_pending.items()):
        if aid == agent_id and not fut.done():
            fut.set_exception(RelayUnavailable("Der Agent wurde abgemeldet."))


def _stat(agent_id: int, ms: Optional[int], error: Optional[str]) -> None:
    s = _stats.setdefault(agent_id, {"requests": 0, "errors": 0})
    s["requests"] += 1
    s["last_at"] = time.time()
    if ms is not None:
        s["last_ms"] = ms
    if error:
        s["errors"] += 1
        s["last_error"] = error[:300]


# =============================================================================
# Warteschlange (Server-Seite der Warte-Anfrage)
# =============================================================================
def take(agent_id: int, limit: int = 32) -> List[dict]:
    """Holt die anstehenden Anfragen für die Warte-Anfrage des Agents ab."""
    q = _queues.get(agent_id)
    if not q:
        return []
    now = time.monotonic()
    out: List[dict] = []
    keep: List[dict] = []
    for item in q:
        rid = item["id"]
        entry = _pending.get(rid)
        if entry is None or entry[1].done() or now - item["_queued"] > QUEUE_TTL:
            continue                                     # aufgegeben oder verfallen
        if len(out) < limit:
            out.append({k: v for k, v in item.items() if not k.startswith("_")})
        else:
            keep.append(item)
    if keep:
        _queues[agent_id] = keep
    else:
        _queues.pop(agent_id, None)
    return out


def deliver(agent_id: int, results: List[dict]) -> int:
    """Antworten des Agents den wartenden Aufrufern zustellen."""
    done = 0
    for r in results or []:
        if not isinstance(r, dict):
            continue
        entry = _pending.get(str(r.get("id", "")))
        if entry is None or entry[0] != agent_id:
            continue                                     # unbekannt oder fremd: verwerfen
        fut = entry[1]
        if not fut.done():
            fut.set_result(r)
            done += 1
    return done


async def forward(agent_id: int, *, method: str, url: str, headers: Optional[Dict[str, str]] = None,
                  body: bytes = b"", timeout: float = 30.0, verify: bool = False,
                  want_cert: bool = False) -> dict:
    """Eine HTTP-Anfrage über den Agent stellen und auf die Antwort warten.

    Liefert das Ergebnis des Agents: ``status``, ``headers`` (Name -> Liste),
    ``body`` (bytes), ``cert`` (DER, wenn verlangt) - oder ``error`` mit
    ``error_kind`` (connect | timeout | tls | denied | other).
    """
    if not agent_id:
        raise RelayUnavailable("Kein Agent für die Weiterleitung festgelegt.")
    if not agentwake.is_live(agent_id):
        raise RelayUnavailable("Der Agent am Standort ist gerade nicht verbunden.")
    if not capable(agent_id):
        raise RelayUnavailable("Der Agent am Standort kann noch nicht weiterleiten – bitte auf "
                               "Version 0.3.0 oder neuer aktualisieren.")
    if len(body) > MAX_BODY:
        raise RelayError("Anfrage zu groß für die Weiterleitung.")
    rid = secrets.token_hex(12)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending[rid] = (agent_id, fut)
    timeout = max(1.0, min(float(timeout or 30.0), 600.0))
    item = {"id": rid, "method": method.upper(), "url": url,
            "headers": {k: v for k, v in (headers or {}).items() if k.lower() not in _DROP_REQUEST},
            "body": base64.b64encode(body).decode() if body else "",
            "timeout": int(timeout + 0.999), "insecure": not verify, "cert": bool(want_cert),
            "_queued": time.monotonic()}
    _queues.setdefault(agent_id, []).append(item)
    agentwake.notify(agent_id)
    started = time.monotonic()
    try:
        res = await asyncio.wait_for(fut, timeout + REPLY_SLACK)
    except asyncio.TimeoutError:
        _stat(agent_id, None, "keine Antwort des Agents")
        raise RelayUnavailable("Der Agent am Standort hat nicht rechtzeitig geantwortet.") from None
    finally:
        _pending.pop(rid, None)
    ms = int((time.monotonic() - started) * 1000)
    out = dict(res)
    raw = out.get("body") or ""
    try:
        out["body"] = base64.b64decode(raw) if raw else b""
    except (ValueError, TypeError):
        out["body"] = b""
    cert = out.get("cert") or ""
    try:
        out["cert"] = base64.b64decode(cert) if cert else None
    except (ValueError, TypeError):
        out["cert"] = None
    _stat(agent_id, ms, out.get("error"))
    return out


# =============================================================================
# httpx-Transport: der NAS-Code merkt vom Umweg nichts
# =============================================================================
class AgentTransport(httpx.AsyncBaseTransport):
    """Stellt jede Anfrage über den Agent ``agent_id``."""

    def __init__(self, agent_id: int, verify: bool = False, default_timeout: float = 30.0) -> None:
        self.agent_id = agent_id
        self.verify = verify
        self.default_timeout = default_timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        to = request.extensions.get("timeout") or {}
        values = [v for v in (to.get("read"), to.get("connect"), to.get("pool")) if v]
        timeout = max(values) if values else self.default_timeout
        headers = {k: v for k, v in request.headers.items()}
        try:
            res = await forward(self.agent_id, method=request.method, url=str(request.url), headers=headers,
                                body=body, timeout=timeout, verify=self.verify)
        except RelayError as exc:
            # Für den Aufrufer eine gewöhnliche "keine Verbindung" - mit Klartext.
            raise httpx.ConnectError(f"über Agent: {exc}", request=request) from exc
        err = res.get("error")
        if err:
            kind = res.get("error_kind") or "other"
            text = f"über Agent: {err}"
            if kind == "timeout":
                raise httpx.ConnectTimeout(text, request=request)
            if kind == "tls" and "certificate" in err.lower():
                raise httpx.ConnectError(f"{text} (CERTIFICATE_VERIFY_FAILED)", request=request)
            raise httpx.ConnectError(text, request=request)
        raw_headers: List[Tuple[str, str]] = []
        for k, vals in (res.get("headers") or {}).items():
            if k.lower() in _DROP_RESPONSE:
                continue
            for v in vals if isinstance(vals, list) else [vals]:
                raw_headers.append((k, str(v)))
        return httpx.Response(status_code=int(res.get("status") or 502), headers=raw_headers,
                              content=res.get("body") or b"", request=request)


def transport(agent_id: Optional[int], verify: bool = False) -> Optional[AgentTransport]:
    """Transport für ``httpx.AsyncClient`` - oder None für den direkten Weg."""
    return AgentTransport(agent_id, verify=verify) if agent_id else None


async def peer_certificate(agent_id: int, url: str, timeout: float = 8.0) -> Optional[bytes]:
    """Zertifikat (DER) der Gegenstelle, vom Agent aus gesehen."""
    try:
        res = await forward(agent_id, method="HEAD", url=url, timeout=timeout, verify=False, want_cert=True)
    except RelayError:
        return None
    return res.get("cert")
