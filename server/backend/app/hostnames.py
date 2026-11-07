# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.07.20.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Hostnamen gefundener Systeme ermitteln
#               (DNS, mDNS, NetBIOS)
# ==============================================================================
"""
Wie heisst das System hinter einer IP?

Eine IP allein hilft niemandem - erst der Name sagt, welcher Rechner das ist.
Kein Weg kennt jeden Namen, deshalb werden mehrere gefragt, gleichzeitig:

1. **Rueckwaerts-DNS ueber den Resolver des Containers.** Der klassische Weg.
   Im Container fragt er aber Dockers eingebauten DNS, und der kennt die
   Namen im LAN oft nicht.
2. **Rueckwaerts-DNS direkt beim DNS des LANs.** Die Nameserver des *Hosts*
   (aus seiner resolv.conf) und das Standard-Gateway. Router wie die
   FRITZ!Box, OPNsense oder ein Windows-DNS kennen jeden Namen, den sie per
   DHCP vergeben haben - genau die Namen, die man sucht.
3. **mDNS (Bonjour/Avahi).** Unicast-Anfrage an Port 5353 des Geraets. Damit
   melden sich Macs, Linux-Rechner mit Avahi, Drucker, NAS und viele
   IoT-Geraete mit ihrem Namen - auch ohne DNS-Eintrag.
4. **NetBIOS.** Statusabfrage an Port 137. Windows-Rechner und Samba-Server
   nennen dabei ihren Computernamen.

Alles laeuft ueber UDP ohne Zusatzpakete und ohne Rechte. Was nicht
antwortet, kostet nur die Wartezeit, und die laeuft fuer alle Wege parallel.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

TIMEOUT = 1.5

# Reihenfolge = Vorrang: ein DNS-Name ist der, unter dem das System im Netz
# erreichbar ist; mDNS und NetBIOS sind Namen, die es sich selbst gibt.
SOURCES = ("dns", "dns-lan", "mdns", "netbios")
SOURCE_LABELS = {
    "dns": "DNS",
    "dns-lan": "DNS des LANs",
    "mdns": "mDNS",
    "netbios": "NetBIOS",
    "snmp": "SNMP",
    "agent": "Agent",
}


# =============================================================================
# UDP
# =============================================================================
async def _udp_query(host: str, port: int, payload: bytes, match: bytes,
                     timeout: float = TIMEOUT) -> Optional[bytes]:
    """Schickt ein Paket und wartet auf die erste Antwort mit passender Kennung.

    UDP geht verloren - nach der halben Wartezeit wird einmal nachgeschickt.
    """
    loop = asyncio.get_running_loop()
    answer: asyncio.Future = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:
            if not answer.done() and data[:len(match)] == match:
                answer.set_result(data)

        def error_received(self, exc: Exception) -> None:   # ICMP "Port unreachable"
            if not answer.done():
                answer.set_result(None)

    try:
        transport, _ = await loop.create_datagram_endpoint(
            _Proto, remote_addr=(host, port), family=socket.AF_INET)
    except OSError:
        return None
    try:
        transport.sendto(payload)
        with contextlib.suppress(asyncio.TimeoutError):
            return await asyncio.wait_for(asyncio.shield(answer), timeout / 2)
        transport.sendto(payload)
        with contextlib.suppress(asyncio.TimeoutError):
            return await asyncio.wait_for(answer, timeout / 2)
        return None
    finally:
        transport.close()


# =============================================================================
# DNS / mDNS
# =============================================================================
def ptr_name(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def build_ptr_query(ip: str, txid: bytes, *, mdns: bool = False) -> bytes:
    # mDNS: keine Rekursion; Klasse mit gesetztem QU-Bit = "bitte direkt an mich".
    flags = 0x0000 if mdns else 0x0100
    header = txid + struct.pack("!HHHHH", flags, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in ptr_name(ip).split(".")) + b"\0"
    return header + qname + struct.pack("!HH", 12, 0x8001 if mdns else 1)


def _read_name(data: bytes, offset: int, depth: int = 0) -> Tuple[str, int]:
    """Liest einen (ggf. komprimierten) DNS-Namen. Gibt (Name, Ende) zurueck."""
    labels: List[str] = []
    end: Optional[int] = None
    while True:
        if offset >= len(data) or depth > 20:
            raise ValueError("kaputter Name")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("kaputter Zeiger")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if end is None:
                end = offset + 2
            offset = pointer
            depth += 1
            continue
        offset += 1
        labels.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), (end if end is not None else offset)


def parse_ptr_answer(data: bytes) -> str:
    """Den ersten PTR-Eintrag einer DNS-Antwort - oder ""."""
    if len(data) < 12:
        return ""
    flags, qd, an = struct.unpack("!HHH", data[2:8])
    if not flags & 0x8000 or flags & 0x000F:          # keine Antwort / Fehlercode
        return ""
    offset = 12
    try:
        for _ in range(qd):
            _, offset = _read_name(data, offset)
            offset += 4
        for _ in range(an):
            _, offset = _read_name(data, offset)
            rtype, _, _, rdlen = struct.unpack("!HHIH", data[offset:offset + 10])
            offset += 10
            if rtype == 12:
                name, _ = _read_name(data, offset)
                return name
            offset += rdlen
    except (ValueError, struct.error):
        return ""
    return ""


async def dns_ptr(ip: str, server: str, port: int = 53, *, mdns: bool = False,
                  timeout: float = TIMEOUT) -> str:
    txid = b"\0\0" if mdns else os.urandom(2)
    data = await _udp_query(server, port, build_ptr_query(ip, txid, mdns=mdns), txid, timeout)
    return parse_ptr_answer(data) if data else ""


async def mdns_name(ip: str, timeout: float = TIMEOUT) -> str:
    return await dns_ptr(ip, ip, 5353, mdns=True, timeout=timeout)


_dns_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="discovery-dns")


async def system_ptr(ip: str, timeout: float = 2.0) -> str:
    loop = asyncio.get_running_loop()
    try:
        name, _, _ = await asyncio.wait_for(
            loop.run_in_executor(_dns_pool, socket.gethostbyaddr, ip), timeout)
    except (asyncio.TimeoutError, OSError):
        return ""
    return name


async def lan_ptr(ip: str, servers: List[str], timeout: float = TIMEOUT) -> str:
    """Fragt die Nameserver des LANs der Reihe nach - der erste Treffer zaehlt."""
    for server in servers:
        name = await dns_ptr(ip, server, timeout=timeout)
        if name:
            return name
    return ""


# =============================================================================
# NetBIOS
# =============================================================================
# Name "*" (Wildcard), 16 Byte, halbbyteweise kodiert: "*" -> "CK", \0 -> "AA".
_NB_WILDCARD = b"\x20" + b"CK" + b"A" * 30 + b"\0"


def build_netbios_query(txid: bytes) -> bytes:
    return txid + struct.pack("!HHHHH", 0, 1, 0, 0, 0) + _NB_WILDCARD + struct.pack("!HH", 0x21, 1)


def parse_netbios_answer(data: bytes) -> str:
    """Der Computername (Suffix 0x00, eindeutig) aus einer Statusantwort."""
    try:
        an = struct.unpack("!H", data[6:8])[0]
        if not an:
            return ""
        _, offset = _read_name(data, 12)
        rtype = struct.unpack("!H", data[offset:offset + 2])[0]
        if rtype != 0x21:
            return ""
        offset += 10
        count = data[offset]
        offset += 1
        fallback = ""
        for _ in range(count):
            entry = data[offset:offset + 18]
            if len(entry) < 18:
                break
            offset += 18
            name = entry[:15].decode("latin-1").rstrip(" \0")
            suffix = entry[15]
            group = struct.unpack("!H", entry[16:18])[0] & 0x8000
            if suffix == 0x00 and not group and name:
                return name
            if suffix == 0x20 and not group and name and not fallback:
                fallback = name
        return fallback
    except (ValueError, IndexError, struct.error):
        return ""


async def netbios_name(ip: str, timeout: float = TIMEOUT) -> str:
    txid = os.urandom(2)
    data = await _udp_query(ip, 137, build_netbios_query(txid), txid, timeout)
    return parse_netbios_answer(data) if data else ""


# =============================================================================
# Zusammenfuehren
# =============================================================================
_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,62})$", re.I)
_PLACEHOLDER = re.compile(r"^(none(-\d+)?|localhost|unknown|unnamed|host|[0-9a-f]{12})$", re.I)


def clean(name: str, ip: str, source: str) -> str:
    """Bereinigt einen gemeldeten Namen. "" fuer alles, was nichts aussagt."""
    name = (name or "").strip().rstrip(".")
    if not name:
        return ""
    if source == "netbios":
        name = name.lower()
    if source == "mdns" and name.lower().endswith(".local"):
        name = name[:-6]
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(name)
        return ""                                    # eine IP ist kein Name
    if any(not _LABEL.match(part) for part in name.split(".")):
        return ""
    # Platzhalter, die Geraete ohne eingestellten Namen melden ("none.local",
    # "none-11.local", "localhost") oder nur die eigene MAC ("ecb5fa035110").
    if _PLACEHOLDER.match(name.split(".")[0]):
        return ""
    # Automatische Namen des Providers ("192-168-1-5.dyn.example.net") sagen
    # nichts ueber das Geraet.
    parts = ip.split(".")
    low = name.lower()
    if "-".join(parts) in low or "-".join(reversed(parts)) in low or ptr_name(ip) in low:
        return ""
    return name[:255]


async def resolve(ip: str, lan_servers: List[str]) -> Tuple[str, str, dict]:
    """Fragt alle Wege parallel. Gibt (Name, Quelle, alle Funde) zurueck."""
    results = await asyncio.gather(
        system_ptr(ip),
        lan_ptr(ip, lan_servers) if lan_servers else _empty(),
        mdns_name(ip),
        netbios_name(ip),
        return_exceptions=True,
    )
    names = {}
    for source, raw in zip(SOURCES, results):
        if isinstance(raw, BaseException):
            continue
        name = clean(raw, ip, source)
        if name:
            names[source] = name
    for source in SOURCES:
        if source in names:
            return names[source], source, names
    return "", "", names


async def _empty() -> str:
    return ""


def parse_resolv_conf(text: str) -> List[str]:
    """Nameserver aus einer resolv.conf - ohne Loopback (der lokale Stub bringt nichts)."""
    servers: List[str] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            with contextlib.suppress(ValueError):
                addr = ipaddress.IPv4Address(parts[1])
                if not addr.is_loopback and str(addr) not in servers:
                    servers.append(str(addr))
    return servers
