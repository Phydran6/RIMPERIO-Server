# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.07.23.00.00
# Beschreibung: RIMPERIO - Erkennung eines externen HTTPS-Proxys vor Caddy
#
# Steht vor RIMPERIO ein weiterer Reverse Proxy (Nginx Proxy Manager, Traefik,
# Firewall, ...), der HTTPS beendet und unverschluesselt an Caddy (Port 80)
# weiterreicht, sieht Caddy selbst nur HTTP. Die Systemstatus-Pruefung meldete
# dann "Nur HTTP", obwohl die Nutzer verschluesselt zugreifen.
#
# Zwei voneinander unabhaengige Hinweise werden ausgewertet:
#
# 1. Kopfzeilen von Caddy (siehe ROUTES_SNIPPET in routes/caddy.py), die ein
#    Client NICHT faelschen kann, weil Caddy sie ueberschreibt:
#      X-Rimperio-Edge-Scheme  - Schema, mit dem die Anfrage bei Caddy ankam
#      X-Rimperio-Outer-Proto  - X-Forwarded-Proto, wie es bei Caddy ankam
#      X-Rimperio-Peer         - direkter Absender bei Caddy (der Proxy)
#    Kommt die Anfrage per HTTP bei Caddy an, meldet der Absender aber HTTPS,
#    steht ein externer HTTPS-Proxy davor.
#
# 2. Die Adresse, die der Browser benutzt (Origin/Referer). Steht dort https,
#    kam die Anfrage bei Caddy aber per HTTP an, sitzt dazwischen jemand, der
#    TLS beendet. Das greift auch, wenn der Proxy kein X-Forwarded-Proto setzt
#    (HAProxy, manche Firewalls) oder die gespeicherte Caddyfile von Hand
#    geschrieben ist und die Kopfzeilen aus 1. fehlen.
#
# Erkannt, aber Caddy vertraut dem Proxy noch nicht? Dann uebernimmt
# routes/caddy.py (POST /api/caddy/external-proxy/auto) den Proxy, sobald ein
# Administrator die Oberflaeche ueber ihn oeffnet - nur solange Caddy selbst
# reines HTTP spricht, und nur fuer genau die IP des Proxys.
# ==============================================================================

import ipaddress
import time
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request

# Nach so langer Zeit ohne Anfrage ueber den Proxy gilt die Erkennung als veraltet.
STALE_AFTER = 7 * 24 * 3600

# CGNAT / Tailscale / Carrier-Grade-NAT: fuer Python nicht "privat", fuer
# Heimnetze mit VPN zum Proxy aber genau der Normalfall.
_SHARED = ipaddress.ip_network("100.64.0.0/10")

_state = {
    "last_seen": 0.0,       # Zeitstempel der letzten Anfrage ueber den externen Proxy
    "peer": None,           # IP des Proxys (falls bekannt)
    "host": None,           # Hostname, unter dem der Proxy RIMPERIO anbietet
    "trusted": False,       # Caddy vertraut dem Proxy (X-Forwarded-Proto kommt durch)
    "via": None,            # "headers" oder "browser" - woran erkannt
    "last_direct_https": 0.0,  # letzte Anfrage, die Caddy selbst per HTTPS bekam
    "headers_seen": False,  # Caddy liefert die Erkennungs-Kopfzeilen ueberhaupt
}


def is_internal(address: Optional[str]) -> bool:
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return False
    if ip.version == 4 and ip in _SHARED:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local


# Alter Name, falls ein anderes Modul ihn noch benutzt.
_is_private = is_internal


def _first(value: Optional[str]) -> str:
    return (value or "").split(",")[0].strip().lower()


def _browser_scheme_host(request: Request):
    for name in ("origin", "referer"):
        raw = request.headers.get(name) or ""
        if not raw or raw == "null":
            continue
        try:
            parts = urlsplit(raw)
        except ValueError:
            continue
        if parts.scheme in ("http", "https") and parts.hostname:
            return parts.scheme, parts.netloc
    return None, None


def outer_https(request: Request) -> bool:
    """Kam DIESE Anfrage ueber einen externen HTTPS-Proxy?

    Fuer public_url: auch wenn Caddy dem Proxy (noch) nicht vertraut und
    X-Forwarded-Proto deshalb "http" lautet, soll die Adresse stimmen.
    """
    headers = request.headers
    edge = _first(headers.get("x-rimperio-edge-scheme"))
    if edge == "https":
        return False          # Caddy hat TLS selbst beendet - dann stimmt X-Forwarded-Proto
    if edge and _first(headers.get("x-rimperio-outer-proto")) == "https" \
            and is_internal(headers.get("x-rimperio-peer")):
        return True
    scheme, _ = _browser_scheme_host(request)
    return scheme == "https" and _first(headers.get("x-forwarded-proto")) != "https"


def observe(request: Request) -> None:
    """Wird fuer jede API-Anfrage aufgerufen - nur Speicherzugriffe, kein I/O."""
    headers = request.headers
    edge = _first(headers.get("x-rimperio-edge-scheme"))
    if edge:
        _state["headers_seen"] = True
    if edge == "https":
        _state["last_direct_https"] = time.time()
        return

    forwarded_proto = _first(headers.get("x-forwarded-proto"))
    peer = (headers.get("x-rimperio-peer") or "").strip() or None
    outer = _first(headers.get("x-rimperio-outer-proto"))

    via = None
    if edge and outer == "https":
        # Ein externer Proxy sitzt praktisch immer im eigenen Netz. Kommt die
        # Behauptung "HTTPS" von einer oeffentlichen Adresse direkt an Caddy,
        # ist es eher ein gefaelschter Header als ein Proxy - nicht werten.
        if not is_internal(peer):
            return
        via = "headers"
    else:
        scheme, netloc = _browser_scheme_host(request)
        # Die Seite ist per https offen, bei Caddy kam aber http an: dazwischen
        # beendet jemand TLS. Ohne Edge-Kopfzeile sagt X-Forwarded-Proto, was
        # Caddy gesehen hat (er ueberschreibt es fuer nicht vertraute Absender).
        if scheme != "https" or edge not in ("", "http"):
            return
        if not edge and forwarded_proto == "https":
            return            # alte Caddyfile, die einem Proxy vertraut - schon in Ordnung
        if peer and not is_internal(peer):
            return
        via = "browser"

    _state["last_seen"] = time.time()
    _state["peer"] = peer or _state["peer"]
    _state["via"] = via
    host = _first(headers.get("x-forwarded-host") or headers.get("host"))
    if via == "browser":
        _, netloc = _browser_scheme_host(request)
        host = (netloc or host or "").lower()
    _state["host"] = host or None
    # Vertraut Caddy dem Proxy, reicht es dessen X-Forwarded-Proto unveraendert durch.
    _state["trusted"] = forwarded_proto == "https"


def status() -> dict:
    now = time.time()
    seen = _state["last_seen"]
    active = bool(seen) and now - seen < STALE_AFTER
    return {
        "detected": active,
        "peer": _state["peer"] if active else None,
        "host": _state["host"] if active else None,
        "trusted": bool(_state["trusted"]) if active else False,
        "via": _state["via"] if active else None,
        "last_seen": int(seen) if seen else None,
        "headers_seen": bool(_state["headers_seen"]),
        "direct_https_recent": bool(_state["last_direct_https"])
            and now - _state["last_direct_https"] < STALE_AFTER,
    }


def mark_trusted() -> None:
    """Nach dem Umschalten auf Modus "external": bis zur naechsten Anfrage als vertraut zeigen."""
    _state["trusted"] = True
