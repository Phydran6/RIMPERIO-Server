# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.07.21.00.00
# Beschreibung: RIMPERIO - Erkennung eines externen HTTPS-Proxys vor Caddy
#
# Steht vor RIMPERIO ein weiterer Reverse Proxy (Nginx Proxy Manager, Traefik,
# Firewall, ...), der HTTPS beendet und unverschluesselt an Caddy (Port 80)
# weiterreicht, sieht Caddy selbst nur HTTP. Die Systemstatus-Pruefung meldete
# dann "Nur HTTP", obwohl die Nutzer verschluesselt zugreifen.
#
# Caddy reicht dafuer zwei Kopfzeilen an das Backend durch (siehe ROUTES_SNIPPET
# in routes/caddy.py), die ein Client NICHT faelschen kann, weil Caddy sie
# ueberschreibt:
#   X-Rimperio-Edge-Scheme  - Schema, mit dem die Anfrage bei Caddy ankam
#   X-Rimperio-Outer-Proto  - X-Forwarded-Proto, wie es bei Caddy ankam
#   X-Rimperio-Peer         - direkter Absender bei Caddy (der Proxy)
# Kommt die Anfrage per HTTP bei Caddy an, meldet der Absender aber HTTPS,
# steht ein externer HTTPS-Proxy davor.
# ==============================================================================

import ipaddress
import time
from typing import Optional

from starlette.requests import Request

# Nach so langer Zeit ohne Anfrage ueber den Proxy gilt die Erkennung als veraltet.
STALE_AFTER = 7 * 24 * 3600

_state = {
    "last_seen": 0.0,       # Zeitstempel der letzten Anfrage ueber den externen Proxy
    "peer": None,           # IP des Proxys
    "host": None,           # Hostname, unter dem der Proxy RIMPERIO anbietet
    "trusted": False,       # Caddy vertraut dem Proxy (Modus "external" aktiv)
    "last_direct_https": 0.0,  # letzte Anfrage, die Caddy selbst per HTTPS bekam
}


def _is_private(address: Optional[str]) -> bool:
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def observe(request: Request) -> None:
    """Wird fuer jede API-Anfrage aufgerufen - nur Speicherzugriffe, kein I/O."""
    headers = request.headers
    edge = (headers.get("x-rimperio-edge-scheme") or "").strip().lower()
    if not edge:
        return  # alte Caddy-Konfiguration ohne die Kopfzeilen
    if edge == "https":
        _state["last_direct_https"] = time.time()
        return
    outer = (headers.get("x-rimperio-outer-proto") or "").split(",")[0].strip().lower()
    if outer != "https":
        return
    peer = (headers.get("x-rimperio-peer") or "").strip()
    # Ein externer Proxy sitzt praktisch immer im eigenen Netz. Kommt die
    # Behauptung "HTTPS" von einer oeffentlichen Adresse direkt an Caddy, ist es
    # eher ein gefaelschter Header als ein Proxy - nicht werten.
    if not _is_private(peer):
        return
    _state["last_seen"] = time.time()
    _state["peer"] = peer
    _state["host"] = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip() or None
    # Vertraut Caddy dem Proxy, reicht es dessen X-Forwarded-Proto unveraendert durch.
    _state["trusted"] = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() == "https"


def status() -> dict:
    now = time.time()
    seen = _state["last_seen"]
    active = bool(seen) and now - seen < STALE_AFTER
    return {
        "detected": active,
        "peer": _state["peer"] if active else None,
        "host": _state["host"] if active else None,
        "trusted": bool(_state["trusted"]) if active else False,
        "last_seen": int(seen) if seen else None,
        "direct_https_recent": bool(_state["last_direct_https"])
            and now - _state["last_direct_https"] < STALE_AFTER,
    }
