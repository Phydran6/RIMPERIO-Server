# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.07.23.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Unter welcher Adresse ist dieser Server erreichbar?
#
# Frueher hat jede Stelle das selbst ausgerechnet (QR-Code fuer die App, SSO,
# Passkeys, Agent-Befehle), jeweils aus X-Forwarded-Proto/-Host. Hinter einem
# externen HTTPS-Proxy (Nginx Proxy Manager, Traefik, Firewall) ging das
# schief: Caddy vertraut dem Proxy nicht und ueberschreibt X-Forwarded-Proto
# mit "http". Der QR-Code nannte dann http://... und die App lehnte ihn ab, weil
# sie nur ueber https verbindet.
#
# Reihenfolge, in der hier entschieden wird:
#   1. SITE_URL aus der .env                     (bewusst gesetzt)
#   2. "Oeffentliche Adresse" aus der Oberflaeche (Einstellungen -> HTTPS)
#   3. die Adresse, die der Browser gerade benutzt (Hinweis, Origin, Referer)
#   4. die Kopfzeilen von Caddy, korrigiert um einen erkannten externen Proxy
#
# Fuer Hintergrundaufgaben ohne Anfrage (Meldungen, Agent-Befehle) gibt es
# site_url(): 1, 2 oder die zuletzt ueber einen externen Proxy gesehene
# https-Adresse.
# ==============================================================================

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request

from . import proxy_detect
from .config import settings

SETTING_KEY = "caddy_public_url"

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")

# In der Datenbank gespeicherte Adresse, beim Start und beim Speichern geladen.
_saved = {"value": ""}


def normalize(value: Optional[str]) -> str:
    """Macht aus einer Eingabe "scheme://host[:port]" oder "" (ungueltig/leer).

    Ohne Schema wird https angenommen: wer eine Adresse fuer Handy und Agents
    eintraegt, meint die verschluesselte.
    """
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname
    if ":" in host:                          # IPv6
        host_part = f"[{host}]"
    elif _HOST_RE.match(host):
        host_part = host.lower()
    else:
        return ""
    if port and not ((parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)):
        host_part += f":{port}"
    return f"{parts.scheme}://{host_part}"


def set_saved(value: Optional[str]) -> None:
    _saved["value"] = normalize(value)


def saved() -> str:
    return _saved["value"]


async def load(db) -> None:
    """Gespeicherte Adresse aus der Datenbank in den Speicher holen."""
    from sqlalchemy import select
    from .models import Setting
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    set_saved(row.value if row and isinstance(row.value, str) else "")


# Platzhalter aus der Beispiel-.env: wer sie unveraendert kopiert hat, bekaeme
# sonst einen QR-Code mit https://deine-domain.de.
_PLACEHOLDER_HOSTS = ("deine-domain.de", "example.com", "example.org", "your-domain.com")


def env_site_url() -> str:
    value = normalize(settings.site_url)
    host = value.split("://", 1)[-1].split(":")[0]
    if any(host == p or host.endswith("." + p) for p in _PLACEHOLDER_HOSTS):
        return ""
    return value


def configured() -> str:
    """Fest eingestellte Adresse: SITE_URL, sonst die aus der Oberflaeche."""
    return env_site_url() or saved()


def detected() -> str:
    """Zuletzt ueber einen externen HTTPS-Proxy gesehene Adresse (oder "")."""
    seen = proxy_detect.status()
    if seen.get("detected") and seen.get("host"):
        return normalize("https://" + seen["host"])
    return ""


def site_url() -> str:
    """Fuer Aufgaben ohne Anfrage: eingestellt, sonst erkannt, sonst ""."""
    return configured() or detected()


def _browser_origin(request: Request) -> str:
    origin = request.headers.get("origin") or ""
    if origin and origin != "null":
        found = normalize(origin)
        if found:
            return found
    referer = request.headers.get("referer") or ""
    if referer:
        try:
            parts = urlsplit(referer)
            return normalize(f"{parts.scheme}://{parts.netloc}")
        except ValueError:
            return ""
    return ""


def effective_scheme(request: Request) -> str:
    """http oder https - so, wie der Nutzer wirklich zugreift."""
    forwarded = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http")
    forwarded = forwarded.split(",")[0].strip().lower()
    if forwarded == "https":
        return "https"
    if proxy_detect.outer_https(request):
        return "https"
    return forwarded or "http"


def _from_headers(request: Request) -> str:
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host")
            or request.url.netloc or "").split(",")[0].strip()
    return normalize(f"{effective_scheme(request)}://{host}") if host else ""


def for_request(request: Request, hint: Optional[str] = None, *, prefer_https: bool = False) -> dict:
    """Oeffentliche Adresse fuer diese Anfrage.

    hint: was der Browser selbst als Adresse sieht (window.location.origin).
    prefer_https: Faellt die Wahl auf eine http-Adresse, ist aber eine
    https-Adresse ueber einen externen Proxy bekannt, nimm die (fuer die App,
    die nur https akzeptiert - etwa wenn das Admin-Fenster ueber die LAN-IP
    offen ist).

    Ergebnis: {"url": ..., "source": "site_url|saved|browser|headers|detected"}
    """
    if env_site_url():
        return {"url": env_site_url(), "source": "site_url"}
    if saved():
        return {"url": saved(), "source": "saved"}

    url, source = normalize(hint), "browser"
    if not url:
        url = _browser_origin(request)
    if not url:
        url, source = _from_headers(request), "headers"

    if prefer_https and not url.startswith("https://"):
        better = detected()
        if better:
            return {"url": better, "source": "detected"}
    return {"url": url, "source": source}


def base_url(request: Request, hint: Optional[str] = None) -> str:
    return for_request(request, hint)["url"]
