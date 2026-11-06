#!/usr/bin/env python3
# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.13.20.58.33
# Changelog:   docs/changelog/syslog.md
# Beschreibung: RIMPERIO - Syslog Server mit erweitertem Parsing
#               Unterstützt: RFC 5424, RFC 3164 (BSD), UniFi Netconsole,
#               UniFi MAC/Model, Cisco IOS, Fortinet key=value,
#               JSON-Logs, generische Formate
# ==============================================================================

import asyncio
import os
import re
import json
import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
import asyncpg

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('syslog_server')

DB_HOST      = os.getenv('DB_HOST',      'localhost')
DB_PORT      = int(os.getenv('DB_PORT',  '5432'))
DB_USER      = os.getenv('DB_USER',      'rimperio')
DB_PASSWORD  = os.getenv('DB_PASSWORD',  '')
DB_NAME      = os.getenv('DB_NAME',      'rimperio')
SYSLOG_PORT  = int(os.getenv('SYSLOG_PORT', '514'))
# Externe Datenbank: komplette Verbindung in einer Variable (hat Vorrang vor den
# Einzelwerten). DB_SSLMODE gehoert gesetzt, sobald die DB ausserhalb des
# Docker-Netzes steht - sonst laufen Zugangsdaten und Logs im Klartext.
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
DB_SSL       = os.getenv('DB_SSLMODE',   '').strip()

BATCH_SIZE       = 100
BATCH_INTERVAL   = 2.0
AGENT_CACHE_TTL  = 300

# Platte fast voll: Syslog verwerfen statt die Datenbank zu ersticken. Bei
# UDP gibt es kein "spaeter nochmal" - aber eine Datenbank, die wegen voller
# Platte nicht mehr startet, verliert ALLES (so geschehen am 2026-10-10).
# Gleiche Grenze wie im Backend (DISK_INGEST_STOP).
DISK_INGEST_STOP = float(os.getenv('DISK_INGEST_STOP', '95'))
_disk_checked_at = 0.0
_disk_blocked = False
_dropped = 0
_dropped_logged_at = 0.0


def disk_blocked() -> bool:
    """Wie `df` gerechnet, hoechstens alle 5 s gemessen."""
    global _disk_checked_at, _disk_blocked
    now = time.monotonic()
    if now - _disk_checked_at > 5:
        _disk_checked_at = now
        try:
            st = os.statvfs('/')
            used = st.f_blocks - st.f_bfree
            usable = used + st.f_bavail
            _disk_blocked = usable > 0 and used / usable * 100.0 >= DISK_INGEST_STOP
        except OSError:
            _disk_blocked = False
    return _disk_blocked

# Spaltenbreiten in logs/agents. Ein einziger zu langer Wert liess sonst den
# ganzen COPY-Stapel scheitern - bis zu 100 Meldungen anderer Geraete gleich mit.
FIELD_LIMITS = {'hostname': 255, 'ip_address': 45, 'level': 20, 'source': 100,
                'device_type': 50}


def sanitize(data: Dict[str, Any]) -> Dict[str, Any]:
    """Textfelder auf Typ und Spaltenbreite bringen.

    JSON-Logs duerfen 'app' oder 'host' als Zahl liefern; COPY verlangt fuer
    VARCHAR aber Text und verwirft sonst den ganzen Stapel.
    """
    for key in ('hostname', 'ip_address', 'level', 'source', 'device_type',
                'message', 'raw_message'):
        value = data.get(key)
        if value is None:
            continue
        value = str(value).replace('\x00', '')
        limit = FIELD_LIMITS.get(key)
        if limit:
            value = value[:limit]
        data[key] = value
    return data

LEVEL_NAMES = {
    0: 'emergency', 1: 'alert', 2: 'critical', 3: 'error',
    4: 'warning',   5: 'notice', 6: 'info',    7: 'debug',
}

# Facility-Namen für lesbares Logging
FACILITY_NAMES = {
    0:  'kern',     1:  'user',    2:  'mail',   3:  'daemon',
    4:  'auth',     5:  'syslog',  6:  'lpr',    7:  'news',
    8:  'uucp',     9:  'cron',    10: 'authpriv', 11: 'ftp',
    16: 'local0',  17: 'local1',  18: 'local2', 19: 'local3',
    20: 'local4',  21: 'local5',  22: 'local6', 23: 'local7',
}

# ==============================================================================
# REGEX PATTERNS
# ==============================================================================

# RFC 5424 – modernes Syslog
# <34>1 2003-10-11T22:14:15.003Z host app - - - message
RFC5424 = re.compile(
    r'^<(\d{1,3})>1\s+'
    r'(\S+)\s+'                          # timestamp (ISO 8601)
    r'(\S+)\s+'                          # hostname
    r'(\S+)\s+'                          # app-name
    r'(\S+)\s+'                          # procid
    r'(\S+)\s+'                          # msgid
    r'(\S+|\[.*?\])\s*'                  # structured-data
    r'(.*)$',                            # message
    re.DOTALL,
)

# BSD / RFC 3164 – klassisches Syslog mit optionalem Jahr und Millisekunden
# <34>Oct 11 22:14:15 mymachine su: 'su root' failed
# <34>Oct 11 22:14:15.123 mymachine su[1234]: message
# <34>Oct 11 2024 22:14:15 mymachine su: message
BSD_SYSLOG = re.compile(
    r'^<(\d{1,3})>'
    r'([A-Z][a-z]{2}\s+\d{1,2}(?:\s+\d{4})?\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)'  # timestamp
    r'\s+(\S+)'                          # hostname
    r'\s+(\S+?)(?:\[\d+\])?:\s*'        # program[pid]:
    r'(.*)$',
)

# UniFi Netconsole – {hex} ist Sequenznummer, NICHT Hostname
# <6>{f1d1} [1234.567890] mclagsyncd[1234]: Message
UNIFI_NETCONSOLE = re.compile(
    r'^<(\d+)>\{([a-fA-F0-9]+)\}\s*\[[\d.]+\]\s*(\S+?)(?:\[\d+\])?:\s*(.*)$'
)

# UniFi MAC/Model – <30>784558fc21cf,U6-LR-6.7.31+15618: hostapd: Message
UNIFI_MAC_MODEL = re.compile(
    r'^<(\d+)>([a-fA-F0-9]{12}),([^:]+):\s*(\S+?):\s*(.*)$'
)

# Cisco IOS – <189>Oct 15 09:13:55.123: %LINK-3-UPDOWN: Interface ...
# Variante mit Sequence: <189>1234: Oct 15 09:13:55: %FAC-SEV-MNEM: msg
CISCO_IOS = re.compile(
    r'^<(\d+)>(?:\d+:\s*)?'
    r'(?:[A-Z][a-z]{2}\s+\d{1,2}\s+[\d:.]+\s*)?'  # optionaler Timestamp
    r':\s*%([A-Z0-9_]+-\d+-[A-Z0-9_]+):\s*(.*)$'
)

# Nur Priority ohne weitere Struktur
SIMPLE = re.compile(r'^<(\d+)>\s*(.*)$')

# Fortinet key=value Erkennung (für lesbare Nachrichten)
_KV_PATTERN = re.compile(r'\b(\w+)=("(?:[^"\\]|\\.)*"|\S+)')

# Eingebettete Timestamps aus Nachrichten entfernen
# z.B. "2024-01-15T12:30:45 message" oder "Jan 15 12:30:45 message"
_EMBEDDED_TS = re.compile(
    r'^(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+|'
    r'[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+)'
)


class SyslogParser:
    """Parst alle gängigen Syslog-Formate."""

    @staticmethod
    def parse_priority(pri: int) -> Tuple[int, str]:
        facility = pri >> 3
        severity = pri & 0x07
        return facility, LEVEL_NAMES.get(severity, 'info')

    @staticmethod
    def format_mac(mac: str) -> str:
        return ':'.join(mac[i:i+2].lower() for i in range(0, 12, 2))

    @staticmethod
    def _clean_message(msg: str) -> str:
        """Entfernt führende eingebettete Timestamps aus dem Nachrichtentext."""
        return _EMBEDDED_TS.sub('', msg).strip()

    @staticmethod
    def _extract_kv(msg: str) -> Dict[str, str]:
        """Extrahiert key=value Paare aus Fortinet/Palo-Alto-Style Nachrichten."""
        result = {}
        for m in _KV_PATTERN.finditer(msg):
            key = m.group(1)
            val = m.group(2).strip('"')
            result[key] = val
        return result

    def parse(self, raw: str, sender_ip: str) -> Dict[str, Any]:
        raw = raw.strip()

        result: Dict[str, Any] = {
            'hostname':    sender_ip,
            'ip_address':  sender_ip,
            'mac_address': None,
            'device_type': 'syslog',
            'facility':    1,
            'level':       'info',
            'source':      'unknown',
            'message':     raw,
            'raw_message': raw,
            'extra_data':  {},
        }

        # ── 1. RFC 5424 ──────────────────────────────────────────────────────
        m = RFC5424.match(raw)
        if m:
            pri, ts, host, app, procid, msgid, sd, msg = m.groups()
            fac, lvl = self.parse_priority(int(pri))
            hostname = host if host != '-' else sender_ip
            source   = app  if app  != '-' else 'unknown'
            message  = self._clean_message(msg) if msg else ''
            # BOM entfernen falls vorhanden
            if message.startswith('\ufeff'):
                message = message[1:]
            result.update({
                'hostname':    hostname,
                'facility':    fac,
                'level':       lvl,
                'source':      source,
                'message':     message or msg,
                'device_type': 'syslog',
                'extra_data':  {
                    'format':    'rfc5424',
                    'timestamp': ts,
                    'procid':    procid if procid != '-' else None,
                    'msgid':     msgid  if msgid  != '-' else None,
                },
            })
            return result

        # ── 2. UniFi Netconsole ──────────────────────────────────────────────
        m = UNIFI_NETCONSOLE.match(raw)
        if m:
            pri, hex_seq, source, msg = m.groups()
            fac, lvl = self.parse_priority(int(pri))
            result.update({
                'hostname':    sender_ip,
                'facility':    fac,
                'level':       lvl,
                'source':      source,
                'message':     msg,
                'device_type': 'unifi_ap',
                'extra_data':  {'format': 'netconsole', 'sequence': hex_seq},
            })
            return result

        # ── 3. UniFi MAC/Model ───────────────────────────────────────────────
        m = UNIFI_MAC_MODEL.match(raw)
        if m:
            pri, mac_raw, model, source, msg = m.groups()
            fac, lvl = self.parse_priority(int(pri))
            mac = self.format_mac(mac_raw)
            result.update({
                'hostname':    mac,
                'mac_address': mac,
                'facility':    fac,
                'level':       lvl,
                'source':      source,
                'message':     msg,
                'device_type': 'unifi_ap',
                'extra_data':  {'format': 'mac_model', 'model': model},
            })
            return result

        # ── 4. Cisco IOS ─────────────────────────────────────────────────────
        m = CISCO_IOS.match(raw)
        if m:
            pri, mnemonic, msg = m.groups()
            fac, lvl = self.parse_priority(int(pri))
            # %LINK-3-UPDOWN → source=LINK, level aus Ziffer
            parts = mnemonic.split('-')
            cisco_source = parts[0] if parts else mnemonic
            # Cisco-Severity überschreibt ggf. das Priority-Level
            if len(parts) >= 2:
                try:
                    lvl = LEVEL_NAMES.get(int(parts[1]), lvl)
                except ValueError:
                    pass
            result.update({
                'hostname':    sender_ip,
                'facility':    fac,
                'level':       lvl,
                'source':      cisco_source,
                'message':     msg,
                'device_type': 'network_device',
                'extra_data':  {'format': 'cisco_ios', 'mnemonic': mnemonic},
            })
            return result

        # ── 5. BSD / RFC 3164 ────────────────────────────────────────────────
        m = BSD_SYSLOG.match(raw)
        if m:
            pri, ts, host, source, msg = m.groups()
            fac, lvl = self.parse_priority(int(pri))
            message = self._clean_message(msg)
            extra: Dict[str, Any] = {'format': 'bsd', 'timestamp': ts.strip()}

            # Fortinet / Palo Alto key=value Erkennung
            if '=' in message:
                kv = self._extract_kv(message)
                if len(kv) >= 3:
                    extra['format'] = 'kv'
                    extra['kv'] = kv
                    # Lesbare Zusammenfassung aus wichtigen Feldern bauen
                    readable = self._kv_to_readable(kv, message)
                    if readable:
                        message = readable

            result.update({
                'hostname':    host,
                'facility':    fac,
                'level':       lvl,
                'source':      source,
                'message':     message or msg,
                'extra_data':  extra,
            })
            return result

        # ── 6. JSON-Log ──────────────────────────────────────────────────────
        # Versuche, JSON nach der Priority zu parsen
        m = SIMPLE.match(raw)
        if m:
            pri, rest = m.groups()
            fac, lvl = self.parse_priority(int(pri))
            rest = rest.strip()
            if rest.startswith('{'):
                try:
                    data = json.loads(rest)
                    msg     = data.get('message') or data.get('msg') or data.get('log') or rest
                    source  = data.get('app') or data.get('service') or data.get('program') or 'unknown'
                    host    = data.get('hostname') or data.get('host') or sender_ip
                    # str(): pino/bunyan liefern das Level als Zahl ("level": 30).
                    # Ohne Umwandlung warf .lower() - die Meldung ging verloren,
                    # bei TCP sogar die ganze Verbindung.
                    j_level = str(data.get('level') or data.get('severity') or '')
                    if j_level.lower() in LEVEL_NAMES.values():
                        lvl = j_level.lower()
                    result.update({
                        'hostname':    host,
                        'facility':    fac,
                        'level':       lvl,
                        'source':      source,
                        'message':     str(msg),
                        'extra_data':  {'format': 'json', 'json': data},
                    })
                    return result
                except (json.JSONDecodeError, TypeError):
                    pass

            # Kein strukturiertes Format – Nachricht so gut wie möglich bereinigen
            message = self._clean_message(rest)
            result.update({'facility': fac, 'level': lvl, 'message': message or rest})

        return result

    @staticmethod
    def _kv_to_readable(kv: Dict[str, str], fallback: str) -> str:
        """Baut eine lesbare Zusammenfassung aus key=value Paaren."""
        # Häufige Felder bevorzugen
        msg = kv.get('msg') or kv.get('message') or kv.get('action', '')
        devname = kv.get('devname') or kv.get('device') or ''
        user = kv.get('user') or kv.get('srcuser') or ''
        src  = kv.get('srcip') or kv.get('src') or ''
        dst  = kv.get('dstip') or kv.get('dst') or ''

        parts = []
        if devname: parts.append(devname)
        if msg:     parts.append(msg)
        if user:    parts.append(f'user={user}')
        if src:     parts.append(f'src={src}')
        if dst:     parts.append(f'dst={dst}')

        if parts:
            return ' | '.join(parts)
        # Wenn keine bekannten Felder, Fallback
        return ''


class DatabaseManager:
    """Async PostgreSQL Verbindung mit Agent-Cache und Batch-Inserts."""

    def __init__(self):
        self.pool = None
        self._agent_cache: Dict[str, Tuple[int, float]] = {}
        self._log_buffer  = []
        # (Geraet, Stufe, Quelle, Text) -> Zeile im aktuellen Stapel. Gleiche
        # Meldungen innerhalb eines Stapels (BATCH_INTERVAL) werden zu einer
        # Zeile mit Anzahl zusammengefasst statt hundertfach gespeichert.
        self._repeat_index: Dict[tuple, list] = {}
        self._buffer_lock = asyncio.Lock()
        self._agents_to_update: set = set()

    async def connect(self):
        for i in range(30):
            try:
                if DATABASE_URL:
                    # Externe Datenbank als fertige URL (gleiche Variable wie im Backend).
                    self.pool = await asyncpg.create_pool(
                        dsn=DATABASE_URL, min_size=2, max_size=10, ssl=DB_SSL or None,
                    )
                    logger.info("DB verbunden ueber DATABASE_URL")
                    return
                self.pool = await asyncpg.create_pool(
                    host=DB_HOST, port=DB_PORT, user=DB_USER,
                    password=DB_PASSWORD, database=DB_NAME,
                    min_size=2, max_size=10, ssl=DB_SSL or None,
                )
                logger.info("DB verbunden: %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
                return
            except Exception as e:
                logger.warning("DB Verbindung fehlgeschlagen (%s/30): %s", i + 1, e)
                await asyncio.sleep(2)
        raise RuntimeError("DB Verbindung dauerhaft fehlgeschlagen")

    async def close(self):
        if self.pool:
            await self._flush_buffer()
            await self.pool.close()

    def _cache_key(self, hostname: str, ip: str, mac: Optional[str]) -> str:
        return f"{mac or ''}/{hostname}/{ip}"

    async def get_or_create_agent(self, hostname: str, ip: str, mac: Optional[str],
                                   device_type: str, extra_data: dict) -> int:
        key = self._cache_key(hostname, ip, mac)
        now = time.monotonic()

        cached = self._agent_cache.get(key)
        if cached and (now - cached[1]) < AGENT_CACHE_TTL:
            self._agents_to_update.add(cached[0])
            return cached[0]

        async with self.pool.acquire() as conn:
            agent_row = None
            if mac:
                agent_row = await conn.fetchrow(
                    "SELECT id, device_type, mac_address FROM agents WHERE mac_address = $1", mac)
            if not agent_row:
                agent_row = await conn.fetchrow(
                    "SELECT id, device_type, mac_address FROM agents WHERE hostname=$1 AND ip_address=$2",
                    hostname, ip)

            if agent_row:
                agent_id = agent_row['id']
                updates, params = [], []
                if (agent_row['device_type'] in (None, 'unknown')) and device_type and device_type != 'unknown':
                    updates.append(f"device_type = ${len(params) + 1}"); params.append(device_type)
                if (not agent_row['mac_address']) and mac:
                    updates.append(f"mac_address = ${len(params) + 1}"); params.append(mac)
                if updates:
                    params.append(agent_id)
                    await conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = ${len(params)}", *params)
            else:
                agent_id = await conn.fetchval(
                    """INSERT INTO agents (hostname, ip_address, mac_address, device_type, extra_data)
                       VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING id""",
                    hostname, ip, mac, device_type, json.dumps(extra_data))

        if len(self._agent_cache) > 10_000:
            # Abgelaufene Eintraege wegwerfen - viele wechselnde Absender
            # (DHCP, Scanner) liessen den Zwischenspeicher sonst endlos wachsen.
            self._agent_cache = {k: v for k, v in self._agent_cache.items()
                                 if now - v[1] < AGENT_CACHE_TTL}
        self._agent_cache[key] = (agent_id, now)
        self._agents_to_update.add(agent_id)
        return agent_id

    async def queue_log(self, data: Dict[str, Any]):
        global _dropped, _dropped_logged_at
        if disk_blocked():
            _dropped += 1
            now = time.monotonic()
            if now - _dropped_logged_at > 60:
                _dropped_logged_at = now
                logger.error("Platte >= %s %% - %s Syslog-Meldungen verworfen",
                             DISK_INGEST_STOP, _dropped)
            return

        data = sanitize(data)
        agent_id = await self.get_or_create_agent(
            data['hostname'], data['ip_address'], data.get('mac_address'),
            data['device_type'], data.get('extra_data', {}))

        extra = data.get('extra_data') or {}
        if not isinstance(extra, dict):
            extra = {'value': extra}
        row = [
            agent_id, data['hostname'], data['ip_address'], data['facility'],
            data['level'], data['source'], data['message'], data['raw_message'],
            extra,
        ]
        key = (agent_id, data['level'], data['source'], data['message'])
        async with self._buffer_lock:
            first = self._repeat_index.get(key)
            if first is not None:
                first_extra = first[8]
                first_extra['repeat'] = first_extra.get('repeat', 1) + 1
                first_extra['repeat_last'] = datetime.utcnow().isoformat()
                return
            self._repeat_index[key] = row
            self._log_buffer.append(row)
            if len(self._log_buffer) >= BATCH_SIZE:
                await self._flush_buffer()

    async def _flush_buffer(self):
        if not self._log_buffer:
            return
        rows = [tuple(row[:8]) + (json.dumps(row[8]),) for row in self._log_buffer]
        self._log_buffer = []
        self._repeat_index = {}
        try:
            async with self.pool.acquire() as conn:
                await conn.copy_records_to_table(
                    'logs',
                    records=rows,
                    columns=['agent_id', 'hostname', 'ip_address', 'facility',
                             'level', 'source', 'message', 'raw_message', 'extra_data'],
                )
        except Exception as e:
            logger.error("Batch-Insert fehlgeschlagen (%s Logs): %s", len(rows), e)
            # Ein Geraet wurde in der Oberflaeche geloescht, der Cache kennt
            # aber noch seine alte ID: der Fremdschluessel laesst dann jeden
            # Stapel mit seinen Meldungen scheitern - bis zu AGENT_CACHE_TTL
            # lang, samt den Meldungen aller anderen Geraete im Stapel.
            # Cache leeren = beim naechsten Mal neu nachschlagen.
            if isinstance(e, asyncpg.exceptions.ForeignKeyViolationError):
                self._agent_cache.clear()

    async def _flush_agent_timestamps(self):
        if not self._agents_to_update:
            return
        agent_ids = list(self._agents_to_update)
        self._agents_to_update.clear()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE agents SET last_seen = NOW() WHERE id = ANY($1::int[])", agent_ids)
        except Exception as e:
            logger.error("Agent last_seen Update fehlgeschlagen: %s", e)

    async def flush_loop(self):
        while True:
            await asyncio.sleep(BATCH_INTERVAL)
            try:
                async with self._buffer_lock:
                    await self._flush_buffer()
                await self._flush_agent_timestamps()
            except Exception as e:
                logger.error("Flush-Loop Fehler: %s", e)


# Hoechstens so viele UDP-Meldungen gleichzeitig in Arbeit. Ist die Datenbank
# langsam (nicht weg - dann scheitert es schnell), stauten sich frueher die
# Aufgaben ohne Grenze im Speicher, bis der Container am Speicher erstickte.
UDP_MAX_INFLIGHT = int(os.getenv('SYSLOG_UDP_MAX_INFLIGHT', '5000'))


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, parser: SyslogParser, db: DatabaseManager):
        self.parser = parser
        self.db = db
        # Referenzen halten: eine Aufgabe ohne Referenz darf der Garbage
        # Collector mitten im Lauf einsammeln.
        self._tasks: set = set()
        self._dropped = 0
        self._dropped_logged_at = 0.0

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        if len(self._tasks) >= UDP_MAX_INFLIGHT:
            self._dropped += 1
            now = time.monotonic()
            if now - self._dropped_logged_at > 60:
                self._dropped_logged_at = now
                logger.error("Datenbank kommt nicht nach - %s UDP-Meldungen verworfen "
                             "(%s in Arbeit)", self._dropped, len(self._tasks))
            return
        # NUL-Zeichen entfernen: PostgreSQL nimmt sie weder in Text noch in
        # JSONB an, und eine solche Meldung riss den ganzen Stapel mit.
        msg = data.decode('utf-8', errors='replace').replace('\x00', '').strip()
        if msg:
            task = asyncio.create_task(self._process(msg, addr[0]))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _process(self, msg: str, ip: str):
        try:
            parsed = self.parser.parse(msg, ip)
            await self.db.queue_log(parsed)
        except Exception as e:
            logger.error("UDP-Verarbeitungsfehler: %s", e)


async def handle_tcp(reader, writer, parser: SyslogParser, db: DatabaseManager):
    addr = writer.get_extra_info('peername')
    ip   = addr[0] if addr else 'unknown'
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            msg = data.decode('utf-8', errors='replace').replace('\x00', '').strip()
            if msg:
                # Fehler pro Zeile abfangen: frueher beendete eine einzige
                # unlesbare Zeile kommentarlos die ganze Verbindung.
                try:
                    parsed = parser.parse(msg, ip)
                    await db.queue_log(parsed)
                except Exception as e:
                    logger.error("TCP-Verarbeitungsfehler (%s): %s", ip, e)
    except ConnectionError:
        pass                                # Gegenstelle hat aufgelegt - normal
    except Exception as e:
        # z.B. eine Zeile ueber 64 KiB (readline-Grenze)
        logger.warning("TCP-Verbindung von %s beendet: %s", ip, e)
    finally:
        writer.close()


async def main():
    logger.info("=" * 60)
    logger.info("RIMPERIO Syslog Server v2026.05.13")
    logger.info("Unterstützte Formate: RFC5424, BSD/RFC3164, UniFi, Cisco IOS, Fortinet kv, JSON")
    logger.info("=" * 60)

    parser = SyslogParser()
    db     = DatabaseManager()
    await db.connect()
    asyncio.create_task(db.flush_loop())

    loop = asyncio.get_event_loop()

    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogUDPProtocol(parser, db),
        local_addr=('0.0.0.0', SYSLOG_PORT))

    tcp_server = await asyncio.start_server(
        lambda r, w: handle_tcp(r, w, parser, db),
        '0.0.0.0', SYSLOG_PORT)

    logger.info("Syslog Server läuft auf UDP/TCP Port %s", SYSLOG_PORT)
    logger.info("Batch: %s Logs oder alle %.1fs", BATCH_SIZE, BATCH_INTERVAL)

    try:
        await tcp_server.serve_forever()
    finally:
        transport.close()
        tcp_server.close()
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
