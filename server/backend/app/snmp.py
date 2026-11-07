# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.07.12.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Kleiner SNMP-Client (nur lesen): v1, v2c und v3 (USM)
# ==============================================================================
"""
Gerade genug SNMP, um Geraete zu lesen - fuer die Netzwerksuche und fuer die
SNMP-Ueberwachung (snmpmon.py).

Warum selbst geschrieben und keine Bibliothek: Gebraucht werden nur GET,
GETNEXT und GETBULK. pysnmp waere ein grosser Brocken mit wechselnder API.
Geschrieben wird hier nie - es gibt kein SET.

Versionen:

* **v1 / v2c** - Community als Lesepasswort.
* **v3** (USM, RFC 3414/3826/7860) - Benutzer mit Authentisierung (MD5, SHA-1,
  SHA-224/256/384/512) und Verschluesselung (DES, AES-128). AES-192/256 kann der
  RIMPERIO-Agent als Poller (gosnmp); der Server selbst lehnt sie mit einem
  klaren Hinweis ab.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import itertools
import logging
import os
import random
import struct
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("rimperio.snmp")

# --- Bekannte OIDs ------------------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# ipNetToMediaPhysAddress: Index = ifIndex.a.b.c.d, Wert = MAC
IP_NET_TO_MEDIA_PHYS = "1.3.6.1.2.1.4.22.1.2"
# ipAdEntNetMask: Index = a.b.c.d (eigene Adresse des Routers), Wert = Maske
IP_AD_ENT_NETMASK = "1.3.6.1.2.1.4.20.1.3"
# ipCidrRouteNextHop: Index = Ziel(4).Maske(4).TOS(1).NextHop(4)
IP_CIDR_ROUTE_NEXTHOP = "1.3.6.1.2.1.4.24.4.1.4"

# usmStats - Gruende, aus denen ein v3-Geraet mit einem Report antwortet
_USM_REPORTS = {
    "1.3.6.1.6.3.15.1.1.1.0": "Sicherheitsstufe vom Gerät nicht unterstützt",
    "1.3.6.1.6.3.15.1.1.2.0": "not in time window",
    "1.3.6.1.6.3.15.1.1.3.0": "Benutzer unbekannt",
    "1.3.6.1.6.3.15.1.1.4.0": "unknown engine id",
    "1.3.6.1.6.3.15.1.1.5.0": "Authentisierung falsch (Passwort oder Verfahren)",
    "1.3.6.1.6.3.15.1.1.6.0": "Entschlüsselung fehlgeschlagen (Passwort oder Verfahren)",
}

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
_REPORT = 0xA8

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


def encode_pdu(pdu_type: int, request_id: int, oids: Sequence[str],
               non_repeaters: int = 0, max_repetitions: int = 0) -> bytes:
    varbinds = b"".join(_tlv(_SEQUENCE, _encode_oid(oid) + _tlv(_NULL, b"")) for oid in oids)
    if pdu_type == _GET_BULK:
        fields = _encode_int(request_id) + _encode_int(non_repeaters) + _encode_int(max_repetitions)
    else:
        fields = _encode_int(request_id) + _encode_int(0) + _encode_int(0)
    return _tlv(pdu_type, fields + _tlv(_SEQUENCE, varbinds))


def encode_request(pdu_type: int, community: str, request_id: int, oids: Sequence[str],
                   non_repeaters: int = 0, max_repetitions: int = 0, version: int = 1) -> bytes:
    """Vollstaendige v1- (version=0) bzw. v2c-Nachricht (version=1)."""
    pdu = encode_pdu(pdu_type, request_id, oids, non_repeaters, max_repetitions)
    message = _encode_int(version) + _tlv(_OCTET_STRING, community.encode("utf-8")) + pdu
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


def _int(body: bytes) -> int:
    return int.from_bytes(body, "big", signed=True) if body else 0


def decode_pdu(data: bytes) -> Tuple[int, int, int, List[Tuple[str, Value]]]:
    """Zerlegt eine PDU in (Tag, request_id, error_status, [(oid, wert), ...])."""
    pdu_tag, pdu, _ = _read_tlv(data, 0)
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
    return pdu_tag, _int(rid), _int(status), result


def decode_response(data: bytes) -> Tuple[int, int, List[Tuple[str, Value]]]:
    """v1/v2c-Antwort in (request_id, error_status, [(oid, wert), ...])."""
    tag, message, _ = _read_tlv(data, 0)
    if tag != _SEQUENCE:
        raise SnmpError("Keine SNMP-Nachricht")
    _, _, pos = _read_tlv(message, 0)                       # Version
    _, _, pos = _read_tlv(message, pos)                     # Community
    pdu_tag, rid, status, varbinds = decode_pdu(message[pos:])
    if pdu_tag != _RESPONSE:
        raise SnmpError(f"Unerwarteter PDU-Typ 0x{pdu_tag:02x}")
    return rid, status, varbinds


# =============================================================================
# SNMPv3 - User-based Security Model
# =============================================================================
_AUTH = {
    # Name: (Hash, Laenge des HMAC im Paket)
    "md5": (hashlib.md5, 12), "sha": (hashlib.sha1, 12), "sha1": (hashlib.sha1, 12),
    "sha224": (hashlib.sha224, 16), "sha256": (hashlib.sha256, 24),
    "sha384": (hashlib.sha384, 32), "sha512": (hashlib.sha512, 48),
}
_PRIV = ("des", "aes", "aes128")


def password_to_key(password: str, hash_fn: Callable) -> bytes:
    """RFC 3414 A.2: Passwort auf 1 MB strecken und hashen."""
    pw = password.encode("utf-8")
    if not pw:
        raise SnmpError("Leeres Passwort")
    data = (pw * (1048576 // len(pw) + 1))[:1048576]
    return hash_fn(data).digest()


def localize_key(key: bytes, engine_id: bytes, hash_fn: Callable) -> bytes:
    return hash_fn(key + engine_id + key).digest()


class _V3:
    """Zustand einer v3-Sitzung: Engine, Zeit, Schluessel."""

    def __init__(self, user: str, auth_proto: str, auth_pass: str, priv_proto: str,
                 priv_pass: str, context: str) -> None:
        self.user = user
        self.auth_proto = (auth_proto or "none").lower()
        self.priv_proto = (priv_proto or "none").lower()
        if self.auth_proto != "none" and self.auth_proto not in _AUTH:
            raise SnmpError(f"Authentisierung '{auth_proto}' unbekannt")
        if self.priv_proto not in ("none",) + _PRIV:
            raise SnmpError(f"Verschlüsselung '{priv_proto}' kann der Server nicht — "
                            "bitte einen RIMPERIO-Agent als Poller wählen.")
        if self.priv_proto != "none" and self.auth_proto == "none":
            raise SnmpError("Verschlüsselung geht nur zusammen mit Authentisierung")
        self.auth_pass, self.priv_pass = auth_pass, priv_pass
        self.context = context.encode("utf-8")
        self.engine_id = b""
        self.boots = 0
        self.time = 0
        self.synced_at = 0.0
        self.auth_key = b""
        self.priv_key = b""
        self.salt = random.getrandbits(64)

    @property
    def flags(self) -> int:
        f = 0
        if self.auth_proto != "none":
            f |= 0x01
        if self.priv_proto != "none":
            f |= 0x02
        return f

    def set_engine(self, engine_id: bytes, boots: int, etime: int) -> None:
        changed = engine_id != self.engine_id
        self.engine_id, self.boots, self.time, self.synced_at = engine_id, boots, etime, time.monotonic()
        if changed and self.auth_proto != "none":
            hash_fn = _AUTH[self.auth_proto][0]
            self.auth_key = localize_key(password_to_key(self.auth_pass, hash_fn), engine_id, hash_fn)
            if self.priv_proto != "none":
                self.priv_key = localize_key(password_to_key(self.priv_pass, hash_fn), engine_id, hash_fn)

    def now(self) -> int:
        return self.time + int(time.monotonic() - self.synced_at)

    # --- Verschluesselung -----------------------------------------------------
    def _next_salt(self) -> int:
        self.salt = (self.salt + 1) & 0xFFFFFFFFFFFFFFFF
        return self.salt

    def encrypt(self, plain: bytes, etime: int) -> Tuple[bytes, bytes]:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        if self.priv_proto in ("aes", "aes128"):
            salt = struct.pack(">Q", self._next_salt())
            iv = struct.pack(">II", self.boots, etime) + salt
            enc = Cipher(algorithms.AES(self.priv_key[:16]), _cfb(iv)).encryptor()
            return enc.update(plain) + enc.finalize(), salt
        # DES-CBC (RFC 3414 8.1.1)
        salt = struct.pack(">II", self.boots, self._next_salt() & 0xFFFFFFFF)
        key, pre_iv = self.priv_key[:8], self.priv_key[8:16]
        iv = bytes(a ^ b for a, b in zip(pre_iv, salt))
        pad = (8 - len(plain) % 8) % 8
        enc = Cipher(_des(key), modes.CBC(iv)).encryptor()
        return enc.update(plain + b"\x00" * pad) + enc.finalize(), salt

    def decrypt(self, data: bytes, salt: bytes, boots: int, etime: int) -> bytes:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        if self.priv_proto in ("aes", "aes128"):
            iv = struct.pack(">II", boots, etime) + salt
            dec = Cipher(algorithms.AES(self.priv_key[:16]), _cfb(iv)).decryptor()
            return dec.update(data) + dec.finalize()
        if len(data) % 8 or len(salt) != 8:
            raise SnmpError("Verschlüsselte Antwort hat eine ungültige Länge")
        iv = bytes(a ^ b for a, b in zip(self.priv_key[8:16], salt))
        dec = Cipher(_des(self.priv_key[:8]), modes.CBC(iv)).decryptor()
        return dec.update(data) + dec.finalize()

    # --- Nachrichten ----------------------------------------------------------
    def encode(self, msg_id: int, pdu: bytes, discovery: bool = False) -> bytes:
        flags = 0x04 if discovery else (self.flags | 0x04)
        auth_len = _AUTH[self.auth_proto][1] if (flags & 0x01) else 0
        placeholder = os.urandom(auth_len) if auth_len else b""
        scoped = _tlv(_SEQUENCE, _tlv(_OCTET_STRING, self.engine_id if not discovery else b"")
                      + _tlv(_OCTET_STRING, self.context if not discovery else b"") + pdu)
        priv_params = b""
        etime = 0 if discovery else self.now()
        if flags & 0x02:
            cipher, priv_params = self.encrypt(scoped, etime)
            scoped = _tlv(_OCTET_STRING, cipher)
        sec = _tlv(_SEQUENCE,
                   _tlv(_OCTET_STRING, b"" if discovery else self.engine_id)
                   + _encode_int(0 if discovery else self.boots)
                   + _encode_int(etime)
                   + _tlv(_OCTET_STRING, b"" if discovery else self.user.encode("utf-8"))
                   + _tlv(_OCTET_STRING, placeholder)
                   + _tlv(_OCTET_STRING, priv_params))
        header = _tlv(_SEQUENCE, _encode_int(msg_id) + _encode_int(65507)
                      + _tlv(_OCTET_STRING, bytes([flags])) + _encode_int(3))
        message = bytearray(_tlv(_SEQUENCE, _encode_int(3) + header + _tlv(_OCTET_STRING, sec) + scoped))
        if auth_len:
            at = bytes(message).index(placeholder)
            message[at:at + auth_len] = b"\x00" * auth_len
            digest = hmac.new(self.auth_key, bytes(message), _AUTH[self.auth_proto][0]).digest()[:auth_len]
            message[at:at + auth_len] = digest
        return bytes(message)

    def decode(self, data: bytes) -> dict:
        tag, message, _ = _read_tlv(data, 0)
        if tag != _SEQUENCE:
            raise SnmpError("Keine SNMP-Nachricht")
        _, version, pos = _read_tlv(message, 0)
        if _int(version) != 3:
            raise SnmpError("Keine SNMPv3-Antwort")
        _, header, pos = _read_tlv(message, pos)
        _, msg_id, hpos = _read_tlv(header, 0)
        _, _, hpos = _read_tlv(header, hpos)                  # maxSize
        _, flags_raw, hpos = _read_tlv(header, hpos)
        flags = flags_raw[0] if flags_raw else 0
        _, sec_raw, pos = _read_tlv(message, pos)
        _, sec, _ = _read_tlv(sec_raw, 0)
        _, engine_id, spos = _read_tlv(sec, 0)
        _, boots, spos = _read_tlv(sec, spos)
        _, etime, spos = _read_tlv(sec, spos)
        _, _user, spos = _read_tlv(sec, spos)
        _, auth_params, spos = _read_tlv(sec, spos)
        _, priv_params, spos = _read_tlv(sec, spos)
        boots_i, time_i = _int(boots), _int(etime)
        if flags & 0x01 and self.auth_key and auth_params:
            raw = bytearray(data)
            at = bytes(raw).find(auth_params)
            if at < 0:
                raise SnmpError("Antwort ohne prüfbare Signatur")
            raw[at:at + len(auth_params)] = b"\x00" * len(auth_params)
            hash_fn, n = _AUTH[self.auth_proto]
            expect = hmac.new(self.auth_key, bytes(raw), hash_fn).digest()[:n]
            if not hmac.compare_digest(expect, auth_params):
                raise SnmpError("Signatur der Antwort stimmt nicht")
        rest = message[pos:]
        if flags & 0x02:
            _, cipher, _ = _read_tlv(rest, 0)
            rest = self.decrypt(cipher, priv_params, boots_i, time_i)
        _, scoped, _ = _read_tlv(rest, 0)
        _, _ctx_engine, cpos = _read_tlv(scoped, 0)
        _, _ctx_name, cpos = _read_tlv(scoped, cpos)
        pdu_tag, rid, status, varbinds = decode_pdu(scoped[cpos:])
        return {"msg_id": _int(msg_id), "engine_id": engine_id, "boots": boots_i, "time": time_i,
                "pdu": pdu_tag, "rid": rid, "status": status, "varbinds": varbinds}


def _cfb(iv: bytes):
    try:
        from cryptography.hazmat.decrepit.ciphers.modes import CFB
    except ImportError:                                             # aeltere cryptography
        from cryptography.hazmat.primitives.ciphers.modes import CFB
    return CFB(iv)


def _des(key: bytes):
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:                                             # aeltere cryptography
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    return TripleDES(key * 3)                                       # K|K|K == einfaches DES


# =============================================================================
# Transport
# =============================================================================
_request_ids = itertools.count(random.randint(1, 1 << 20))


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.waiting: Dict[int, asyncio.Future] = {}
        self.decoder: Optional[Callable[[bytes], Tuple[int, object]]] = None

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            if self.decoder:
                key, payload = self.decoder(data)
            else:
                rid, status, varbinds = decode_response(data)
                key, payload = rid, (status, varbinds)
        except SnmpError as exc:
            logger.debug("Unlesbare SNMP-Antwort von %s: %s", addr, exc)
            # Eine v3-Antwort, die wir nicht pruefen/entschluesseln koennen: allen
            # Wartenden Bescheid geben, statt in den Zeitablauf zu laufen.
            if self.decoder:
                for future in self.waiting.values():
                    if not future.done():
                        future.set_exception(exc)
                self.waiting.clear()
            return
        future = self.waiting.pop(key, None)
        if future and not future.done():
            future.set_result(payload)

    def error_received(self, exc: Exception) -> None:
        # ICMP "Port nicht erreichbar" - kein SNMP auf dem Geraet.
        for future in self.waiting.values():
            if not future.done():
                future.set_exception(SnmpError(str(exc)))
        self.waiting.clear()


class SnmpClient:
    """Eine Verbindung zu einem Geraet. Mit `async with` benutzen.

    version: "1", "2c" (Standard) oder "3". Fuer v3 kommen user, auth_proto,
    auth_pass, priv_proto, priv_pass und context dazu.
    """

    def __init__(self, host: str, community: str = "public", port: int = 161,
                 timeout: float = 1.5, retries: int = 1, version: str = "2c",
                 user: str = "", auth_proto: str = "none", auth_pass: str = "",
                 priv_proto: str = "none", priv_pass: str = "", context: str = "") -> None:
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.version = str(version or "2c")
        self._transport = None
        self._protocol: Optional[_Protocol] = None
        self._v3 = _V3(user, auth_proto, auth_pass, priv_proto, priv_pass, context) \
            if self.version == "3" else None

    async def __aenter__(self) -> "SnmpClient":
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            _Protocol, remote_addr=(self.host, self.port))
        if self._v3:
            self._protocol.decoder = self._decode_v3
            await self._discover()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._transport:
            self._transport.close()

    def _decode_v3(self, data: bytes) -> Tuple[int, object]:
        out = self._v3.decode(data)
        return out["msg_id"], out

    async def _send(self, key: int, packet: bytes):
        assert self._transport and self._protocol
        last_error: Optional[Exception] = None
        for _ in range(self.retries + 1):
            future = asyncio.get_running_loop().create_future()
            self._protocol.waiting[key] = future
            self._transport.sendto(packet)
            try:
                return await asyncio.wait_for(future, self.timeout)
            except asyncio.TimeoutError as exc:
                last_error = exc
                self._protocol.waiting.pop(key, None)
        raise SnmpError(f"Keine Antwort von {self.host}") from last_error

    async def _discover(self) -> None:
        """v3: Engine-ID, Boots und Zeit des Geraets erfragen (RFC 3414 4)."""
        msg_id = next(_request_ids) & 0x7FFFFFFF
        out = await self._send(msg_id, self._v3.encode(msg_id, encode_pdu(_GET, msg_id, []), discovery=True))
        if not out["engine_id"]:
            raise SnmpError("Gerät nennt keine Engine-ID")
        self._v3.set_engine(out["engine_id"], out["boots"], out["time"])
        if self._v3.flags & 0x01:
            # Zeitabgleich mit einer authentisierten Anfrage - die Antwort (oder
            # ein notInTimeWindow-Report) traegt die gueltige Zeit.
            try:
                await self._request(_GET, [SYS_UPTIME])
            except SnmpError as exc:
                if "time window" not in str(exc):
                    raise

    async def _request(self, pdu_type: int, oids: Sequence[str],
                       max_repetitions: int = 0) -> List[Tuple[str, Value]]:
        if self._v3 is None:
            rid = next(_request_ids) & 0x7FFFFFFF
            packet = encode_request(pdu_type, self.community, rid, oids,
                                    max_repetitions=max_repetitions,
                                    version=0 if self.version == "1" else 1)
            status, varbinds = await self._send(rid, packet)
            if status != 0:
                if self.version == "1" and status == 2:          # noSuchName
                    return [(oid, END) for oid in oids]
                raise SnmpError(f"Geraet meldet Fehlerstatus {status}")
            return varbinds
        for attempt in range(2):
            rid = next(_request_ids) & 0x7FFFFFFF
            pdu = encode_pdu(pdu_type, rid, oids, max_repetitions=max_repetitions)
            out = await self._send(rid, self._v3.encode(rid, pdu))
            if out["pdu"] == _REPORT:
                oid = out["varbinds"][0][0] if out["varbinds"] else ""
                reason = _USM_REPORTS.get(oid, f"Report {oid}")
                if reason == "not in time window" and attempt == 0:
                    self._v3.set_engine(out["engine_id"] or self._v3.engine_id, out["boots"], out["time"])
                    continue
                raise SnmpError(f"SNMPv3: {reason}")
            if out["status"] != 0:
                raise SnmpError(f"Geraet meldet Fehlerstatus {out['status']}")
            return out["varbinds"]
        raise SnmpError("SNMPv3: Zeitabgleich mit dem Gerät gelingt nicht")

    async def get(self, *oids: str) -> Dict[str, Value]:
        """Einzelwerte lesen. Fehlende OIDs fehlen auch im Ergebnis."""
        out: Dict[str, Value] = {}
        for i in range(0, len(oids), 20):
            for oid, value in await self._request(_GET, oids[i:i + 20]):
                if value is not END:
                    out[oid] = value
        return out

    async def walk(self, base: str, max_rows: int = 5000,
                   max_repetitions: int = 25) -> List[Tuple[str, Value]]:
        """Liest einen ganzen Teilbaum (GETBULK, bei v1 GETNEXT). Gibt (Index-Suffix, Wert)."""
        prefix = base.strip(".") + "."
        rows: List[Tuple[str, Value]] = []
        current = base.strip(".")
        while len(rows) < max_rows:
            if self.version == "1":
                batch = await self._request(_GET_NEXT, [current])
            else:
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
