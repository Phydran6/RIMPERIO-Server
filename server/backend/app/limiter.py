# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.09.15.20.00.00
# Beschreibung: RIMPERIO - Rate Limiter (slowapi) + echte Client-IP hinter Caddy
# ==============================================================================

from starlette.requests import Request
from slowapi import Limiter


def client_ip(request: Request) -> str:
    """IP des echten Absenders.

    Das Backend ist nur im Docker-Netz erreichbar (expose, kein ports), jede
    Anfrage kommt also ueber Caddy. request.client.host ist darum immer die
    Container-IP von Caddy (z.B. 172.18.0.3). Caddy setzt X-Forwarded-For und
    haengt den direkten Absender als LETZTEN Eintrag an - der ist nicht
    faelschbar, weiter vorne stehende Eintraege schon.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def agent_key(request: Request) -> str:
    """Limit-Schluessel fuer Agent-Lieferungen: der Agent, nicht die IP.

    Hinter NAT oder einem Proxy teilen sich sonst alle Agents EIN Limit - dann
    bekommen alle 429, und ein Agent, der bei Fehlern wiederholt, heizt das
    weiter an (so geschehen am 2026-10-10). Gespeichert wird nur ein Abdruck
    des Schluessels, nie der Schluessel selbst.
    """
    import hashlib
    auth = request.headers.get("authorization", "")
    if auth:
        return "agent:" + hashlib.sha256(auth.encode("utf-8", "replace")).hexdigest()[:24]
    return client_ip(request)


# Vorher get_remote_address -> alle Nutzer teilten sich das Limit der Caddy-IP.
limiter = Limiter(key_func=client_ip)
