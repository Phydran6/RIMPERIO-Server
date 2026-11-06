# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.10.08.22.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Kleiner SNMPv2c-Client (nur lesen) fuer die Netzwerksuche
# ==============================================================================
"""
Gerade genug SNMP, um einen Router nach seinen Tabellen zu fragen.

Wozu: Die Netzwerksuche sieht von sich aus nur das eigene Subnetz vollstaendig.
Was hinter einem Router haengt, kennt der Router - in seiner ARP-Tabelle
(welche IP hat welche MAC), in seiner Adresstabelle (an welchen Subnetzen
haengt er selbst) und in seiner Routingtabelle. Genau diese drei Tabellen gibt
praktisch jeder verwaltbare Router und L3-Switch per SNMP heraus.

Warum selbst geschrieben und keine Bibliothek: Gebraucht werden nur GET und
GETBULK in Version 2c, also ein paar Dutzend Zeilen BER-Kodierung. pysnmp
waere ein grosser Brocken mit wechselnder API, nur um dieselben drei Tabellen
zu lesen. Geschrieben wird hier nie - es gibt kein SET.

SNMPv3 ist (noch) nicht dabei. v2c reicht fuer das Lesen im eigenen Netz, die
Community ist dabei das Passwort - und eben nur ein Lesepasswort.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import random
from typing import Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("rimperio.snmp")

# --- Bekannte OIDs ------------------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

# ipNetToMediaPhysAddress: Index = ifIndex.a.b.c.d, Wert = MAC
IP_NET_TO_MEDIA_PHYS = "1.3.6.1.2.1.4.22.1.2"
# ipAdEntNetMask: Index = a.b.c.d (eigene Adresse des Routers), Wert = Maske
IP_AD_ENT_NETMASK = "1.3.6.1.2.1.4.20.1.3"
# ipCidrRouteNextHop: Index = Ziel(4).Maske(4).TOS(1).NextHop(4)
IP_CIDR_ROUTE_NEXTHOP = "1.3.6.1.2.1.4.24.4.1.4"

# --- BER-Tags ----------------------------------------------------------------
_INTEGER = 0x02
_OCTET_STRING = 0x04
_NULL = 0x05
_OID = 0x06
_SEQUENCE = 0x30
_IP_ADDRESS = 0x40
_COUNTER32 = 0x41
_GAUGE32 = 0x42
_TIMETICKS = 0x43
_OPAQUE = 0x44
_COUNTER64 = 0x46
_NO_SUCH_OBJECT = 0x80
_NO_SUCH_INSTANCE = 0x81
_END_OF_MIB_VIEW = 0x82

_GET = 0xA0
_GET_NEXT = 0xA1
_RESPONSE = 0xA2
_GET_BULK = 0xA5

# Markiert "hier ist nichts" - kommt bei nicht vorhandenen OIDs und am Ende.
END = object()

Value = Union[int, bytes, str, None, object]


class SnmpError(Exception):
    """Keine Antwort, kaputte Antwort oder ein Fehlerstatus vom Geraet."""


# =============================================================================
# BER kodieren
# =============================================================================
def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _encode_int(value: int) -> bytes:
    length = max(1, (value.bit_length() + 8) // 8)
    return _tlv(_INTEGER, value.to_bytes(length, "big", signed=True))


def _encode_oid(oid: str) -> bytes:
    arcs = [int(part) for part in oid.strip(".").split(".")]
    if len(arcs) < 2:
        raise ValueError(f"OID zu kurz: {oid}")
    body = bytearray([arcs[0] * 40 + arcs[1]])
    for arc in arcs[2:]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body.extend(reversed(chunk))
    return _tlv(_OID, bytes(body))


def encode_request(pdu_type: int, community: str, request_id: int, oids: Sequence[str],
                   non_repeaters: int = 0, max_repetitions: int = 0) -> bytes:
    """Baut eine vollstaendige SNMPv2c-Nachricht."""
    varbinds = b"".join(_tlv(_SEQUENCE, _encode_oid(oid) + _tlv(_NULL, b"")) for oid in oids)
    if pdu_type == _GET_BULK:
        fields = _encode_int(request_id) + _encode_int(non_repeaters) + _encode_int(max_repetitions)
    else:
        fields = _encode_int(request_id) + _encode_int(0) + _encode_int(0)
    pdu = _tlv(pdu_type, fields + _tlv(_SEQUENCE, varbinds))
    message = _encode_int(1) + _tlv(_OCTET_STRING, community.encode("utf-8")) + pdu
    return _tlv(_SEQUENCE, message)


# =============================================================================
# BER lesen
# =============================================================================
def _read_tlv(data: bytes, pos: int) -> Tuple[int, bytes, int]:
    """Liest ein Element ab `pos`. Gibt (Tag, Inhalt, Position danach) zurueck."""
    if pos + 2 > len(data):
        raise SnmpError("Antwort ist abgeschnitten")
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or pos + count > len(data):
            raise SnmpError("Ungueltige Laengenangabe")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    end = pos + length
    if end > len(data):
        raise SnmpError("Antwort ist abgeschnitten")
    return tag, data[pos:end], end


def _decode_oid(body: bytes) -> str:
    if not body:
        return ""
    first = body[0]
    arcs = [first // 40, first % 40] if first < 80 else [2, first - 80]
    value = 0
    for byte in body[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(value)
            value = 0
    return ".".join(str(arc) for arc in arcs)


def _decode_value(tag: int, body: bytes) -> Value:
    if tag == _INTEGER:
        return int.from_bytes(body, "big", signed=True) if body else 0
    if tag in (_COUNTER32, _GAUGE32, _TIMETICKS, _COUNTER64):
        return int.from_bytes(body, "big", signed=False) if body else 0
    if tag == _IP_ADDRESS:
        return str(ipaddress.IPv4Address(body)) if len(body) == 4 else body
    if tag == _OID:
        return _decode_oid(body)
    if tag == _NULL:
        return None
    if tag in (_NO_SUCH_OBJECT, _NO_SUCH_INSTANCE, _END_OF_MIB_VIEW):
        return END
    return body          # OCTET STRING, Opaque & Co. bleiben Bytes


def decode_response(data: bytes) -> Tuple[int, int, List[Tuple[str, Value]]]:
    """Zerlegt eine Antwort in (request_id, error_status, [(oid, wert), ...])."""
    tag, message, _ = _read_tlv(data, 0)
    if tag != _SEQUENCE:
        raise SnmpError("Keine SNMP-Nachricht")
    _, _, pos = _read_tlv(message, 0)                       # Version
    _, _, pos = _read_tlv(message, pos)                     # Community
    pdu_tag, pdu, _ = _read_tlv(message, pos)
    if pdu_tag != _RESPONSE:
        raise SnmpError(f"Unerwarteter PDU-Typ 0x{pdu_tag:02x}")

    _, rid, pos = _read_tlv(pdu, 0)
    _, status, pos = _read_tlv(pdu, pos)
    _, _, pos = _read_tlv(pdu, pos)                         # error-index
    _, varbind_list, _ = _read_tlv(pdu, pos)

    result: List[Tuple[str, Value]] = []
    vpos = 0
    while vpos < len(varbind_list):
        _, varbind, vpos = _read_tlv(varbind_list, vpos)
        _, oid_body, inner = _read_tlv(varbind, 0)
        value_tag, value_body, _ = _read_tlv(varbind, inner)
        result.append((_decode_oid(oid_body), _decode_value(value_tag, value_body)))

    return (int.from_bytes(rid, "big", signed=True),
            int.from_bytes(status, "big", signed=True), result)


# =============================================================================
# Transport
# =============================================================================
_request_ids = itertools.count(random.randint(1, 1 << 20))


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.waiting: Dict[int, asyncio.Future] = {}

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            rid, status, varbinds = decode_response(data)
        except SnmpError as exc:
            logger.debug("Unlesbare SNMP-Antwort von %s: %s", addr, exc)
            return
        future = self.waiting.pop(rid, None)
        if future and not future.done():
            future.set_result((status, varbinds))

    def error_received(self, exc: Exception) -> None:
        # ICMP "Port nicht erreichbar" - kein SNMP auf dem Geraet.
        for future in self.waiting.values():
            if not future.done():
                future.set_exception(SnmpError(str(exc)))
        self.waiting.clear()


class SnmpClient:
    """Eine Verbindung zu einem Geraet. Mit `async with` benutzen."""

    def __init__(self, host: str, community: str = "public", port: int = 161,
                 timeout: float = 1.5, retries: int = 1) -> None:
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self._transport = None
        self._protocol: Optional[_Protocol] = None

    async def __aenter__(self) -> "SnmpClient":
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            _Protocol, remote_addr=(self.host, self.port))
        return self

    async def __aexit__(self, *exc) -> None:
        if self._transport:
            self._transport.close()

    async def _request(self, pdu_type: int, oids: Sequence[str],
                       max_repetitions: int = 0) -> List[Tuple[str, Value]]:
        assert self._transport and self._protocol
        last_error: Optional[Exception] = None
        for _ in range(self.retries + 1):
            rid = next(_request_ids) & 0x7FFFFFFF
            future = asyncio.get_running_loop().create_future()
            self._protocol.waiting[rid] = future
            self._transport.sendto(encode_request(
                pdu_type, self.community, rid, oids, max_repetitions=max_repetitions))
            try:
                status, varbinds = await asyncio.wait_for(future, self.timeout)
            except asyncio.TimeoutError as exc:
                last_error = exc
                self._protocol.waiting.pop(rid, None)
                continue
            if status != 0:
                raise SnmpError(f"Geraet meldet Fehlerstatus {status}")
            return varbinds
        raise SnmpError(f"Keine Antwort von {self.host}") from last_error

    async def get(self, *oids: str) -> Dict[str, Value]:
        """Einzelwerte lesen. Fehlende OIDs fehlen auch im Ergebnis."""
        return {oid: value for oid, value in await self._request(_GET, oids) if value is not END}

    async def walk(self, base: str, max_rows: int = 5000,
                   max_repetitions: int = 25) -> List[Tuple[str, Value]]:
        """Liest einen ganzen Teilbaum per GETBULK. Gibt (Index-Suffix, Wert) zurueck."""
        prefix = base.strip(".") + "."
        rows: List[Tuple[str, Value]] = []
        current = base.strip(".")
        while len(rows) < max_rows:
            batch = await self._request(_GET_BULK, [current], max_repetitions=max_repetitions)
            if not batch:
                break
            for oid, value in batch:
                if value is END or not oid.startswith(prefix):
                    return rows
                rows.append((oid[len(prefix):], value))
            if batch[-1][0] == current:      # Geraet tritt auf der Stelle
                break
            current = batch[-1][0]
        return rows


# =============================================================================
# Auswerten
# =============================================================================
def as_text(value: Value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00").strip()
    if value is None or value is END:
        return ""
    return str(value)


def as_mac(value: Value) -> Optional[str]:
    if isinstance(value, bytes) and len(value) == 6 and any(value):
        return ":".join(f"{b:02x}" for b in value)
    return None


async def system_info(host: str, community: str, timeout: float = 1.0) -> Optional[dict]:
    """sysName und sysDescr eines Geraets - oder None, wenn es kein SNMP spricht."""
    try:
        async with SnmpClient(host, community, timeout=timeout, retries=0) as client:
            values = await client.get(SYS_DESCR, SYS_NAME, SYS_OBJECT_ID)
    except (SnmpError, OSError):
        return None
    if not values:
        return None
    return {
        "sys_descr": as_text(values.get(SYS_DESCR))[:500],
        "sys_name": as_text(values.get(SYS_NAME))[:255],
        "sys_object_id": as_text(values.get(SYS_OBJECT_ID)),
    }


async def router_tables(host: str, community: str) -> dict:
    """Fragt einen Router nach allem, was die Suche weiterbringt.

    Ergebnis:
      neighbors - [{"ip", "mac"}]  aus der ARP-Tabelle
      subnets   - ["10.0.1.0/24"]  an denen der Router selbst haengt
      routes    - ["10.9.0.0/16"]  die er ueber andere Router erreicht
      system    - sysName/sysDescr
    Wirft SnmpError, wenn der Router gar nicht antwortet.
    """
    async with SnmpClient(host, community, timeout=2.0, retries=1) as client:
        values = await client.get(SYS_DESCR, SYS_NAME)
        if not values:
            raise SnmpError(f"{host} antwortet, liefert aber keine Systemdaten")
        result: dict = {
            "system": {"sys_descr": as_text(values.get(SYS_DESCR))[:500],
                       "sys_name": as_text(values.get(SYS_NAME))[:255]},
            "neighbors": [], "subnets": [], "routes": [],
        }

        try:
            for index, value in await client.walk(IP_NET_TO_MEDIA_PHYS):
                parts = index.split(".")
                mac = as_mac(value)
                if len(parts) >= 5 and mac:
                    result["neighbors"].append({"ip": ".".join(parts[-4:]), "mac": mac})
        except SnmpError as exc:
            logger.info("ARP-Tabelle von %s nicht lesbar: %s", host, exc)

        try:
            for index, value in await client.walk(IP_AD_ENT_NETMASK):
                net = _network(index, value)
                if net and net not in result["subnets"]:
                    result["subnets"].append(net)
        except SnmpError as exc:
            logger.info("Adresstabelle von %s nicht lesbar: %s", host, exc)

        try:
            for index, _ in await client.walk(IP_CIDR_ROUTE_NEXTHOP, max_rows=2000):
                parts = index.split(".")
                if len(parts) >= 8:
                    net = _network(".".join(parts[0:4]), ".".join(parts[4:8]))
                    if net and net not in result["routes"] and net not in result["subnets"]:
                        result["routes"].append(net)
        except SnmpError as exc:
            logger.info("Routingtabelle von %s nicht lesbar: %s", host, exc)

    return result


def _network(address: str, mask: Value) -> Optional[str]:
    """Adresse + Maske -> "10.0.1.0/24". Unbrauchbares (Loopback, /32, 0/0) faellt raus."""
    try:
        net = ipaddress.IPv4Network(f"{address}/{as_text(mask)}", strict=False)
    except ValueError:
        return None
    if net.prefixlen in (0, 32) or net.is_loopback or net.is_link_local or net.is_multicast:
        return None
    return str(net)
