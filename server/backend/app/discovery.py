# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.10.08.22.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Netzwerksuche: findet Systeme im Netz und sagt, ob sie
#               schon Logs liefern und wie man sie einbindet.
# ==============================================================================
"""
Die Netzwerksuche.

Die Frage, die sie beantwortet: **Was haengt in meinem Netz, und liefert es
schon Logs?** Das Ergebnis landet in der Tabelle `discovered_devices` und in
der Oberflaeche unter "Systeme im Netz".

Woher die Systeme kommen
------------------------
1. **Die eigenen Subnetze.** Gelesen aus den Schnittstellen des *Hosts*, nicht
   des Containers - der Container haengt in einem Docker-Netz und saehe sonst
   nur sich selbst. Dafuer gibt es `hostexec` (nsenter in PID 1).
2. **Router per SNMP.** Ein Router kennt, was hinter ihm haengt: seine
   ARP-Tabelle nennt IP und MAC jedes Nachbarn, seine Adress- und
   Routingtabelle die Subnetze. So wird subnetzuebergreifend gesucht, obwohl
   RIMPERIO selbst kein Gateway ist. Das Standard-Gateway wird von selbst
   gefragt, weitere Router traegt man ein.
3. **Von Hand eingetragene Bereiche** - fuer alles, was weder lokal ist noch
   von einem Router gemeldet wird.

Wie gesucht wird
----------------
Fuer jede Adresse: ein Ping (ICMP, falls der Container darf) und ein paar
TCP-Verbindungsversuche auf typische Ports. Wer antwortet - auch mit
"Verbindung abgelehnt" -, lebt. Danach liest die Suche die ARP-Tabelle des
Hosts: im eigenen Subnetz tauchen dort auch Geraete auf, die jede Anfrage
verwerfen (typisch: Smartphones). Fuer jedes gefundene System kommen dann die
Details: Rueckwaerts-DNS, SSH-Kennung, SNMP-Systembeschreibung.

Das ist bewusst ein *hoeflicher* Scan: nur Verbindungsaufbau, keine
Schwachstellenpruefung, gedrosselt. Trotzdem kann eine Firewall mit
Angriffserkennung anschlagen - deshalb laesst sich die automatische Suche in
den Einstellungen abschalten.

Was danach passiert
-------------------
Jedes System wird mit der Tabelle `agents` abgeglichen (MAC, IP, Hostname).
Liefert es schon Logs, ist nichts zu tun. Sonst gibt es eine Empfehlung:

* Linux, Windows  -> HTTPS-Agent (verschluesselt, mit eigenem Schluessel)
* Netzwerkgeraete, Drucker, NAS, Appliances -> Syslog, weil dort kein Agent
  laeuft
* Smartphones     -> nicht einbindbar

Zugangsdaten werden hier nirgends gebraucht oder gespeichert. Das spaetere
automatische Einbinden (SSH) bekommt sie nur fuer den einen Vorgang.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import re
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import hostexec, journal, snmp
from .models import Agent, DiscoveredDevice, Setting

logger = logging.getLogger("rimperio.discovery")

CONFIG_KEY = "discovery_config"
STATE_KEY = "discovery_state"

DEFAULT_CONFIG: dict = {
    # Automatisch suchen: einmal kurz nach dem ersten Start, danach im Takt.
    "auto_scan": True,
    "interval_hours": 24,
    # Eigene Subnetze des Servers absuchen.
    "scan_local": True,
    # Standard-Gateway per SNMP nach ARP-/Routingtabelle fragen.
    "query_gateway": True,
    # Subnetze, die ein Router meldet, gleich mit absuchen.
    "scan_router_subnets": True,
    # Weitere Router: [{"host": "10.0.0.1", "community": "public"}]
    "routers": [],
    # Community fuer das Gateway und fuer die Abfrage einzelner Geraete.
    "snmp_community": "public",
    # Gefundene Geraete per SNMP nach Name und Beschreibung fragen.
    "snmp_probe_hosts": True,
    # Zusaetzliche Bereiche: "10.20.0.0/24", "10.20.0.5", "10.20.0.10-10.20.0.50"
    "extra_ranges": [],
    # Obergrenze, damit niemand aus Versehen ein /8 absucht.
    "max_hosts": 4096,
}

# Groesstes Netz, das ohne ausdrueckliche Angabe abgesucht wird. Ein Router,
# der ein /16 meldet, soll nicht 65.000 Adressen abklappern lassen.
AUTO_MAX_PREFIX = 22

# Wie lange ein Agent ohne Lebenszeichen noch als "liefert" gilt.
DELIVERING_WINDOW = timedelta(hours=24)

# Erster automatischer Lauf: kurz nach dem Start, wenn alles hochgefahren ist.
STARTUP_DELAY = int(os.getenv("DISCOVERY_STARTUP_DELAY", "120"))
WATCH_INTERVAL = 600

# Ports, an denen sich ein System verraet.
PORTS: Dict[int, str] = {
    21: "FTP", 22: "SSH", 23: "Telnet", 53: "DNS", 80: "HTTP", 135: "RPC",
    139: "NetBIOS", 443: "HTTPS", 445: "SMB", 515: "LPD", 548: "AFP",
    631: "IPP", 3389: "RDP", 5000: "DSM", 5001: "DSM (TLS)", 5555: "ADB",
    5985: "WinRM", 5986: "WinRM (TLS)", 8006: "Proxmox", 8080: "HTTP-Alt",
    8291: "Winbox", 8443: "HTTPS-Alt", 8728: "RouterOS-API", 9090: "Cockpit",
    9100: "JetDirect", 49000: "TR-064", 62078: "iOS-Sync",
}
# Erste Runde: wenige Ports, um herauszufinden, ob ueberhaupt jemand da ist.
QUICK_PORTS = (22, 80, 443, 445, 135, 3389, 8080, 9100, 62078, 49000)

CONNECT_TIMEOUT = 0.8
# Wie viele Adressen gleichzeitig abgetastet werden. Je Adresse sind bis zu
# ~20 Sockets offen - die Zahl wird deshalb am Dateilimit des Prozesses
# gedeckelt (siehe _parallelism).
MAX_PARALLEL = 96

KIND_LABELS: Dict[str, str] = {
    "linux": "Linux",
    "windows": "Windows",
    "network": "Netzwerkgerät",
    "printer": "Drucker",
    "nas": "NAS",
    "appliance": "Appliance",
    "iot": "IoT / Smart Home",
    "mobile": "Smartphone / Tablet",
    "rimperio": "RIMPERIO-Server",
    "unknown": "Unbekannt",
}

# Empfehlung je Geraeteart - die Regel: HTTPS-Agent ueberall dort, wo einer
# laeuft; Syslog nur, wo nichts anderes geht.
METHOD_BY_KIND: Dict[str, str] = {
    "linux": "agent_linux",
    "windows": "agent_windows",
    "network": "syslog",
    "printer": "syslog",
    "nas": "syslog",
    "appliance": "syslog",
    "iot": "syslog",
    "mobile": "unsupported",
    "rimperio": "self",
    "unknown": "check",
}

_IGNORED_IFACES = ("lo", "docker", "br-", "veth", "virbr", "lxcbr", "lxdbr",
                   "cni", "flannel", "cali", "kube", "podman")


# =============================================================================
# Einstellungen
# =============================================================================
async def _get_setting(session: AsyncSession, key: str) -> Optional[Setting]:
    return (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()


async def _put_setting(session: AsyncSession, key: str, value: dict, description: str) -> None:
    row = await _get_setting(session, key)
    if row:
        row.value = dict(value)             # neues Objekt, sonst merkt SQLAlchemy nichts
    else:
        session.add(Setting(key=key, value=dict(value), description=description))
    await session.commit()


async def load_config(session: AsyncSession) -> dict:
    row = await _get_setting(session, CONFIG_KEY)
    cfg = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        cfg.update({k: v for k, v in row.value.items() if k in DEFAULT_CONFIG})
    return cfg


async def save_config(session: AsyncSession, cfg: dict) -> dict:
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
    await _put_setting(session, CONFIG_KEY, merged, "Netzwerksuche")
    return merged


async def load_state(session: AsyncSession) -> dict:
    row = await _get_setting(session, STATE_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


# =============================================================================
# Bereiche lesen
# =============================================================================
def parse_range(text: str) -> List[ipaddress.IPv4Address]:
    """"10.0.0.0/24", "10.0.0.5" oder "10.0.0.10-10.0.0.50" / "10.0.0.10-50".

    Wirft ValueError mit einer Meldung, die man so anzeigen kann.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Leerer Bereich")
    if "-" in text:
        start_s, end_s = (part.strip() for part in text.split("-", 1))
        start = ipaddress.IPv4Address(start_s)
        if "." not in end_s:
            end_s = start_s.rsplit(".", 1)[0] + "." + end_s
        end = ipaddress.IPv4Address(end_s)
        if end < start:
            raise ValueError(f"{text}: Ende liegt vor dem Anfang")
        return [ipaddress.IPv4Address(n) for n in range(int(start), int(end) + 1)]
    if "/" in text:
        net = ipaddress.IPv4Network(text, strict=False)
        if net.prefixlen >= 31:
            return list(net)
        return list(net.hosts())
    return [ipaddress.IPv4Address(text)]


def range_size(text: str) -> int:
    """Wie viele Adressen ein Bereich hat - ohne ihn auszurollen."""
    text = (text or "").strip()
    if "-" in text:
        return len(parse_range(text))
    if "/" in text:
        net = ipaddress.IPv4Network(text, strict=False)
        return max(1, net.num_addresses - (2 if net.prefixlen < 31 else 0))
    ipaddress.IPv4Address(text)
    return 1


# =============================================================================
# Netze des Hosts
# =============================================================================
@dataclass
class HostNetworks:
    """Was der Server selbst ueber sein Netz weiss."""
    host_access: bool = False
    interfaces: List[dict] = field(default_factory=list)   # {"iface","address","network"}
    gateways: List[str] = field(default_factory=list)
    routes: List[str] = field(default_factory=list)        # weitere, geroutete Netze

    @property
    def addresses(self) -> Set[str]:
        return {i["address"] for i in self.interfaces}

    @property
    def networks(self) -> List[ipaddress.IPv4Network]:
        return [ipaddress.IPv4Network(i["network"]) for i in self.interfaces]

    def local_address_for(self, ip: str) -> Optional[str]:
        """Die eigene Adresse im selben Subnetz wie `ip` - oder None."""
        addr = ipaddress.IPv4Address(ip)
        for item in self.interfaces:
            if addr in ipaddress.IPv4Network(item["network"]):
                return item["address"]
        return None


def _iface_ignored(name: str) -> bool:
    return any(name == p or name.startswith(p) for p in _IGNORED_IFACES)


_nets_cache: Tuple[float, Optional[HostNetworks]] = (0.0, None)


async def host_networks(max_age: float = 30.0) -> HostNetworks:
    """Schnittstellen, Gateways und Routen des Hosts. Kurz zwischengespeichert,
    weil die Oberflaeche waehrend einer Suche im Sekundentakt nachfragt."""
    global _nets_cache
    stamp, cached = _nets_cache
    if cached is not None and time.monotonic() - stamp < max_age:
        return cached
    result = await _read_host_networks()
    _nets_cache = (time.monotonic(), result)
    return result


async def _read_host_networks() -> HostNetworks:
    import json

    result = HostNetworks(host_access=hostexec.nsenter_available())
    if not result.host_access:
        return result

    addr = await hostexec.run_host(["ip", "-j", "-4", "addr", "show"], timeout=10)
    if addr.ok:
        try:
            for link in json.loads(addr.stdout or "[]"):
                name = link.get("ifname", "")
                if _iface_ignored(name):
                    continue
                for info in link.get("addr_info", []):
                    if info.get("family") != "inet" or info.get("scope") != "global":
                        continue
                    prefix = int(info.get("prefixlen", 32))
                    if prefix >= 31:
                        continue                    # Punkt-zu-Punkt, kein Netz zum Absuchen
                    net = ipaddress.IPv4Network(f"{info['local']}/{prefix}", strict=False)
                    result.interfaces.append({"iface": name, "address": info["local"],
                                              "network": str(net)})
        except (ValueError, KeyError) as exc:
            logger.warning("Schnittstellen des Hosts nicht lesbar: %s", exc)

    route = await hostexec.run_host(["ip", "-j", "-4", "route", "show"], timeout=10)
    if route.ok:
        try:
            local = {i["network"] for i in result.interfaces}
            for entry in json.loads(route.stdout or "[]"):
                dev = entry.get("dev", "")
                if _iface_ignored(dev):
                    continue
                dst = entry.get("dst", "")
                gateway = entry.get("gateway")
                if dst == "default":
                    if gateway and gateway not in result.gateways:
                        result.gateways.append(gateway)
                    continue
                if not gateway:
                    continue
                net = str(ipaddress.IPv4Network(dst if "/" in dst else f"{dst}/32", strict=False))
                if net not in local and net not in result.routes and not net.endswith("/32"):
                    result.routes.append(net)
        except (ValueError, KeyError) as exc:
            logger.warning("Routingtabelle des Hosts nicht lesbar: %s", exc)

    return result


def _read_arp_table() -> Dict[str, str]:
    """ARP-Tabelle des Hosts: IP -> MAC. Nur vollstaendige Eintraege."""
    table: Dict[str, str] = {}
    for path in ("/proc/1/net/arp", "/proc/net/arp"):
        try:
            with open(path, "r", encoding="ascii", errors="replace") as handle:
                lines = handle.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 6:
                continue
            ip, flags, mac, dev = parts[0], parts[2], parts[3].lower(), parts[5]
            if _iface_ignored(dev) or mac == "00:00:00:00:00:00":
                continue
            try:
                if not int(flags, 16) & 0x2:           # ATF_COM: Eintrag ist aufgeloest
                    continue
            except ValueError:
                continue
            table[ip] = mac
        if table or path == "/proc/1/net/arp" and hostexec.nsenter_available():
            break
    return table


# =============================================================================
# Abtasten
# =============================================================================
class IcmpPinger:
    """Ping ohne externes Programm - ein Socket fuer alle Ziele.

    Braucht CAP_NET_RAW (hat der Backend-Container im Standardbetrieb). Ohne
    das Recht versucht er den unprivilegierten Ping-Socket, sonst gibt es eben
    keinen Ping und die Suche verlaesst sich auf TCP und ARP.
    """

    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        self.raw = False
        self.waiting: Dict[str, asyncio.Future] = {}
        self.ident = os.getpid() & 0xFFFF
        self.seq = 0
        for kind, raw in ((socket.SOCK_RAW, True), (socket.SOCK_DGRAM, False)):
            try:
                self.sock = socket.socket(socket.AF_INET, kind, socket.IPPROTO_ICMP)
                self.raw = raw
                break
            except (PermissionError, OSError):
                continue
        if self.sock:
            self.sock.setblocking(False)
            asyncio.get_running_loop().add_reader(self.sock.fileno(), self._readable)

    @property
    def available(self) -> bool:
        return self.sock is not None

    def close(self) -> None:
        if self.sock:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self.sock.fileno())
            self.sock.close()
            self.sock = None

    @staticmethod
    def _checksum(data: bytes) -> int:
        if len(data) % 2:
            data += b"\x00"
        total = sum(struct.unpack(f"!{len(data) // 2}H", data))
        total = (total >> 16) + (total & 0xFFFF)
        total += total >> 16
        return ~total & 0xFFFF

    def _readable(self) -> None:
        while self.sock:
            try:
                data, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if self.raw:
                data = data[(data[0] & 0x0F) * 4:]   # IP-Kopf ueberspringen
            if len(data) < 8 or data[0] != 0:       # 0 = Echo Reply
                continue
            if self.raw and struct.unpack("!H", data[4:6])[0] != self.ident:
                continue                            # Antwort an jemand anderen
            future = self.waiting.pop(addr[0], None)
            if future and not future.done():
                future.set_result(True)

    async def ping(self, ip: str, timeout: float = 0.9, attempts: int = 2) -> bool:
        if not self.sock:
            return False
        for _ in range(attempts):
            self.seq = (self.seq + 1) & 0xFFFF
            header = struct.pack("!BBHHH", 8, 0, 0, self.ident, self.seq)
            payload = b"rimperio-discovery"
            packet = struct.pack("!BBHHH", 8, 0, self._checksum(header + payload),
                                 self.ident, self.seq) + payload
            future = asyncio.get_running_loop().create_future()
            self.waiting[ip] = future
            try:
                self.sock.sendto(packet, (ip, 0))
                await asyncio.wait_for(future, timeout)
                return True
            except (asyncio.TimeoutError, OSError):
                self.waiting.pop(ip, None)
        return False


async def tcp_probe(ip: str, port: int, timeout: float = CONNECT_TIMEOUT) -> Optional[str]:
    """"open", "closed" (Host lebt, Port zu) oder None (keine Antwort)."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except ConnectionRefusedError:
        return "closed"
    except (asyncio.TimeoutError, OSError):
        return None
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), 0.5)
    return "open"


async def ssh_banner(ip: str, timeout: float = 2.5) -> str:
    """Die erste Zeile des SSH-Servers, z.B. "SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3"."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, 22), timeout)
    except (asyncio.TimeoutError, OSError):
        return ""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout)
        return line.decode("utf-8", "replace").strip()[:200]
    except (asyncio.TimeoutError, OSError):
        return ""
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 0.5)


_dns_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="discovery-dns")


async def reverse_dns(ip: str, timeout: float = 2.0) -> str:
    loop = asyncio.get_running_loop()
    try:
        name, _, _ = await asyncio.wait_for(
            loop.run_in_executor(_dns_pool, socket.gethostbyaddr, ip), timeout)
    except (asyncio.TimeoutError, OSError):
        return ""
    return name.rstrip(".")[:255]


# =============================================================================
# Erkennen
# =============================================================================
_PRINTER = ("laserjet", "officejet", "deskjet", "pagewide", "jetdirect", "printer",
            "drucker", "brother", "epson", "kyocera", "lexmark", "xerox", "ricoh",
            "konica", "bizhub", "imagerunner")
_NETWORK = ("cisco ios", "cisco nx-os", "junos", "routeros", "mikrotik", "procurve",
            "aruba", "unifi", "edgeos", "edgeswitch", "fortigate", "fortios", "pfsense",
            "opnsense", "ubiquiti", "netgear", "tp-link", "zyxel", "lancom", "sophos",
            "draytek", "fritz!box", "fritz.box", "fritzbox", "openwrt", "dd-wrt",
            "juniper", "officeconnect", "omada", "sonicwall", "watchguard", "meraki",
            "speedport", "easybox", "fritz!repeater", "fritz!powerline")
_NAS = ("synology", "diskstation", "qnap", "truenas", "freenas", "unraid", "asustor",
        "terramaster", "readynas")
_IOT = ("shelly", "tasmota", "sonoff", "espressif", "esp32", "esp8266", "esp_", "esp-",
        "sonos", "chromecast", "homematic", "ccu3", "hue-bridge", "philips-hue", "tradfri")
_MOBILE = ("android", "iphone", "ipad", "galaxy", "pixel", "xiaomi", "redmi", "oneplus",
           "oppo", "realme", "huawei-p", "moto-g", "motorola")
_APPLIANCE = ("vmware esxi", "dropbear")


def _contains(text: str, needles: Iterable[str]) -> Optional[str]:
    for needle in needles:
        if needle in text:
            return needle
    return None


def _os_from_banner(banner: str) -> str:
    low = banner.lower()
    if not low:
        return ""
    if "windows" in low:
        return "Windows (OpenSSH)"
    match = re.search(r"deb(\d+)u", low)
    if "debian" in low:
        return f"Debian {match.group(1)}" if match else "Debian"
    if "ubuntu" in low:
        return "Ubuntu"
    if "raspbian" in low:
        return "Raspberry Pi OS"
    if "freebsd" in low:
        return "FreeBSD"
    if "dropbear" in low:
        return "Embedded Linux (Dropbear)"
    if "openssh" in low:
        return "Linux/Unix (OpenSSH)"
    return ""


def is_random_mac(mac: Optional[str]) -> bool:
    """Lokal verwaltete MAC - so tarnen sich aktuelle Smartphones in jedem WLAN."""
    if not mac:
        return False
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def classify(ports: Iterable[int], *, ssh: str = "", sys_descr: str = "", sys_name: str = "",
             hostname: str = "", mac: Optional[str] = None, is_gateway: bool = False,
             is_self: bool = False) -> Tuple[str, str]:
    """Rät die Geräteart. Gibt (kind, Beschreibung) zurück.

    Reihenfolge: was das Gerät *über sich selbst sagt* (SNMP, SSH-Kennung,
    Name) schlägt, was man an offenen Ports *vermutet*.
    """
    ports = set(ports)
    if is_self:
        return "rimperio", "Dieser Server"

    descr = sys_descr.lower()
    # Nur der Kurzname zaehlt: hinter einer FRITZ!Box heisst *jedes* Geraet
    # "irgendwas.fritz.box" - die Domaene sagt nichts ueber das Geraet.
    host = hostname.lower().rstrip(".")
    if host in ("fritz.box", "fritz.nas", "fritz.repeater", "speedport.ip"):
        return "network", "FRITZ!Box" if host.startswith("fritz") else "Speedport"
    names = f"{sys_name} {host.split('.')[0]}".lower().strip()
    banner_os = _os_from_banner(ssh)
    said = f"{descr} {names}"

    # 1. Selbstauskunft
    if _contains(said, _PRINTER) or names.startswith("brn") or " brn" in names:
        return "printer", sys_descr[:80] or "Drucker"
    hit = _contains(said, _NETWORK)
    if hit:
        return "network", sys_descr[:80] or hit
    hit = _contains(said, _NAS)
    if hit:
        return "nas", sys_descr[:80] or hit
    hit = _contains(said, _IOT)
    if hit:
        return "iot", hit
    hit = _contains(names, _MOBILE)
    if hit:
        return "mobile", hit
    if _contains(f"{descr} {ssh.lower()}", _APPLIANCE):
        return "appliance", banner_os or sys_descr[:80]
    if "windows" in descr or banner_os.startswith("Windows"):
        return "windows", banner_os or sys_descr[:80] or "Windows"

    # 2. Ports
    if 62078 in ports:
        return "mobile", "iPhone / iPad"
    if 5555 in ports and 22 not in ports:
        return "mobile", "Android (ADB)"
    if ports & {9100, 631, 515}:
        return "printer", "Drucker"
    if ports & {8291, 8728}:
        return "network", "MikroTik"
    if 49000 in ports:
        return "network", "FRITZ!Box"
    if ports & {135, 3389, 5985, 5986}:
        return "windows", "Windows"
    if 8006 in ports:
        return "linux", "Proxmox VE"
    if 22 in ports:
        return "linux", banner_os or "Linux/Unix"
    if ports & {139, 445}:
        return "windows", "Windows (SMB)"
    if ports & {5000, 5001}:
        return "nas", "Synology DSM"
    if is_gateway:
        return "network", "Router (Standard-Gateway)"
    if descr.startswith("linux"):
        return "appliance", "Linux-basiertes Gerät"
    if not ports and is_random_mac(mac):
        return "mobile", "wahrscheinlich Smartphone (zufällige MAC)"
    if ports & {80, 443, 8080, 8443}:
        return "unknown", "Gerät mit Weboberfläche"
    return "unknown", ""


# =============================================================================
# Ablauf
# =============================================================================
@dataclass
class ScanState:
    """Was gerade passiert - fuer die Fortschrittsanzeige."""
    running: bool = False
    trigger: str = ""
    actor: str = ""
    phase: str = ""
    done: int = 0
    total: int = 0
    found: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "running": self.running, "trigger": self.trigger, "actor": self.actor,
            "phase": self.phase, "done": self.done, "total": self.total,
            "found": self.found, "started_at": self.started_at,
            "finished_at": self.finished_at, "notes": list(self.notes),
            "error": self.error,
        }


STATE = ScanState()
_lock = asyncio.Lock()


@dataclass
class Found:
    ip: str
    mac: Optional[str] = None
    ports: Set[int] = field(default_factory=set)
    alive_by: Set[str] = field(default_factory=set)   # icmp, tcp, arp, router
    network: str = ""
    hostname: str = ""
    ssh: str = ""
    snmp: Optional[dict] = None


def is_running() -> bool:
    return STATE.running


async def start_scan(trigger: str, actor: str) -> bool:
    """Startet eine Suche im Hintergrund. False, wenn schon eine laeuft."""
    if STATE.running or _lock.locked():
        return False
    STATE.running = True            # sofort, damit die Oberflaeche es sieht
    task = asyncio.create_task(run_scan(trigger, actor))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


_tasks: set = set()


async def run_scan(trigger: str = "manual", actor: str = "system") -> dict:
    from .database import async_session

    async with _lock:
        started = time.monotonic()
        now = datetime.utcnow()
        STATE.__init__()
        STATE.running = True
        STATE.trigger, STATE.actor = trigger, actor
        STATE.started_at = now.isoformat()
        try:
            async with async_session() as session:
                cfg = await load_config(session)
            found, nets, summary = await _discover(cfg)
            STATE.phase = "Speichern"
            async with async_session() as session:
                new_count = await _store(session, found, nets, now)
                summary["new"] = new_count
                summary["duration_s"] = round(time.monotonic() - started, 1)
                summary["finished_at"] = datetime.utcnow().isoformat()
                summary["trigger"] = trigger
                await _put_setting(session, STATE_KEY, summary, "Letzte Netzwerksuche")
                await journal.record(
                    session, category="discovery", event="discovery.scan",
                    message=(f"Netzwerksuche: {summary['alive']} Systeme in "
                             f"{summary['addresses']} Adressen gefunden, davon {new_count} neu."),
                    actor=actor, ok=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    detail={k: v for k, v in summary.items() if k != "finished_at"})
            return summary
        except Exception as exc:
            logger.exception("Netzwerksuche fehlgeschlagen")
            STATE.error = str(exc)
            await journal.write(category="discovery", event="discovery.scan", level="error",
                                message=f"Netzwerksuche fehlgeschlagen: {exc}", actor=actor,
                                ok=False)
            return {"error": str(exc)}
        finally:
            STATE.running = False
            STATE.phase = ""
            STATE.finished_at = datetime.utcnow().isoformat()


async def _discover(cfg: dict) -> Tuple[Dict[str, Found], HostNetworks, dict]:
    STATE.phase = "Netze ermitteln"
    nets = await host_networks(max_age=0)
    if not nets.host_access:
        STATE.notes.append("Kein Zugriff auf das Netz des Hosts (gehärteter Betrieb?) — "
                           "eigene Subnetze und ARP-Tabelle sind nicht sichtbar. "
                           "Es werden nur eingetragene Bereiche und Router abgesucht.")

    # --- Bereiche sammeln -----------------------------------------------------
    ranges: List[Tuple[str, str]] = []          # (Bereich, Herkunft)
    if cfg.get("scan_local"):
        for item in nets.interfaces:
            net = ipaddress.IPv4Network(item["network"])
            if not net.is_private:
                # Ein gemieteter Server mit oeffentlicher IP haengt in einem
                # Netz voller fremder Kunden - das wird nicht von selbst abgesucht.
                STATE.notes.append(f"{net} ist ein öffentliches Netz und wird nicht automatisch "
                                   f"abgesucht. Wer das wirklich will, trägt es als Bereich ein.")
                continue
            if net.prefixlen < AUTO_MAX_PREFIX:
                narrowed = ipaddress.IPv4Network(f"{item['address']}/{AUTO_MAX_PREFIX}", strict=False)
                STATE.notes.append(f"{net} ist groß — abgesucht wird nur {narrowed} rund um den "
                                   f"Server. Mehr über „Weitere Bereiche“.")
                net = narrowed
            ranges.append((str(net), f"lokal ({item['iface']})"))

    for text in cfg.get("extra_ranges") or []:
        ranges.append((text, "eingetragen"))

    # --- Router fragen ----------------------------------------------------------
    found: Dict[str, Found] = {}
    routers: List[Tuple[str, str]] = []
    if cfg.get("query_gateway"):
        # Nur ein Gateway im eigenen, privaten Netz - nicht den Router des Providers.
        routers += [(gw, cfg.get("snmp_community") or "public") for gw in nets.gateways
                    if ipaddress.IPv4Address(gw).is_private]
    for entry in cfg.get("routers") or []:
        host = (entry or {}).get("host", "").strip()
        if host and host not in [r[0] for r in routers]:
            routers.append((host, (entry.get("community") or cfg.get("snmp_community") or "public")))

    router_report: List[dict] = []
    for host, community in routers:
        STATE.phase = f"Router {host} fragen"
        try:
            tables = await snmp.router_tables(host, community)
        except (snmp.SnmpError, OSError) as exc:
            router_report.append({"host": host, "ok": False, "error": str(exc)})
            continue
        router_report.append({"host": host, "ok": True, "name": tables["system"]["sys_name"],
                              "neighbors": len(tables["neighbors"]),
                              "subnets": tables["subnets"], "routes": tables["routes"]})
        for neighbor in tables["neighbors"]:
            # Die ARP-Tabelle eines Routers kennt auch die WAN-Seite - die
            # Nachbarn beim Provider gehen uns nichts an.
            if not ipaddress.IPv4Address(neighbor["ip"]).is_private:
                continue
            item = found.setdefault(neighbor["ip"], Found(ip=neighbor["ip"]))
            item.mac = item.mac or neighbor["mac"]
            item.alive_by.add("router")
            item.network = item.network or f"Router {host}"
        if cfg.get("scan_router_subnets"):
            for net in tables["subnets"] + tables["routes"]:
                if not ipaddress.IPv4Network(net).is_private:
                    continue                        # WAN-Netz des Routers
                if ipaddress.IPv4Network(net).prefixlen < AUTO_MAX_PREFIX:
                    STATE.notes.append(f"Router {host} meldet {net} — zu groß für die "
                                       f"automatische Suche, bei Bedarf als Bereich eintragen.")
                    continue
                ranges.append((net, f"Router {host}"))

    # --- Zieladressen ausrollen ------------------------------------------------
    targets: Dict[str, str] = {}
    budget = int(cfg.get("max_hosts") or DEFAULT_CONFIG["max_hosts"])
    for text, origin in ranges:
        try:
            size = range_size(text)
        except ValueError as exc:
            STATE.notes.append(f"Bereich „{text}“ ignoriert: {exc}")
            continue
        if size > budget:
            STATE.notes.append(f"Bereich {text} übersprungen — größer als die Obergrenze von "
                               f"{budget} Adressen (Einstellung „Höchstzahl Adressen“).")
            continue
        # Meldet der Router das eigene Subnetz noch einmal, zaehlt nur, was neu ist.
        fresh = [str(a) for a in parse_range(text) if str(a) not in targets]
        if len(targets) + len(fresh) > budget:
            STATE.notes.append(f"Bereich {text} übersprungen — Obergrenze von {budget} "
                               f"Adressen erreicht (Einstellung „Höchstzahl Adressen“).")
            continue
        label = text if origin == "eingetragen" else f"{text} · {origin}"
        for addr in fresh:
            targets[addr] = label
    for ip in found:                                # vom Router gemeldete Nachbarn
        targets.setdefault(ip, found[ip].network)

    # --- Abtasten ---------------------------------------------------------------
    STATE.phase = "Systeme suchen"
    STATE.total = len(targets)
    pinger = IcmpPinger()
    icmp_ok = pinger.available
    if not icmp_ok:
        STATE.notes.append("Ping steht nicht zur Verfügung (fehlendes Recht) — gesucht wird "
                           "nur über TCP und die ARP-Tabelle.")
    gate = asyncio.Semaphore(_parallelism())
    alive_count = 0

    async def probe(ip: str) -> None:
        nonlocal alive_count
        async with gate:
            item = found.get(ip) or Found(ip=ip)
            item.network = item.network or targets[ip]
            checks = [pinger.ping(ip)] + [tcp_probe(ip, port) for port in QUICK_PORTS]
            results = await asyncio.gather(*checks)
            if results[0]:
                item.alive_by.add("icmp")
            for port, answer in zip(QUICK_PORTS, results[1:]):
                if answer:
                    item.alive_by.add("tcp")
                if answer == "open":
                    item.ports.add(port)
            if item.alive_by:
                found[ip] = item
                alive_count += 1
            STATE.done += 1
            STATE.found = alive_count

    try:
        await asyncio.gather(*(probe(ip) for ip in targets))
    finally:
        pinger.close()

    # Die ARP-Tabelle des Hosts kennt jetzt jeden, der im eigenen Subnetz
    # geantwortet hat - auch auf Anfragen, die er ansonsten verworfen hat.
    if nets.host_access:
        arp = _read_arp_table()
        for ip, mac in arp.items():
            if ip not in targets:
                continue
            item = found.setdefault(ip, Found(ip=ip, network=targets[ip]))
            item.mac = item.mac or mac
            item.alive_by.add("arp")

    alive = {ip: f for ip, f in found.items() if f.alive_by}

    # --- Details ------------------------------------------------------------------
    STATE.phase = "Details lesen"
    STATE.done, STATE.total = 0, len(alive)
    rest_ports = [p for p in PORTS if p not in QUICK_PORTS]
    community = cfg.get("snmp_community") or "public"

    async def details(item: Found) -> None:
        async with gate:
            answers = await asyncio.gather(*(tcp_probe(item.ip, p) for p in rest_ports))
            item.ports.update(p for p, a in zip(rest_ports, answers) if a == "open")
            jobs = [reverse_dns(item.ip)]
            jobs.append(ssh_banner(item.ip) if 22 in item.ports else _empty())
            if cfg.get("snmp_probe_hosts"):
                jobs.append(snmp.system_info(item.ip, community))
            results = await asyncio.gather(*jobs)
            item.hostname = results[0]
            item.ssh = results[1]
            if len(results) > 2:
                item.snmp = results[2]
            STATE.done += 1

    await asyncio.gather(*(details(item) for item in alive.values()))

    summary = {
        "addresses": len(targets),
        "alive": len(alive),
        "ranges": [r[0] for r in ranges],
        "routers": router_report,
        "notes": list(STATE.notes),
        "icmp": icmp_ok,
        "host_access": nets.host_access,
    }
    return alive, nets, summary


async def _empty() -> str:
    return ""


def _parallelism() -> int:
    """Gleichzeitige Adressen, passend zum Dateilimit.

    Docker gibt Containern meist ein grosszuegiges Limit, manche Systeme aber
    nur 1024. Das weiche Limit wird so weit angehoben, wie das harte erlaubt;
    reicht es trotzdem nicht, wird langsamer gesucht statt mit "Too many open
    files" abzubrechen.
    """
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if hard == resource.RLIM_INFINITY or hard > soft:
            target = 65536 if hard == resource.RLIM_INFINITY else min(hard, 65536)
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                soft = target
    except ImportError:
        return 16
    return max(8, min(MAX_PARALLEL, (soft - 200) // 24))


async def _store(session: AsyncSession, found: Dict[str, Found], nets: HostNetworks,
                 now: datetime) -> int:
    """Ergebnis in `discovered_devices` schreiben. Gibt die Zahl neuer Systeme zurueck.

    Wiedererkannt wird ein System zuerst an der MAC - ein Rechner, der per DHCP
    eine neue IP bekommen hat, ist derselbe Rechner. Ohne MAC (andere Subnetze,
    wenn kein Router fragbar war) an der IP.
    """
    rows = (await session.execute(select(DiscoveredDevice))).scalars().all()
    by_ip = {r.ip_address: r for r in rows}
    by_mac = {r.mac_address: r for r in rows if r.mac_address}
    own = nets.addresses
    gateways = set(nets.gateways)
    new_count = 0

    for ip, item in found.items():
        row = by_mac.get(item.mac) if item.mac else None
        if row is None:
            row = by_ip.get(ip)
            # Gleiche IP, aber nachweislich ein anderes Geraet: alter Eintrag
            # ist ueberholt, das neue Geraet bekommt eine eigene Zeile.
            if row is not None and item.mac and row.mac_address and row.mac_address != item.mac:
                await session.delete(row)
                await session.flush()
                by_ip.pop(ip, None)
                row = None
        elif row.ip_address != ip:
            other = by_ip.get(ip)
            if other is not None and other is not row:
                await session.delete(other)
                await session.flush()
            by_ip.pop(row.ip_address, None)

        if row is None:
            row = DiscoveredDevice(ip_address=ip, first_seen=now, details={})
            session.add(row)
            new_count += 1

        snmp_info = item.snmp or {}
        kind, os_guess = classify(
            item.ports, ssh=item.ssh, sys_descr=snmp_info.get("sys_descr", ""),
            sys_name=snmp_info.get("sys_name", ""), hostname=item.hostname,
            mac=item.mac, is_gateway=ip in gateways, is_self=ip in own)

        row.ip_address = ip
        row.mac_address = item.mac or row.mac_address
        row.hostname = item.hostname or snmp_info.get("sys_name") or row.hostname
        row.kind = kind
        row.os_guess = (os_guess or "")[:120]
        row.open_ports = sorted(item.ports)
        row.network = (item.network or "")[:100]
        row.same_subnet = nets.local_address_for(ip) is not None if nets.interfaces else None
        row.last_seen = now
        details = dict(row.details or {})
        details.update({
            "ssh_banner": item.ssh,
            "snmp": snmp_info or None,
            "alive_by": sorted(item.alive_by),
            "random_mac": is_random_mac(item.mac),
        })
        row.details = details
        by_ip[ip] = row
        if row.mac_address:
            by_mac[row.mac_address] = row

    await session.commit()
    return new_count


# =============================================================================
# Lesen fuer die Oberflaeche
# =============================================================================
def _norm_mac(mac: Optional[str]) -> str:
    return (mac or "").lower().replace("-", ":")


def _short(name: Optional[str]) -> str:
    return (name or "").lower().split(".")[0]


async def list_devices(session: AsyncSession, nets: Optional[HostNetworks] = None) -> List[dict]:
    """Alle gefundenen Systeme mit Status - der Abgleich mit `agents` passiert hier.

    Bewusst beim Lesen und nicht beim Suchen: ob ein Geraet liefert, aendert
    sich mit jeder Logzeile, nicht mit jedem Suchlauf.
    """
    devices = (await session.execute(
        select(DiscoveredDevice).order_by(DiscoveredDevice.ip_address))).scalars().all()
    agents = (await session.execute(select(Agent))).scalars().all()

    # Gibt es mehrere Eintraege fuer dieselbe MAC oder IP (Geraet neu
    # angelegt, alte Karte nie geloescht), zaehlt der zuletzt aktive.
    agents = sorted(agents, key=lambda a: a.last_seen or datetime.min)
    by_mac = {_norm_mac(a.mac_address): a for a in agents if a.mac_address}
    by_ip = {a.ip_address: a for a in agents if a.ip_address}
    by_name: Dict[str, List[Agent]] = {}
    for agent in agents:
        if agent.hostname:
            by_name.setdefault(_short(agent.hostname), []).append(agent)

    last_scan = max((d.last_seen for d in devices if d.last_seen), default=None)
    now = datetime.utcnow()
    result = []
    for device in devices:
        agent = by_mac.get(_norm_mac(device.mac_address)) if device.mac_address else None
        matched_by = "mac" if agent else ""
        if agent is None and device.ip_address in by_ip:
            agent, matched_by = by_ip[device.ip_address], "ip"
        if agent is None and device.hostname:
            candidates = by_name.get(_short(device.hostname), [])
            if len(candidates) == 1:
                agent, matched_by = candidates[0], "hostname"

        delivering = bool(agent and agent.last_seen and now - agent.last_seen <= DELIVERING_WINDOW)
        kind = device.kind or "unknown"
        if kind == "rimperio":
            status = "self"
        elif delivering:
            status = "delivering"
        elif agent:
            status = "stale"
        elif METHOD_BY_KIND.get(kind) == "unsupported":
            status = "unsupported"
        else:
            status = "open"

        syslog_target = None
        if nets and nets.interfaces:
            syslog_target = nets.local_address_for(device.ip_address) or nets.interfaces[0]["address"]

        result.append({
            "id": device.id,
            "ip_address": device.ip_address,
            "mac_address": device.mac_address,
            "hostname": device.hostname,
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "os_guess": device.os_guess,
            "open_ports": [{"port": p, "name": PORTS.get(p, str(p))} for p in (device.open_ports or [])],
            "network": device.network,
            "same_subnet": device.same_subnet,
            "first_seen": device.first_seen.isoformat() if device.first_seen else None,
            "last_seen": device.last_seen.isoformat() if device.last_seen else None,
            # Beim letzten Lauf nicht mehr gesehen -> vermutlich aus oder weg.
            "present": bool(last_scan and device.last_seen and device.last_seen >= last_scan),
            "ignored": bool(device.ignored),
            "note": device.note,
            "details": device.details or {},
            "status": status,
            "method": _method(status, kind, device.os_guess),
            "syslog_target": syslog_target,
            "agent": ({
                "id": agent.id, "hostname": agent.hostname, "device_type": agent.device_type,
                "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
                "matched_by": matched_by,
            } if agent else None),
        })
    return result


def _method(status: str, kind: str, os_guess: Optional[str]) -> str:
    if status in ("self", "delivering"):
        return "none"
    # Die FRITZ!Box spricht kein Syslog - ihr Ereignisprotokoll holt der
    # n8n-Sammler ab (server/n8n/rimperio.json) und liefert es im Namen der Box ein.
    if kind == "network" and (os_guess or "").startswith("FRITZ!"):
        return "collector"
    return METHOD_BY_KIND.get(kind, "check")


async def forget_missing(session: AsyncSession, older_than_days: int) -> int:
    """Systeme entfernen, die seit `older_than_days` Tagen keine Suche mehr gefunden hat."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    result = await session.execute(
        delete(DiscoveredDevice).where(DiscoveredDevice.last_seen < cutoff,
                                       DiscoveredDevice.ignored.is_(False)))
    await session.commit()
    return result.rowcount or 0


# =============================================================================
# Zeitplan
# =============================================================================
async def watch_task() -> None:
    """Sucht von selbst: einmal kurz nach dem ersten Start, danach im eingestellten Takt."""
    from .database import async_session

    await asyncio.sleep(STARTUP_DELAY)
    while True:
        try:
            async with async_session() as session:
                cfg = await load_config(session)
                state = await load_state(session)
            if cfg.get("auto_scan") and not STATE.running:
                last = state.get("finished_at")
                interval = float(cfg.get("interval_hours") or 0) * 3600
                due = not last
                if last and interval > 0:
                    due = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() >= interval
                if due:
                    logger.info("Automatische Netzwerksuche startet")
                    await run_scan(trigger="auto", actor="system")
        except Exception as exc:
            logger.warning("Zeitplan der Netzwerksuche: %s", exc)
        await asyncio.sleep(WATCH_INTERVAL)
