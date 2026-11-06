# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.11.13.03.42
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Netzwerk API (DNS-Verwaltung fuer den Backend-Container)
#               Standard: per DHCP vergebener System-DNS, optional eigene Server.
# ==============================================================================

import logging
import socket
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_admin
from ..database import get_db, async_session
from ..models import Setting
from ..schemas import (
    DnsConfigResponse,
    DnsConfigUpdate,
    DnsTestRequest,
    DnsTestResponse,
)

router = APIRouter(prefix="/api/network", tags=["Network"])
logger = logging.getLogger("rimperio.network")

DNS_SETTING_KEY = "dns_config"
RESOLV_CONF = Path("/etc/resolv.conf")

# Host-DNS wird ueber das Host-Root (pid: host + privileged) gelesen.
HOST_RESOLV_CANDIDATES = [
    Path("/proc/1/root/run/systemd/resolve/resolv.conf"),  # systemd-resolved: echte Upstreams
    Path("/proc/1/root/etc/resolv.conf"),
    Path("/run/systemd/resolve/resolv.conf"),
]
# Loopback-Stub-Resolver, die dem Container nichts nuetzen.
_STUB_NS = {"127.0.0.53", "127.0.0.11"}


# ------------------------------------------------------------------------------
# Hilfsfunktionen
# ------------------------------------------------------------------------------

def _parse_nameservers(text: str) -> List[str]:
    servers: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) >= 2:
                servers.append(parts[1])
    return servers


def _read_nameservers(path: Path) -> List[str]:
    try:
        return _parse_nameservers(path.read_text())
    except Exception:
        return []


def detect_host_dns() -> List[str]:
    """Per DHCP/System vergebenen DNS des Hosts ermitteln (Loopback-Stubs herausfiltern)."""
    result: List[str] = []
    for path in HOST_RESOLV_CANDIDATES:
        for ns in _read_nameservers(path):
            if ns not in _STUB_NS and ns not in result:
                result.append(ns)
        if result:
            break
    return result


def active_nameservers() -> List[str]:
    return _read_nameservers(RESOLV_CONF)


def _effective_servers(cfg: dict) -> List[str]:
    if cfg.get("mode") == "manual":
        return [s for s in cfg.get("servers", []) if s]
    return detect_host_dns()


def _write_resolv_conf(servers: List[str], search_domains: List[str]) -> None:
    if not servers:
        # Nichts ueberschreiben -> Docker-Default bleibt aktiv (kein Totalausfall).
        return
    lines = ["# Verwaltet von RIMPERIO (Einstellungen -> Netzwerk -> DNS)"]
    if search_domains:
        lines.append("search " + " ".join(search_domains))
    lines.extend(f"nameserver {ns}" for ns in servers)
    RESOLV_CONF.write_text("\n".join(lines) + "\n")


def apply_dns_config(cfg: dict) -> List[str]:
    """Wendet die DNS-Config auf /etc/resolv.conf des Containers an. Gibt die angewandten Server zurueck."""
    servers = _effective_servers(cfg)
    _write_resolv_conf(servers, cfg.get("search_domains", []))
    return servers


async def _load_config(db: AsyncSession):
    result = await db.execute(select(Setting).where(Setting.key == DNS_SETTING_KEY))
    return result.scalar_one_or_none()


def _config_to_dict(setting) -> dict:
    if not setting or not isinstance(setting.value, dict):
        return {"mode": "dhcp", "servers": [], "search_domains": []}
    return setting.value


async def _save_config(db: AsyncSession, cfg: dict) -> None:
    setting = await _load_config(db)
    if setting:
        setting.value = cfg
    else:
        db.add(Setting(key=DNS_SETTING_KEY, value=cfg))
    await db.commit()


# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------

@router.get("/dns", response_model=DnsConfigResponse)
async def get_dns(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    cfg = _config_to_dict(await _load_config(db))
    return DnsConfigResponse(
        mode=cfg.get("mode", "dhcp"),
        detected=detect_host_dns(),
        configured=cfg.get("servers", []),
        active=active_nameservers(),
        search_domains=cfg.get("search_domains", []),
    )


@router.put("/dns", response_model=DnsConfigResponse)
async def set_dns(data: DnsConfigUpdate, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    if data.mode == "manual" and not data.servers:
        raise HTTPException(status_code=400, detail="Im manuellen Modus mindestens einen DNS-Server angeben")

    cfg = {"mode": data.mode, "servers": data.servers, "search_domains": data.search_domains}
    try:
        applied = apply_dns_config(cfg)
    except Exception as exc:
        logger.error("DNS anwenden fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=500, detail=f"DNS konnte nicht angewendet werden: {exc}")

    if data.mode == "dhcp" and not applied:
        raise HTTPException(
            status_code=400,
            detail="Kein per DHCP/System vergebener DNS erkannt. Bitte manuell setzen.",
        )

    await _save_config(db, cfg)
    return DnsConfigResponse(
        mode=cfg["mode"],
        detected=detect_host_dns(),
        configured=cfg["servers"],
        active=active_nameservers(),
        search_domains=cfg["search_domains"],
    )


@router.post("/dns/test", response_model=DnsTestResponse)
async def test_dns(data: DnsTestRequest, _=Depends(get_current_admin)):
    host = data.host.strip()
    try:
        infos = socket.getaddrinfo(host, None)
        addresses = sorted({info[4][0] for info in infos})
        return DnsTestResponse(host=host, resolved=True, addresses=addresses)
    except Exception as exc:
        return DnsTestResponse(host=host, resolved=False, error=str(exc))


async def ensure_dns_config_on_startup(session_factory=None):
    """Wendet eine GESPEICHERTE DNS-Config nach dem Start erneut an.

    Der Container regeneriert /etc/resolv.conf bei jedem Neustart, daher muss die
    Konfiguration erneut geschrieben werden. Ist nichts gespeichert, bleibt der
    Docker-Default unangetastet.
    """
    session_factory = session_factory or async_session
    try:
        async with session_factory() as db:
            setting = await _load_config(db)
    except Exception as exc:
        logger.warning("DNS-Config konnte nicht geladen werden (DB): %s", exc)
        return

    if not setting:
        return  # keine gespeicherte Config -> nichts anwenden

    try:
        apply_dns_config(_config_to_dict(setting))
        logger.info("Gespeicherte DNS-Konfiguration wurde beim Start angewendet.")
    except Exception as exc:
        logger.error("DNS-Konfiguration beim Start fehlgeschlagen: %s", exc)
