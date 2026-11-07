# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.07.23.00.00
# Beschreibung: RIMPERIO - Caddy Management API (Config Apply + Cert Upload)
#
# Grundsatz: Das UI darf sich NIE aussperren.
#   * Port 80 liefert immer die Oberflaeche aus (kein Zwangs-Redirect auf HTTPS).
#   * Zugriff per IP und per FQDN funktioniert parallel, auf 80 wie auf 443.
#   * Jede Konfiguration wird vor dem Anwenden von Caddy geprueft (/adapt);
#     schlaegt das Anwenden fehl, wird die vorher laufende Config zurueckgeholt.
# ==============================================================================

import asyncio
import ipaddress
import json
import logging
import re
import socket
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import caddy_admin
from ..auth import get_current_admin
from .. import proxy_detect, public_url
from ..config import settings
from ..database import get_db, async_session
from ..models import Setting
from ..schemas import (
    CaddyApplyRequest,
    CaddyConfigResponse,
    CaddyTemplateRequest,
)

router = APIRouter(prefix="/api/caddy", tags=["Caddy"])
logger = logging.getLogger("rimperio.caddy")

# Routen-Snippet: einmal definiert, in jedem Site-Block importiert.
ROUTES_SNIPPET = """(rimperio_routes) {
    # Schutz-Kopfzeilen fuer die Oberflaeche (die API setzt ihre eigenen).
    # -Server: nicht verraten, welcher Proxy in welcher Fassung hier steht.
    header {
        -Server
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
    }
    handle /api/* {
        reverse_proxy backend:8000 {
            # Erkennung eines externen HTTPS-Proxys + echte Client-IP.
            # Caddy ueberschreibt diese Kopfzeilen, ein Client kann sie nicht faelschen.
            header_up X-Rimperio-Edge-Scheme {http.request.scheme}
            header_up X-Rimperio-Outer-Proto {http.request.header.X-Forwarded-Proto}
            header_up X-Rimperio-Peer {http.request.remote.host}
            header_up X-Rimperio-Client-IP {client_ip}
        }
    }
    handle {
        reverse_proxy frontend:80
    }
}
"""

# Aeltere gespeicherte Konfigurationen kennen die Kopfzeilen oben noch nicht.
# Beim Start wird dieser Abschnitt woertlich ersetzt (siehe _upgrade_caddyfile).
_LEGACY_API_HANDLE = """    handle /api/* {
        reverse_proxy backend:8000
    }"""
_CURRENT_API_HANDLE = ROUTES_SNIPPET[
    ROUTES_SNIPPET.index("    handle /api/*"):ROUTES_SNIPPET.index("    handle {")
].rstrip("\n")

# Merker in der Caddyfile, an dem der Modus "externer Proxy" wiedererkannt wird.
EXTERNAL_MARKER = "# rimperio-mode: external"

# Admin-API nur ueber den Unix-Socket (siehe app/caddy_admin.py). Vor jedem
# Laden setzt caddy_admin.enforce_admin die tatsaechlich passende Adresse ein.
ADMIN_BLOCK = "{\n    admin " + caddy_admin.SOCKET_LISTEN + "\n}\n\n"

# HTTP-Block: faengt alles auf Port 80, was kein spezifischerer Block bedient.
# Bewusst OHNE redir - sonst ist die Oberflaeche bei kaputtem TLS/DNS nicht mehr
# erreichbar. Auf den Hinweis "bitte HTTPS nutzen" weist das Frontend selbst hin.
HTTP_BLOCK = ":80 {\n    import rimperio_routes\n}\n"

# Fallback, wenn nichts gespeichert ist (entspricht caddy/Caddyfile im Repo).
DEFAULT_CADDYFILE = ADMIN_BLOCK + ROUTES_SNIPPET + "\n" + HTTP_BLOCK

# Die E-Mail landet woertlich in der Caddyfile. Ohne Pruefung liesse sich ueber
# einen Zeilenumbruch darin beliebige Caddy-Konfiguration einschleusen.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,63}$")

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")


# ==============================================================================
# Caddyfile bauen
# ==============================================================================

def _clean_hosts(hosts: Optional[List[str]]) -> List[str]:
    """Prueft und entdoppelt zusaetzliche Adressen (IPs oder Hostnamen)."""
    cleaned: List[str] = []
    for raw in hosts or []:
        host = (raw or "").strip().lower()
        if not host:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME_RE.match(host):
                raise HTTPException(status_code=400, detail=f"Ungültige Adresse: {raw}")
        if host not in cleaned:
            cleaned.append(host)
    return cleaned


def _clean_proxies(proxies: Optional[List[str]]) -> List[str]:
    """Adressen des externen Proxys: IPs oder Netze (CIDR), sonst private_ranges."""
    cleaned: List[str] = []
    for raw in proxies or []:
        value = (raw or "").strip()
        if not value:
            continue
        if value.lower() == "private_ranges":
            value = "private_ranges"
        else:
            try:
                value = str(ipaddress.ip_network(value, strict=False))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Ungültige Proxy-Adresse: {raw}")
        if value not in cleaned:
            cleaned.append(value)
    # Vorgabe: alle privaten Netze plus CGNAT (100.64.0.0/10) - dort landen
    # Tailscale und viele VPN-Strecken zwischen Proxy und Server.
    return cleaned or ["private_ranges", "100.64.0.0/10"]


def detect_mode(caddyfile: Optional[str]) -> Optional[str]:
    """Erkennt aus einer (von uns erzeugten) Caddyfile den Modus - fuers UI."""
    text = caddyfile or ""
    if EXTERNAL_MARKER in text or "trusted_proxies" in text:
        return "external"
    if "custom.crt" in text:
        return "custom"
    match = re.search(r"^https://\S+ \{\n\s+tls (\S+)", text, re.MULTILINE)
    if match:
        return "internal" if match.group(1) == "internal" else "letsencrypt"
    if "https://" not in text:
        return "http"
    return None


def detect_domain(caddyfile: Optional[str]) -> Optional[str]:
    text = caddyfile or ""
    match = (re.search(r"^https://([A-Za-z0-9.\-]+) \{", text, re.MULTILINE)
             or re.search(r"# Oeffentlich erreichbar ueber https://([A-Za-z0-9.\-]+)", text))
    return match.group(1) if match else None


def detect_trusted_proxies(caddyfile: Optional[str]) -> List[str]:
    match = re.search(r"trusted_proxies static ([^\n]+)", caddyfile or "")
    return match.group(1).split() if match else []


# Jede Schreibweise des alten API-Blocks: beliebige Einrueckung, Leerzeilen,
# Kommentare oder ein flush_interval darin. Frueher wurde nur der woertliche
# Text ersetzt - eine von Hand angefasste Caddyfile bekam die Kopfzeilen nie,
# und die Proxy-Erkennung blieb stumm.
_LEGACY_API_RE = re.compile(
    r"handle /api/\* \{\s*reverse_proxy backend:8000(?:\s*\{(?P<inner>[^{}]*)\})?\s*\}",
    re.MULTILINE,
)


def _upgrade_caddyfile(caddyfile: str) -> str:
    """Ergaenzt gespeicherte Altkonfigurationen um die Erkennungs-Kopfzeilen."""
    if "X-Rimperio-Edge-Scheme" in caddyfile:
        return caddyfile
    if _LEGACY_API_HANDLE in caddyfile:
        return caddyfile.replace(_LEGACY_API_HANDLE, _CURRENT_API_HANDLE)

    def _replace(match: "re.Match") -> str:
        inner = (match.group("inner") or "").strip("\n")
        extra = "".join(f"            {line.strip()}\n" for line in inner.splitlines() if line.strip())
        return ("handle /api/* {\n"
                "        reverse_proxy backend:8000 {\n"
                "            header_up X-Rimperio-Edge-Scheme {http.request.scheme}\n"
                "            header_up X-Rimperio-Outer-Proto {http.request.header.X-Forwarded-Proto}\n"
                "            header_up X-Rimperio-Peer {http.request.remote.host}\n"
                "            header_up X-Rimperio-Client-IP {client_ip}\n"
                f"{extra}"
                "        }\n"
                "    }")

    return _LEGACY_API_RE.sub(_replace, caddyfile)


def _build_template(
    domain: Optional[str],
    mode: str,
    letsencrypt_email: Optional[str] = None,
    extra_hosts: Optional[List[str]] = None,
    https_only: bool = False,
    trusted_proxies: Optional[List[str]] = None,
) -> str:
    """Baut eine Caddyfile.

    mode:
      http        - nur Port 80 (kein Zertifikat)
      letsencrypt - Zertifikat automatisch von Let's Encrypt (FQDN muss oeffentlich sein)
      custom      - hochgeladenes Zertifikat (custom.crt/custom.key)
      internal    - Caddys eigene CA (selbstsigniert, Browser warnt, funktioniert offline)
      external    - ein anderer Server davor beendet HTTPS und reicht per HTTP an
                    Port 80 weiter; Caddy vertraut dessen X-Forwarded-* Kopfzeilen,
                    damit Anmeldung, Passkeys und SSO die https-Adresse kennen

    Port 80 bleibt in JEDEM Modus als Zugang erhalten.
    """
    domain = (domain or "").strip().lower()
    hosts = _clean_hosts(extra_hosts)
    blocks = [ADMIN_BLOCK, ROUTES_SNIPPET, "\n"]

    if mode == "http":
        blocks.append(HTTP_BLOCK)
        return "".join(blocks)

    if mode == "external":
        if domain and not _HOSTNAME_RE.match(domain):
            raise HTTPException(status_code=400, detail=f"Ungültiger FQDN: {domain}")
        proxies = _clean_proxies(trusted_proxies)
        comment = f"    {EXTERNAL_MARKER}\n"
        if domain:
            comment += f"    # Oeffentlich erreichbar ueber https://{domain} (TLS beim externen Proxy)\n"
        admin = (
            "{\n    admin " + caddy_admin.SOCKET_LISTEN + "\n" + comment
            + "    servers {\n"
            + f"        trusted_proxies static {' '.join(proxies)}\n"
            + "        client_ip_headers X-Forwarded-For X-Real-IP\n"
            + "    }\n}\n\n"
        )
        return admin + ROUTES_SNIPPET + "\n" + HTTP_BLOCK

    if mode not in ("letsencrypt", "custom", "internal"):
        raise HTTPException(status_code=400, detail=f"Unbekannter Modus: {mode}")

    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="FQDN erforderlich für HTTPS")
    if not _HOSTNAME_RE.match(domain):
        raise HTTPException(status_code=400, detail=f"Ungültiger FQDN: {domain}")

    if mode == "custom":
        cert_dir = Path(settings.caddy_certs_dir)
        if not ((cert_dir / "custom.crt").exists() and (cert_dir / "custom.key").exists()):
            raise HTTPException(
                status_code=400,
                detail="Kein eigenes Zertifikat hinterlegt - bitte zuerst Zertifikat und Key hochladen.",
            )
        tls_line = "    tls /etc/caddy/certs/custom.crt /etc/caddy/certs/custom.key"
    elif mode == "letsencrypt":
        letsencrypt_email = (letsencrypt_email or "").strip()
        if not letsencrypt_email:
            raise HTTPException(status_code=400, detail="E-Mail für Let's Encrypt erforderlich")
        if not _EMAIL_RE.match(letsencrypt_email):
            raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse für Let's Encrypt")
        tls_line = f"    tls {letsencrypt_email}"
    else:  # internal
        tls_line = "    tls internal"

    # HTTPS fuer den FQDN
    blocks.append(f"https://{domain} {{\n{tls_line}\n    import rimperio_routes\n}}\n\n")

    # HTTPS zusaetzlich per IP / weiteren Namen - immer mit Caddys interner CA,
    # denn fuer eine IP gibt es kein oeffentliches Zertifikat.
    if hosts:
        addresses = " ".join(f"https://{host}" for host in hosts)
        blocks.append(f"{addresses} {{\n    tls internal\n    import rimperio_routes\n}}\n\n")

    if https_only:
        blocks.append(_https_only_block(domain, hosts))
        return "".join(blocks)

    # Port 80 bleibt nutzbar (Rettungsweg + Zugriff per IP ohne Zertifikatswarnung)
    blocks.append(HTTP_BLOCK)
    return "".join(blocks)


def _https_only_block(domain: str, hosts: List[str]) -> str:
    """Port 80 als reine Weiterleitung auf HTTPS.

    Ohne das gehen Passwoerter, Sitzungs-Tokens und Agent-Schluessel ueber
    Port 80 im Klartext durchs Netz, sobald jemand die http://-Adresse nutzt.

    * 308 statt 301: Agents, die per POST liefern, behalten Methode und Inhalt.
    * Adressen mit eigenem Zertifikat (FQDN und Zusatzadressen) bleiben beim
      gleichen Namen; alles andere (etwa die nackte IP ohne Zertifikat) geht
      auf den FQDN - sonst landete der Browser bei einem Zertifikatsfehler.
    * Let's-Encrypt-Pruefungen (/.well-known/acme-challenge) beantwortet Caddy
      auf Port 80 weiterhin selbst, vor dieser Weiterleitung.
    * Rettungsweg, falls HTTPS klemmt: CADDY_FORCE_HTTP=true in der .env und
      das Backend neu starten.
    """
    known = " ".join([domain, *hosts])
    return (":80 {\n"
            f"    @tls_host host {known}\n"
            "    redir @tls_host https://{host}{uri} 308\n"
            f"    redir https://{domain}{{uri}} 308\n"
            "}\n")


def _dns_warning(domain: str) -> Optional[str]:
    """Prueft die Aufloesbarkeit - als Hinweis, nicht als Abbruchgrund.

    Im Container kann der Resolver ein anderer sein als im restlichen Netz;
    ein harter Abbruch hier hat frueher funktionierende Setups blockiert.
    """
    if not domain:
        return None
    try:
        socket.getaddrinfo(domain, None)
        return None
    except socket.gaierror:
        return (
            f"'{domain}' ist aus dem Backend-Container heraus nicht auflösbar. "
            "Für Let's Encrypt muss der Name öffentlich auf diesen Server zeigen. "
            "DNS lässt sich unter Einstellungen → DNS anpassen."
        )


# ==============================================================================
# Caddy Admin API
# ==============================================================================

def _admin_url(path: str) -> str:
    """Pfad fuer caddy_admin.client() - die Basis (Socket oder URL) setzt der Client."""
    return path


async def _adapt_caddyfile(caddyfile: str) -> dict:
    """Wandelt die Caddyfile in JSON um - dient als Syntaxprüfung OHNE Anwenden."""
    try:
        async with caddy_admin.client(timeout=10.0) as client:
            resp = await client.post(
                _admin_url("/adapt"),
                content=caddy_admin.enforce_admin(caddyfile).encode("utf-8"),
                headers={"Content-Type": "text/caddyfile"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Caddy Admin API nicht erreichbar: {exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Caddyfile ungültig: {resp.text.strip()}")
    return resp.json()


async def _get_running_config() -> Optional[dict]:
    """Aktuell laufende Config (fuer Rollback). None, wenn nicht lesbar."""
    try:
        async with caddy_admin.client(timeout=5.0) as client:
            resp = await client.get(_admin_url("/config/"))
            if resp.status_code < 400:
                return resp.json()
    except Exception as exc:
        logger.warning("Laufende Caddy-Config nicht lesbar: %s", exc)
    return None


async def _load_json_config(config: dict) -> None:
    async with caddy_admin.client(timeout=10.0) as client:
        await client.post(
            _admin_url("/load"),
            json=config,
            headers={"Content-Type": "application/json"},
        )


async def _apply_caddyfile(caddyfile: str) -> None:
    url = _admin_url("/load")
    # must-revalidate: Caddy laedt auch bei unveraenderter Config neu. Sonst
    # ueberspringt Caddy das Laden und behaelt ein ausgetauschtes Zertifikat
    # (gleicher Dateiname custom.crt) weiter im Speicher.
    headers = {"Content-Type": "text/caddyfile", "Cache-Control": "must-revalidate"}
    try:
        async with caddy_admin.client(timeout=20.0) as client:
            resp = await client.post(url, content=caddy_admin.enforce_admin(caddyfile).encode("utf-8"),
                                     headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Caddy Admin API nicht erreichbar: {exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text or "Caddy-Fehler beim Laden")


async def _caddy_alive() -> bool:
    try:
        async with caddy_admin.client(timeout=5.0) as client:
            resp = await client.get(_admin_url("/config/"))
            return resp.status_code < 500
    except Exception:
        return False


# ==============================================================================
# Settings-Helfer
# ==============================================================================

async def _save_setting(db: AsyncSession, key: str, value) -> None:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        db.add(setting)
    await db.commit()


async def _get_saved_caddyfile(db: AsyncSession) -> Optional[str]:
    result = await db.execute(select(Setting).where(Setting.key == "caddy_caddyfile"))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


# ==============================================================================
# Endpoints
# ==============================================================================

@router.get("/config", response_model=CaddyConfigResponse)
async def get_caddy_config(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    saved = await _get_saved_caddyfile(db)
    running_config = {}
    last_error = None
    try:
        async with caddy_admin.client(timeout=5.0) as client:
            resp = await client.get(_admin_url("/config/"))
            if resp.status_code < 400:
                running_config = resp.json() or {}
            else:
                last_error = f"Caddy antwortet mit HTTP {resp.status_code}"
    except Exception as exc:  # broad: wir zeigen nur Status
        last_error = f"Caddy nicht erreichbar: {exc}"

    cert_dir = Path(settings.caddy_certs_dir)
    cert_present = (cert_dir / "custom.crt").exists() and (cert_dir / "custom.key").exists()

    effective = saved or DEFAULT_CADDYFILE
    return CaddyConfigResponse(
        running_config=running_config,
        saved_caddyfile=effective,
        cert_present=cert_present,
        last_error=last_error,
        detected_mode=detect_mode(effective),
        detected_domain=detect_domain(effective),
        trusted_proxies=detect_trusted_proxies(effective),
        external_proxy=proxy_detect.status(),
        public_url=public_url.saved(),
        public_url_env=public_url.env_site_url(),
        public_url_effective=public_url.site_url(),
    )


@router.post("/apply")
async def apply_caddy_config(
    data: CaddyApplyRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    warnings: List[str] = []

    # Wenn ein Modus mitgeschickt wird, erzwinge eine passende Vorlage.
    # Ohne Modus gilt genau das, was im Editor steht.
    if data.mode:
        if data.mode in ("letsencrypt", "custom", "internal") and not data.domain:
            raise HTTPException(status_code=400, detail="FQDN ist für HTTPS erforderlich")
        effective = _build_template(data.domain, data.mode, data.letsencrypt_email,
                                    data.extra_hosts, trusted_proxies=data.trusted_proxies,
                                    https_only=data.https_only)
        if data.mode == "letsencrypt":
            warning = _dns_warning((data.domain or "").strip())
            if warning:
                warnings.append(warning)
    else:
        effective = (data.caddyfile or "").strip()

    if not effective:
        raise HTTPException(status_code=400, detail="Caddyfile darf nicht leer sein")

    # 1) Syntax pruefen, ohne etwas zu veraendern
    await _adapt_caddyfile(effective)

    # 2) Laufende Config fuer den Rollback merken
    previous = await _get_running_config()

    # 3) Anwenden - bei Fehler den alten Stand zurueckholen
    try:
        await _apply_caddyfile(effective)
    except HTTPException:
        if previous:
            try:
                await _load_json_config(previous)
                logger.warning("Caddy-Config fehlgeschlagen - vorherige Konfiguration wiederhergestellt.")
            except Exception as exc:
                logger.error("Rollback der Caddy-Config fehlgeschlagen: %s", exc)
        raise

    # 4) Kurzer Lebenszeichen-Check
    if not await _caddy_alive():
        if previous:
            try:
                await _load_json_config(previous)
            except Exception as exc:
                logger.error("Rollback der Caddy-Config fehlgeschlagen: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Caddy antwortet nach dem Laden nicht mehr - vorherige Konfiguration wurde wiederhergestellt.",
        )

    if data.save:
        await _save_setting(db, "caddy_caddyfile", effective)

    return {
        "message": "Caddy-Konfiguration geladen",
        "saved": data.save,
        "caddyfile": effective,
        "warnings": warnings,
    }


@router.post("/template")
async def build_caddy_template(
    req: CaddyTemplateRequest,
    _=Depends(get_current_admin),
):
    caddyfile = _build_template(req.domain, req.mode, req.letsencrypt_email,
                                req.extra_hosts, trusted_proxies=req.trusted_proxies,
                                https_only=req.https_only)
    warnings = []
    if req.mode == "letsencrypt":
        warning = _dns_warning((req.domain or "").strip())
        if warning:
            warnings.append(warning)
    return {"caddyfile": caddyfile, "warnings": warnings}


# ==============================================================================
# Oeffentliche Adresse
# ==============================================================================
# Unter dieser Adresse erreichen Handy-App, Agents und SSO den Server. Bisher
# nur per SITE_URL in der .env - die Oberflaeche verwies darauf, bot aber kein
# Feld dafuer. SITE_URL behaelt Vorrang.

class PublicUrlIn(BaseModel):
    url: str = Field(default="", max_length=300)


@router.put("/public-url")
async def set_public_url(data: PublicUrlIn, db: AsyncSession = Depends(get_db),
                         admin=Depends(get_current_admin)):
    raw = (data.url or "").strip()
    value = public_url.normalize(raw)
    if raw and not value:
        raise HTTPException(status_code=400,
                            detail="Ungültige Adresse. Beispiel: https://rimperio.example.com")
    await _save_setting(db, public_url.SETTING_KEY, value)
    public_url.set_saved(value)
    logger.warning("Öffentliche Adresse gesetzt auf %r von %s", value or "(automatisch)",
                   getattr(admin, "username", "?"))
    return {"public_url": value, "effective": public_url.site_url(),
            "env_override": public_url.env_site_url()}


# ==============================================================================
# Externer HTTPS-Proxy: automatisch uebernehmen
# ==============================================================================
# Ruft die Oberflaeche einmal je Sitzung auf, wenn ein Administrator sie
# oeffnet. Kommt GENAU DIESE Anfrage ueber einen externen HTTPS-Proxy aus dem
# eigenen Netz, und spricht Caddy selbst nur HTTP, stellt RIMPERIO auf
# "HTTPS ueber externen Proxy" um und vertraut nur der IP dieses Proxys.
#
# Warum nicht still im Hintergrund bei irgendeiner Anfrage: dann koennte jeder
# im LAN mit einer gefaelschten Kopfzeile seine IP vertrauenswuerdig machen.
# So braucht es eine angemeldete Administrator-Sitzung, die wirklich ueber den
# Proxy kommt. Abschalten: RIMPERIO_AUTO_EXTERNAL_PROXY=false.

def _peer_network(peer: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_network(peer.strip(), strict=False))
    except ValueError:
        return None


def _covered(peer: str, proxies: List[str]) -> bool:
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in proxies:
        if entry == "private_ranges":
            if ip.is_private or ip.is_loopback:
                return True
            continue
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


@router.post("/external-proxy/auto")
async def auto_adopt_external_proxy(request: Request, db: AsyncSession = Depends(get_db),
                                    admin=Depends(get_current_admin)):
    import os
    if (os.getenv("RIMPERIO_AUTO_EXTERNAL_PROXY", "true").strip().lower()
            in ("0", "false", "no", "off")):
        return {"adopted": False, "reason": "disabled"}
    if settings.caddy_force_http:
        return {"adopted": False, "reason": "force_http"}

    if not proxy_detect.outer_https(request):
        return {"adopted": False, "reason": "not_via_proxy"}
    if (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() == "https":
        return {"adopted": False, "reason": "already_trusted"}

    peer = (request.headers.get("x-rimperio-peer") or "").strip()
    network = _peer_network(peer) if peer and proxy_detect.is_internal(peer) else None
    if not network:
        # Ohne Edge-Kopfzeilen (von Hand geschriebene Caddyfile) kennen wir die
        # IP des Proxys nicht - dann nichts automatisch freigeben.
        return {"adopted": False, "reason": "peer_unknown"}

    saved = await _get_saved_caddyfile(db) or DEFAULT_CADDYFILE
    mode = detect_mode(saved)
    proxies = detect_trusted_proxies(saved)
    if mode == "external":
        if _covered(peer, proxies):
            return {"adopted": False, "reason": "already_trusted"}
        proxies = [*proxies, network]
    elif mode == "http":
        proxies = [network]
    else:
        # Caddy hat eigenes TLS (Let's Encrypt, eigenes Zertifikat ...) oder die
        # Caddyfile ist handgeschrieben - nicht ungefragt umbauen.
        return {"adopted": False, "reason": f"mode_{mode or 'custom'}"}

    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "")
    host = host.split(",")[0].strip().split(":")[0].lower()
    origin = public_url.for_request(request)["url"]
    if origin.startswith("https://"):
        host = origin[len("https://"):].split(":")[0].split("/")[0]
    domain = host if host and _HOSTNAME_RE.match(host) and "." in host else (detect_domain(saved) or None)

    caddyfile = _build_template(domain, "external", trusted_proxies=proxies)
    await _adapt_caddyfile(caddyfile)
    previous = await _get_running_config()
    try:
        await _apply_caddyfile(caddyfile)
    except HTTPException:
        if previous:
            try:
                await _load_json_config(previous)
            except Exception as exc:                                # defensiv
                logger.error("Rollback der Caddy-Config fehlgeschlagen: %s", exc)
        raise
    await _save_setting(db, "caddy_caddyfile", caddyfile)
    proxy_detect.mark_trusted()
    logger.warning("Externer HTTPS-Proxy %s automatisch übernommen (https://%s), ausgelöst von %s",
                   peer, domain or "?", getattr(admin, "username", "?"))
    from .. import journal
    journal.fire(category="network", event="caddy.external_proxy", level="notice",
                 message=f"Externer HTTPS-Proxy {peer} automatisch übernommen"
                         + (f" (https://{domain})" if domain else ""),
                 actor=getattr(admin, "username", None) or "system", target=peer)
    return {"adopted": True, "peer": peer, "trusted_proxies": proxies, "domain": domain,
            "message": f"HTTPS über externen Proxy ({peer}) erkannt und übernommen."}


@router.post("/certificates")
async def upload_certificates(
    cert_file: UploadFile = File(...),
    key_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    cert_dir = Path(settings.caddy_certs_dir)
    cert_dir.mkdir(parents=True, exist_ok=True)

    cert_path = cert_dir / "custom.crt"
    key_path = cert_dir / "custom.key"

    cert_bytes = await cert_file.read()
    key_bytes = await key_file.read()

    if len(cert_bytes) < 32 or len(key_bytes) < 32:
        raise HTTPException(status_code=400, detail="Zertifikat/Key scheinen zu klein oder leer")
    if b"-----BEGIN" not in cert_bytes or b"-----BEGIN" not in key_bytes:
        raise HTTPException(status_code=400, detail="Zertifikat und Key müssen im PEM-Format vorliegen")

    not_after = _check_cert_and_key(cert_bytes, key_bytes)

    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    key_path.chmod(0o600)

    await _save_setting(db, "caddy_cert_uploaded", True)

    # Nutzt die gespeicherte Config das eigene Zertifikat, sofort neu laden -
    # sonst liefert Caddy bis zum naechsten Neustart das alte aus.
    reloaded = False
    saved = await _get_saved_caddyfile(db)
    if saved and "custom.crt" in saved:
        await _apply_caddyfile(saved)
        reloaded = True

    message = "Zertifikat gespeichert"
    message += " und Caddy neu geladen." if reloaded else " - wird nach „Apply“ im Modus „Eigenes Zertifikat“ genutzt."
    return {
        "message": message,
        "reloaded": reloaded,
        "not_after": not_after,
        "cert_path": str(cert_path),
        "key_path": str(key_path),
    }


def _check_cert_and_key(cert_bytes: bytes, key_bytes: bytes) -> Optional[str]:
    """Prueft, ob Zertifikat und Key lesbar sind und zusammenpassen."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError:  # ohne cryptography nur die Grundpruefung
        return None

    try:
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Zertifikat ist kein gültiges PEM-Zertifikat") from exc
    try:
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail="Der Key ist passwortgeschützt - bitte ohne Passwort exportieren") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Key ist kein gültiger privater PEM-Schlüssel") from exc

    pub_format = serialization.PublicFormat.SubjectPublicKeyInfo
    der = serialization.Encoding.DER
    if cert.public_key().public_bytes(der, pub_format) != key.public_key().public_bytes(der, pub_format):
        raise HTTPException(status_code=400, detail="Key passt nicht zum Zertifikat (vertauscht oder falsches Paar?)")

    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    return not_after.strftime("%d.%m.%Y")


@router.post("/reset")
async def reset_caddy_config(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Notausstieg: zurück auf reines HTTP (Port 80), gespeicherte Config wird ersetzt."""
    await _apply_caddyfile(DEFAULT_CADDYFILE)
    await _save_setting(db, "caddy_caddyfile", DEFAULT_CADDYFILE)
    return {"message": "Zurückgesetzt auf HTTP (Port 80)", "caddyfile": DEFAULT_CADDYFILE}


async def ensure_caddy_config_on_startup(session_factory=None):
    """
    Laedt die gespeicherte Caddyfile beim App-Start.
    Schlaegt das fehl, wird die HTTP-Grundkonfiguration geladen, damit die
    Oberflaeche in jedem Fall erreichbar bleibt.
    session_factory: optional dependency injection fuer Tests.
    """
    session_factory = session_factory or async_session

    # Caddy startet laut depends_on NACH dem Backend. Ohne Warten ging das
    # Laden der gespeicherten Config beim ersten Start regelmaessig ins Leere -
    # und Caddy blieb bei reinem HTTP.
    if not await wait_for_caddy():
        logger.warning("Caddy nach %ss nicht erreichbar - der Wächter lädt die Config nach, "
                       "sobald Caddy da ist.", STARTUP_WAIT_SECONDS)
        return

    # Notausstieg per Umgebungsvariable: zurueck auf HTTP, gespeicherte Config verwerfen.
    if settings.caddy_force_http:
        logger.warning("CADDY_FORCE_HTTP gesetzt - starte mit reinem HTTP auf Port 80.")
        try:
            await _apply_caddyfile(DEFAULT_CADDYFILE)
            async with session_factory() as db:
                await _save_setting(db, "caddy_caddyfile", DEFAULT_CADDYFILE)
            logger.warning("Reverse-Proxy-Konfiguration zurückgesetzt. CADDY_FORCE_HTTP wieder entfernen.")
        except Exception as exc:
            logger.error("Zurücksetzen auf HTTP fehlgeschlagen: %s", exc)
        return

    saved: Optional[str] = None
    try:
        async with session_factory() as db:
            saved = await _get_saved_caddyfile(db)
    except Exception as exc:
        logger.warning("Caddy-Config konnte nicht geladen werden (DB): %s", exc)
        return

    if not saved:
        # Erster Start mit Domain aus der Installation (install.sh --domain):
        # dieselbe Vorlage wie im Web-UI bauen, anwenden und speichern. Danach
        # gilt nur noch, was unter Einstellungen -> HTTPS & Adresse steht.
        domain = (settings.rimperio_domain or "").strip().lower()
        if not domain:
            return
        email = (settings.rimperio_acme_email or "").strip()
        mode = (settings.rimperio_tls or "").strip().lower() or ("letsencrypt" if email else "internal")
        if mode == "letsencrypt" and not email:
            logger.warning("Let's Encrypt ohne E-Mail nicht möglich - nehme internes Zertifikat.")
            mode = "internal"
        try:
            caddyfile = _build_template(domain, mode, email or None,
                                        https_only=settings.rimperio_https_only)
            await _apply_caddyfile(caddyfile)
            async with session_factory() as db:
                await _save_setting(db, "caddy_caddyfile", caddyfile)
            logger.info("HTTPS aus der Installation eingerichtet: https://%s (%s)", domain, mode)
        except Exception as exc:
            logger.error("HTTPS für %s nicht eingerichtet (%s) - bleibe bei HTTP.", domain, exc)
        return

    upgraded = _upgrade_caddyfile(saved)
    if upgraded != saved:
        try:
            await _adapt_caddyfile(upgraded)
            saved = upgraded
            async with session_factory() as db:
                await _save_setting(db, "caddy_caddyfile", saved)
            logger.info("Gespeicherte Caddy-Konfiguration um die Proxy-Erkennung ergänzt.")
        except Exception as exc:
            logger.warning("Caddy-Konfiguration nicht ergänzt (%s) - nutze sie unverändert.", exc)

    try:
        await _apply_caddyfile(saved)
        logger.info("Gespeicherte Caddy-Konfiguration wurde beim Start angewendet.")
        return
    except Exception as exc:
        logger.error("Caddy-Konfiguration beim Start fehlgeschlagen: %s", exc)

    try:
        await _apply_caddyfile(DEFAULT_CADDYFILE)
        logger.warning("Not-Konfiguration (HTTP auf Port 80) geladen - Oberfläche bleibt erreichbar.")
    except Exception as exc:
        logger.error("Auch die Not-Konfiguration konnte nicht geladen werden: %s", exc)


# ==============================================================================
# Waechter: Caddy neu gestartet? Dann die gespeicherte Config wieder laden.
# ==============================================================================
# Caddy liest beim Start nur die statische Caddyfile (reines HTTP). Wird der
# Container neu gestartet - Image-Update ueber RIMPERIO-Server -> Container, Absturz,
# `docker restart` -, war HTTPS bis zum naechsten Neustart des Backends weg.
# Der Waechter vergleicht die laufende Config mit der gespeicherten und spielt
# sie bei Abweichung wieder ein. Verglichen wird das JSON, das Caddy selbst
# aus der Caddyfile macht (/adapt) - es ist identisch mit dem, was /config/
# nach dem Laden liefert.

STARTUP_WAIT_SECONDS = 120
WATCH_INTERVAL_SECONDS = 30
_adapted_cache: dict = {"source": None, "json": None}


async def wait_for_caddy(max_seconds: float = STARTUP_WAIT_SECONDS) -> bool:
    """Wartet, bis die Admin-API antwortet. True = erreichbar."""
    waited, delay = 0.0, 1.0
    while waited < max_seconds:
        if await _caddy_alive():
            return True
        await asyncio.sleep(delay)
        waited += delay
        delay = min(delay * 2, 10.0)
    return await _caddy_alive()


def _canonical(config) -> str:
    return json.dumps(config or {}, sort_keys=True, separators=(",", ":"))


async def reload_if_drifted(session_factory=None) -> bool:
    """Einmal pruefen; True, wenn neu geladen wurde."""
    if settings.caddy_force_http:
        return False
    session_factory = session_factory or async_session
    async with session_factory() as db:
        saved = await _get_saved_caddyfile(db)
    if not saved:
        return False
    running = await _get_running_config()
    if running is None:
        return False
    source = caddy_admin.enforce_admin(saved)
    if _adapted_cache["source"] != source:
        adapted = await _adapt_caddyfile(saved)
        _adapted_cache.update(source=source, json=_canonical(adapted.get("result", adapted)))
    if _canonical(running) == _adapted_cache["json"]:
        return False
    logger.warning("Laufende Caddy-Konfiguration weicht von der gespeicherten ab "
                   "(Caddy neu gestartet?) - lade die gespeicherte erneut.")
    await _apply_caddyfile(saved)
    return True


async def watch_caddy() -> None:
    """Hintergrundaufgabe: haelt Caddy auf der gespeicherten Konfiguration."""
    failures = 0
    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
        try:
            await reload_if_drifted()
            failures = 0
        except Exception as exc:                                    # defensiv
            failures += 1
            # Nicht im 30-Sekunden-Takt das Protokoll fluten.
            if failures in (1, 10) or failures % 120 == 0:
                logger.warning("Caddy-Wächter: %s", getattr(exc, "detail", exc))
