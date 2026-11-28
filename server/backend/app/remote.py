# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Fernzugriff: Live-Shell, RDP und Live-Sitzung ueber
#               einen verschluesselten Tunnel des Agents (keine offenen Ports).
# ==============================================================================
"""
Fernzugriff ueber den Agent-Tunnel
==================================

     Browser ──wss──► RIMPERIO ◄──wss── Agent (ausgehend, HTTPS)
                         │                  │
     xterm  ◄──────── Live-Shell ──────── PTY / ConPTY
     Guacamole-JS ◄── guacd ── TCP ─────► 127.0.0.1:3389 (RDP)
                                          127.0.0.1:5900 (VNC, Live-Sitzung)

* Der Rechner oeffnet nie einen Port nach aussen. Auf Anforderung baut der Agent
  selbst eine WebSocket-Verbindung zu RIMPERIO auf - durch dieselbe HTTPS-
  Verbindung, ueber die er sich ohnehin meldet. Verschluesselt ist alles, was
  ueber HTTPS laeuft; Zugangsdaten fuer RDP liegen nur im Speicher dieser
  Sitzung und werden nach dem Verbinden verworfen.
* **Live-Shell**: ein echtes Terminal (Linux: PTY als Dienstkonto, Windows:
  ConPTY als SYSTEM) im Browser.
* **RDP**: der Remotedesktop des Rechners, im Browser gezeichnet von Apache
  Guacamole (guacd als Container neben RIMPERIO). guacd verbindet sich mit
  einem Port dieses Backends, der direkt in den Agent-Tunnel fuehrt.
* **Live-Sitzung** (wie bei NinjaOne): der Bildschirm der angemeldeten Person
  ueber VNC auf 127.0.0.1:5900. Der VNC-Dienst nimmt nur Verbindungen von
  localhost an - erreichbar also ausschliesslich durch den Tunnel.

Jede Sitzung hat eine zufaellige ID, gehoert genau einem Administrator, gilt
fuer genau einen Tunnel und verfaellt, wenn sie nicht binnen 60 s genutzt wird.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("rimperio.remote")

GUACD_HOST = os.getenv("GUACD_HOST", "guacd")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))
# Unter welchem Namen guacd dieses Backend erreicht (Docker-Netz).
TUNNEL_HOST = os.getenv("RIMPERIO_TUNNEL_HOST", "backend")
SESSION_TTL = 60          # Sekunden bis zur ersten Verbindung
MAX_SESSIONS = 50


@dataclass
class Session:
    id: str
    kind: str                       # shell | rdp | vnc
    agent_id: int
    hostname: str
    user: str
    created: float = field(default_factory=time.time)
    agent_ws: object = None
    agent_ready: asyncio.Event = field(default_factory=asyncio.Event)
    agent_error: str = ""
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    used: bool = False
    params: Dict[str, str] = field(default_factory=dict)   # RDP-Zugang, VNC-Passwort (nur im Speicher)
    to_agent: asyncio.Queue = field(default_factory=asyncio.Queue)
    from_agent: asyncio.Queue = field(default_factory=asyncio.Queue)


SESSIONS: Dict[str, Session] = {}


def _prune() -> None:
    now = time.time()
    for sid, s in list(SESSIONS.items()):
        if (not s.used and now - s.created > SESSION_TTL) or s.closed.is_set():
            SESSIONS.pop(sid, None)


def create(kind: str, agent_id: int, hostname: str, user: str, params: Optional[dict] = None) -> Session:
    _prune()
    if len(SESSIONS) >= MAX_SESSIONS:
        raise RuntimeError("Zu viele offene Fernzugriffe – bitte alte Fenster schließen.")
    s = Session(id=secrets.token_urlsafe(24), kind=kind, agent_id=agent_id, hostname=hostname, user=user,
                params={k: str(v) for k, v in (params or {}).items() if v not in (None, "")})
    SESSIONS[s.id] = s
    return s


def get(sid: str) -> Optional[Session]:
    _prune()
    return SESSIONS.get(sid)


def public(s: Session) -> dict:
    return {"id": s.id, "kind": s.kind, "hostname": s.hostname, "user": s.user,
            "created": s.created, "connected": s.agent_ready.is_set()}


# -----------------------------------------------------------------------------
# Agent-Seite: WebSocket des Agents bedienen
# -----------------------------------------------------------------------------
async def serve_agent(s: Session, ws) -> None:
    """Haelt die Verbindung zum Agent: was er schickt -> from_agent, was der
    Browser/guacd schickt (to_agent) -> zum Agent."""
    import json
    s.agent_ws = ws

    async def reader() -> None:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await s.from_agent.put(("data", msg["bytes"]))
            elif msg.get("text") is not None:
                try:
                    ctl = json.loads(msg["text"])
                except ValueError:
                    continue
                if ctl.get("type") == "ready":
                    s.agent_ready.set()
                elif ctl.get("type") == "error":
                    s.agent_error = ctl.get("message") or "Fehler auf dem Rechner"
                    s.agent_ready.set()
                await s.from_agent.put(("ctl", ctl))
        await s.from_agent.put(("eof", None))

    async def writer() -> None:
        while True:
            kind, payload = await s.to_agent.get()
            if kind == "eof":
                break
            if kind == "data":
                await ws.send_bytes(payload)
            else:
                await ws.send_text(json.dumps(payload))

    tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    finally:
        s.closed.set()
        if not s.agent_ready.is_set():
            s.agent_error = s.agent_error or "Der Rechner hat den Tunnel geschlossen."
            s.agent_ready.set()


async def wait_agent(s: Session, timeout: float = 40.0) -> None:
    try:
        await asyncio.wait_for(s.agent_ready.wait(), timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Der Rechner hat sich nicht gemeldet (offline, oder Agent zu alt für Fernzugriff – "
                           "bitte den Agent aktualisieren).") from exc
    if s.agent_error:
        raise RuntimeError(s.agent_error)


# -----------------------------------------------------------------------------
# Guacamole-Protokoll (guacd)
# -----------------------------------------------------------------------------
def encode(*elements: str) -> str:
    return ",".join(f"{len(str(e))}.{e}" for e in elements) + ";"


def parse(buffer: str) -> Tuple[List[List[str]], str, List[str]]:
    """Zerlegt den Puffer in vollstaendige Anweisungen.

    Liefert (anweisungen, rest, rohtexte). Laengenangaben zaehlen Zeichen."""
    out: List[List[str]] = []
    raws: List[str] = []
    pos = 0
    n = len(buffer)
    while True:
        start = pos
        elems: List[str] = []
        complete = False
        while True:
            dot = buffer.find(".", pos)
            if dot < 0:
                break
            try:
                length = int(buffer[pos:dot])
            except ValueError:
                raise ValueError("Ungültige Guacamole-Anweisung")
            end = dot + 1 + length
            if end >= n:
                break
            elems.append(buffer[dot + 1:end])
            sep = buffer[end]
            pos = end + 1
            if sep == ";":
                complete = True
                break
            if sep != ",":
                raise ValueError("Ungültige Guacamole-Anweisung")
        if not complete:
            return out, buffer[start:], raws
        out.append(elems)
        raws.append(buffer[start:pos])


class GuacdConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.buf = ""

    async def send(self, text: str) -> None:
        self.writer.write(text.encode("utf-8"))
        await self.writer.drain()

    async def read_instruction(self, timeout: float = 15.0) -> List[str]:
        while True:
            ins, rest, _ = parse(self.buf)
            if ins:
                first_len = len(encode(*ins[0]))
                self.buf = self.buf[first_len:]
                return ins[0]
            chunk = await asyncio.wait_for(self.reader.read(65536), timeout)
            if not chunk:
                raise RuntimeError("guacd hat die Verbindung geschlossen.")
            self.buf += chunk.decode("utf-8", errors="replace")


def connect_values(kind: str, arg_names: List[str], host: str, port: int, s: Session,
                   width: int, height: int, dpi: int) -> List[str]:
    p = s.params
    base = {"hostname": host, "port": str(port)}
    if kind == "rdp":
        base.update({
            "username": p.get("username", ""), "password": p.get("password", ""), "domain": p.get("domain", ""),
            "security": p.get("security", "any"), "ignore-cert": "true", "resize-method": "display-update",
            "width": str(width), "height": str(height), "dpi": str(dpi), "server-layout": p.get("layout", "de-de-qwertz"),
            "enable-font-smoothing": "true", "disable-audio": "true", "enable-drive": "false",
            "normalize-clipboard": "windows",
        })
    else:
        base.update({"password": p.get("password", ""), "color-depth": "24", "cursor": "remote",
                     "encodings": "tight zrle ultra copyrect hextile zlib corre rre raw"})
    values = []
    for name in arg_names:
        if name.startswith("VERSION_"):
            values.append(name)
        else:
            values.append(base.get(name, ""))
    return values


async def open_guacd(s: Session, width: int, height: int, dpi: int) -> Tuple[GuacdConnection, asyncio.AbstractServer]:
    """Lokalen Port fuer guacd oeffnen (fuehrt in den Agent-Tunnel), dann den
    Handschlag mit guacd. Zurueck: verbundene guacd-Verbindung und der Server."""
    accepted = asyncio.Event()

    async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if accepted.is_set():                    # nur eine Verbindung je Sitzung
            writer.close()
            return
        accepted.set()
        await pipe_tcp_to_agent(s, reader, writer)

    server = await asyncio.start_server(on_conn, host="0.0.0.0", port=0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(GUACD_HOST, GUACD_PORT), 10)
    except (OSError, asyncio.TimeoutError) as exc:
        server.close()
        raise RuntimeError(f"guacd ({GUACD_HOST}:{GUACD_PORT}) nicht erreichbar – läuft der Container guacd? "
                           "(docker compose up -d guacd)") from exc
    g = GuacdConnection(reader, writer)
    protocol = "rdp" if s.kind == "rdp" else "vnc"
    await g.send(encode("select", protocol))
    args = await g.read_instruction()
    if not args or args[0] != "args":
        raise RuntimeError(f"guacd antwortet unerwartet: {args[:2]}")
    await g.send(encode("size", str(width), str(height), str(dpi)))
    await g.send(encode("audio"))
    await g.send(encode("video"))
    await g.send(encode("image", "image/png", "image/jpeg", "image/webp"))
    await g.send(encode("timezone", "Europe/Berlin"))
    await g.send(encode("connect", *connect_values(s.kind, args[1:], TUNNEL_HOST, port, s, width, height, dpi)))
    ready = await g.read_instruction(timeout=20)
    if ready[0] != "ready":
        raise RuntimeError(f"guacd: {' '.join(ready)[:200]}")
    s.params.pop("password", None)              # Zugang nur fuer den Handschlag
    return g, server


async def pipe_tcp_to_agent(s: Session, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Verbindung von guacd <-> Agent-Tunnel."""
    async def up() -> None:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await s.to_agent.put(("data", data))
        await s.to_agent.put(("ctl", {"type": "close"}))

    async def down() -> None:
        while True:
            kind, payload = await s.from_agent.get()
            if kind == "eof":
                break
            if kind == "data":
                writer.write(payload)
                await writer.drain()
            elif kind == "ctl" and payload.get("type") == "exit":
                break
        writer.close()

    t = [asyncio.create_task(up()), asyncio.create_task(down())]
    await asyncio.wait(t, return_when=asyncio.FIRST_COMPLETED)
    for task in t:
        task.cancel()
    try:
        writer.close()
    except Exception:                                               # defensiv
        pass


# -----------------------------------------------------------------------------
# VNC fuer die Live-Sitzung (Windows): TightVNC nur fuer localhost
# -----------------------------------------------------------------------------
LIVE_SETUP_PS = r"""
$ErrorActionPreference = 'Stop'
$pw = '__PW__'
$svc = Get-Service -Name tvnserver -ErrorAction SilentlyContinue
if (-not $svc) {
  $arch = if ([Environment]::Is64BitOperatingSystem) { '64bit' } else { '32bit' }
  $url = "https://www.tightvnc.com/download/2.8.85/tightvnc-2.8.85-gpl-setup-$arch.msi"
  $msi = Join-Path $env:TEMP 'rimperio-tightvnc.msi'
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $msi
  $args = "/i `"$msi`" /quiet /norestart ADDLOCAL=Server SERVER_REGISTER_AS_SERVICE=1 SERVER_ADD_FIREWALL_EXCEPTION=0 " +
          "SET_USEVNCAUTHENTICATION=1 VALUE_OF_USEVNCAUTHENTICATION=1 SET_PASSWORD=1 VALUE_OF_PASSWORD=$pw " +
          "SET_ALLOWLOOPBACK=1 VALUE_OF_ALLOWLOOPBACK=1 SET_LOOPBACKONLY=1 VALUE_OF_LOOPBACKONLY=1 " +
          "SET_ACCEPTHTTPCONNECTIONS=1 VALUE_OF_ACCEPTHTTPCONNECTIONS=0 SET_RUNCONTROLINTERFACE=1 VALUE_OF_RUNCONTROLINTERFACE=0"
  $p = Start-Process msiexec.exe -ArgumentList $args -Wait -PassThru
  Remove-Item $msi -Force -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) { throw "TightVNC-Installation: Exitcode $($p.ExitCode)" }
}
# Passwort und "nur localhost" bei jeder Vorbereitung neu setzen (Registry des Dienstes).
$key = 'HKLM:\SOFTWARE\TightVNC\Server'
if (Test-Path $key) {
  & "$env:ProgramFiles\TightVNC\tvnserver.exe" -controlservice -shutdown 2>$null | Out-Null
  Set-ItemProperty -Path $key -Name LoopbackOnly -Value 1 -Type DWord
  Set-ItemProperty -Path $key -Name AllowLoopback -Value 1 -Type DWord
  Set-ItemProperty -Path $key -Name AcceptHttpConnections -Value 0 -Type DWord
  Set-ItemProperty -Path $key -Name UseVncAuthentication -Value 1 -Type DWord
  Set-ItemProperty -Path $key -Name Password -Value ([byte[]](__PWBYTES__)) -Type Binary
}
Restart-Service tvnserver -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
if ((Get-Service tvnserver).Status -ne 'Running') { throw 'Der VNC-Dienst läuft nicht.' }
$t = Test-NetConnection -ComputerName 127.0.0.1 -Port 5900 -WarningAction SilentlyContinue
if (-not $t.TcpTestSucceeded) { throw 'Port 5900 antwortet nicht.' }
'LIVE_READY'
"""


def vnc_password(agent_uid: str) -> str:
    """VNC-Passwort je Rechner, abgeleitet vom Server-Geheimnis (nirgends gespeichert)."""
    import hashlib
    import hmac
    from .config import settings
    digest = hmac.new(settings.jwt_secret.encode(), f"vnc:{agent_uid}".encode(), hashlib.sha256).hexdigest()
    return digest[:8]                            # VNC-Authentifizierung kennt nur 8 Zeichen


def vnc_obfuscate(password: str) -> bytes:
    """So legt TightVNC das Passwort in der Registry ab: DES mit festem Schluessel."""
    from cryptography.hazmat.primitives.ciphers import Cipher, modes
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:                                             # aeltere cryptography
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    # d3des aus VNC dreht die Bits jedes Schluesselbytes um: {23,82,107,6,35,78,88,7}
    # entspricht fuer Standard-DES dem Schluessel e84ad660c4721ae0.
    key = bytes.fromhex("e84ad660c4721ae0")
    data = password.encode()[:8].ljust(8, b"\0")
    enc = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def live_setup_script(agent_uid: str) -> str:
    pw = vnc_password(agent_uid)
    return (LIVE_SETUP_PS.replace("__PW__", pw)
            .replace("__PWBYTES__", ",".join(str(b) for b in vnc_obfuscate(pw))))
