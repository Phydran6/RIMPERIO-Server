# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Proxmox-Konsolen (termproxy): Shell auf Knoten, LXC und
#               VM (serielle Konsole), Updates des Knotens, Befehle per pct exec
# ==============================================================================
"""
Proxmox bietet seine Konsolen ueber `termproxy` an: ein POST liefert Port und
Ticket, danach spricht man ueber einen WebSocket (`vncwebsocket`) mit dem
Terminal. So macht es auch die Proxmox-Oberflaeche ("Shell", "Konsole",
"Upgrade").

Damit kann RIMPERIO, ohne Agent und ohne SSH:

* eine **Shell auf dem Knoten** oeffnen (root@pam) - auch fuer `pct exec`,
* die **Konsole eines LXC-Containers** oeffnen (Proxmox-Einstellung "cmode":
  tty = Anmeldeaufforderung, shell = direkt root),
* die **serielle Konsole einer VM** (nur mit serial0 in der VM-Konfiguration),
* den **Knoten aktualisieren** (`cmd=upgrade` = `pveupgrade`), mit
  automatischer Bestaetigung und mitgeschnittener Ausgabe,
* einen **Befehl in einem Container ausfuehren** (`pct exec` ueber die
  Knoten-Shell) - z. B. den Agent installieren.

Leitung (Proxmox-Protokoll):
    nach dem Verbinden:   "<user>:<ticket>\\n"   -> Proxmox antwortet "OK"
    Eingabe:              "0:<Bytes>:<Daten>"
    Groesse:              "1:<Spalten>:<Zeilen>:"
    Lebenszeichen:        "2"

Rechte: Shell und Upgrade auf dem Knoten verlangen in Proxmox root@pam (bzw.
Sys.Console/Sys.Modify). Mit einem eingeschraenkten API-Token antwortet
Proxmox mit 403 - die Meldung sagt dann genau das.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import quote

from . import pve
from .models import PveHost

logger = logging.getLogger("rimperio.pveterm")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "").replace("\r", "")


def _path(kind: str, node: str, vmid: Optional[int]) -> str:
    if kind == "node":
        return f"/nodes/{node}"
    if kind == "lxc":
        return f"/nodes/{node}/lxc/{int(vmid or 0)}"
    if kind == "qemu":
        return f"/nodes/{node}/qemu/{int(vmid or 0)}"
    raise pve.PveError(f"Unbekannte Konsole '{kind}'.")


class Terminal:
    """Eine offene Proxmox-Konsole."""

    def __init__(self, ws, user: str) -> None:
        self.ws = ws
        self.user = user
        self.closed = False

    async def send(self, data: str) -> None:
        raw = data.encode("utf-8")
        await self.ws.send(f"0:{len(raw)}:" + data)

    async def resize(self, cols: int, rows: int) -> None:
        await self.ws.send(f"1:{int(cols)}:{int(rows)}:")

    async def ping(self) -> None:
        await self.ws.send("2")

    async def recv(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            msg = await asyncio.wait_for(self.ws.recv(), timeout) if timeout else await self.ws.recv()
        except asyncio.TimeoutError:
            return ""
        except Exception:
            self.closed = True
            return None
        if isinstance(msg, (bytes, bytearray)):
            return bytes(msg).decode("utf-8", errors="replace")
        return msg


@asynccontextmanager
async def open_terminal(host: PveHost, kind: str, node: str, vmid: Optional[int] = None,
                        cmd: Optional[str] = None, cols: int = 120, rows: int = 32) -> AsyncIterator[Terminal]:
    """Oeffnet eine Konsole. `cmd`: nur beim Knoten - 'upgrade' oder 'login'."""
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:                                      # defensiv
        raise pve.PveError("Das Paket 'websockets' fehlt im Backend.") from exc

    base = pve.normalize_url(host.url)
    path = _path(kind, node, vmid)
    async with pve.PveClient(host, timeout=30.0) as client:
        data: Dict[str, Any] = {}
        if kind == "node" and cmd:
            data["cmd"] = cmd
        try:
            info = await client.post(f"{path}/termproxy", data or None)
        except pve.PveError as exc:
            if "403" in str(exc) or "Permission" in str(exc):
                raise pve.PveError("Proxmox verweigert die Konsole – dafür braucht es root@pam bzw. die "
                                   "Rechte Sys.Console (Knoten) / VM.Console (Gast).") from exc
            raise
        if not info or not info.get("port"):
            raise pve.PveError("Proxmox hat keine Konsole bereitgestellt.")
        headers = {}
        if host.auth_type == "token":
            headers["Authorization"] = f"PVEAPIToken={host.token_id}={host.secret}"
        else:
            cookie = client._client.cookies.get("PVEAuthCookie") if client._client else None
            if cookie:
                headers["Cookie"] = f"PVEAuthCookie={quote(cookie, safe='')}"
        ws_url = (base.replace("https://", "wss://").replace("http://", "ws://") + "/api2/json"
                  + f"{path}/vncwebsocket?port={info['port']}&vncticket={quote(info['ticket'], safe='')}")
        ctx = None
        if ws_url.startswith("wss://"):
            ctx = ssl.create_default_context()
            if not host.verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        try:
            ws = await connect(ws_url, additional_headers=headers, ssl=ctx, open_timeout=20,
                               max_size=None, ping_interval=None)
        except Exception as exc:
            raise pve.PveError(f"Konsole nicht erreichbar: {exc}") from exc
        user = info.get("user") or host.username or (host.token_id or "").split("!")[0]
        try:
            await ws.send(f"{user}:{info['ticket']}\n")
            first = await asyncio.wait_for(ws.recv(), 15)
            first = first.decode("utf-8", "replace") if isinstance(first, (bytes, bytearray)) else first
            if not first.startswith("OK"):
                raise pve.PveError(f"Konsole abgelehnt: {first[:200]}")
            term = Terminal(ws, user)
            await term.resize(cols, rows)
            rest = first[2:]
            term.pending = rest  # type: ignore[attr-defined]
            yield term
        finally:
            try:
                await ws.close()
            except Exception:
                pass


# =============================================================================
# Befehl ueber die Knoten-Shell ausfuehren (z. B. pct exec)
# =============================================================================
async def run_on_node(host: PveHost, node: str, command: str, timeout: int = 900) -> dict:
    """Fuehrt einen Befehl in der Knoten-Shell aus und liefert Ausgabe + Rueckgabewert.

    Die Ausgabe wird zwischen zwei Markern eingefangen; so stoert weder der
    Begruessungstext noch das Echo der Eingabe.
    """
    tag = uuid.uuid4().hex[:10]
    begin, end = f"__RIM_BEGIN_{tag}__", f"__RIM_END_{tag}__"
    script = (f"stty -echo 2>/dev/null; echo {begin}; ( {command} ) 2>&1; "
              f"echo {end}:$?; exit\r")
    out = ""
    async with open_terminal(host, "node", node, cmd="login", cols=200, rows=50) as term:
        await asyncio.sleep(0.5)
        await term.send(script)
        deadline = time.monotonic() + timeout
        out = getattr(term, "pending", "")
        while time.monotonic() < deadline:
            chunk = await term.recv(timeout=10)
            if chunk is None:
                break
            out += chunk
            if re.search(re.escape(end) + r":\d+", strip_ansi(out)):
                break
    clean = strip_ansi(out)
    m_end = re.search(re.escape(end) + r":(\d+)", clean)
    # Das Echo der Eingabe enthaelt den Marker ebenfalls - die letzte Fundstelle zaehlt.
    starts = [m.end() for m in re.finditer(re.escape(begin) + r"\n", clean)]
    body = clean[starts[-1]:m_end.start()] if starts and m_end else clean[-8000:]
    return {"ok": bool(m_end) and m_end.group(1) == "0", "exit": int(m_end.group(1)) if m_end else None,
            "output": body.strip()[-20000:], "finished": bool(m_end)}


def pct_exec_command(vmid: int, command: str) -> str:
    """`pct exec` mit sauber gequotetem Befehl."""
    quoted = "'" + command.replace("'", "'\\''") + "'"
    return f"pct exec {int(vmid)} -- sh -c {quoted}"


# =============================================================================
# Updates des Knotens (apt ueber die API, Upgrade ueber die Konsole)
# =============================================================================
async def node_updates(host: PveHost, node: str) -> dict:
    async with pve.PveClient(host, timeout=30.0) as client:
        pkgs = await client.get(f"/nodes/{node}/apt/update") or []
        try:
            versions = await client.get(f"/nodes/{node}/apt/versions") or []
        except pve.PveError:
            versions = []
        status = await client.get(f"/nodes/{node}/status") or {}
    items = [{"package": p.get("Package"), "title": p.get("Title") or p.get("Description", "")[:120],
              "from": p.get("OldVersion") or "", "to": p.get("Version") or "",
              "priority": p.get("Priority") or "", "section": p.get("Section") or "",
              "origin": p.get("Origin") or "",
              "kernel": bool(re.match(r"^(pve-kernel|proxmox-kernel|linux-image)", p.get("Package") or "")),
              "security": "security" in (p.get("Origin") or "").lower() or "-security" in (p.get("ChangeLogUrl") or "")}
             for p in pkgs]
    manager = next((v for v in versions if v.get("Package") == "pve-manager"), {})
    return {"node": node, "count": len(items), "items": items,
            "kernel_update": any(i["kernel"] for i in items),
            "pve_version": manager.get("OldVersion") or manager.get("CurrentState") or "",
            "uptime": status.get("uptime"), "kversion": status.get("kversion", "")}


async def refresh_node_updates(host: PveHost, node: str) -> str:
    async with pve.PveClient(host, timeout=30.0) as client:
        return await client.post(f"/nodes/{node}/apt/update", {"notify": 0})


JOBS: Dict[str, dict] = {}
MAX_OUTPUT = 400_000


def public_job(job: dict, with_output: bool = True) -> dict:
    out = {k: v for k, v in job.items() if k != "output"}
    if with_output:
        out["output"] = strip_ansi(job.get("output", ""))[-60_000:]
    return out


def jobs_for(host_id: Optional[int] = None) -> List[dict]:
    return [public_job(j, False) for j in sorted(JOBS.values(), key=lambda j: j["started_at"], reverse=True)
            if host_id is None or j["host_id"] == host_id][:30]


# Antworten auf die Fragen von apt/dpkg waehrend des Upgrades. Konfigurations-
# dateien: die eigene behalten (N) - das ist auch die Vorgabe von dpkg.
_PROMPTS = [
    (re.compile(r"Do you want to continue\? \[Y/n\]"), "y\r"),
    (re.compile(r"\(Y/I/N/O/D/Z\) \[default=N\] \?"), "N\r"),
    (re.compile(r"\[y/N\]\s*$"), "N\r"),
]


async def start_node_upgrade(host: PveHost, node: str, user: str) -> dict:
    if any(j["host_id"] == host.id and j["node"] == node and j["status"] == "running" for j in JOBS.values()):
        raise pve.PveError(f"Auf {node} läuft bereits ein Upgrade.")
    job = {"id": uuid.uuid4().hex[:12], "host_id": host.id, "host": host.name, "node": node,
           "user": user, "status": "running", "started_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
           "finished_at": None, "reboot_recommended": False, "error": None, "output": ""}
    JOBS[job["id"]] = job
    # Detached ORM-Objekt reicht: open_terminal liest nur Felder.
    asyncio.create_task(_run_upgrade(host, node, job))
    return public_job(job, False)


async def _run_upgrade(host: PveHost, node: str, job: dict) -> None:
    from . import journal, notify
    from .database import async_session
    answered: Dict[int, int] = {}
    try:
        async with open_terminal(host, "node", node, cmd="upgrade", cols=160, rows=40) as term:
            job["output"] += getattr(term, "pending", "")
            last_ping = time.monotonic()
            deadline = time.monotonic() + 3 * 3600
            shell_seen = False
            while time.monotonic() < deadline:
                chunk = await term.recv(timeout=15)
                if chunk is None:
                    break
                if chunk:
                    job["output"] = (job["output"] + chunk)[-MAX_OUTPUT:]
                tail = strip_ansi(job["output"][-2000:])
                for n, (pattern, answer) in enumerate(_PROMPTS):
                    hits = len(pattern.findall(strip_ansi(job["output"])))
                    if hits > answered.get(n, 0):
                        answered[n] = hits
                        await term.send(answer)
                if "consider rebooting" in tail or "reboot" in tail.lower() and "kernel" in tail.lower():
                    job["reboot_recommended"] = True
                # pveupgrade startet am Ende eine Shell ("starting shell") - die
                # brauchen wir nicht.
                if not shell_seen and "starting shell" in tail.lower():
                    shell_seen = True
                    await asyncio.sleep(1)
                    await term.send("exit\r")
                if time.monotonic() - last_ping > 25:
                    await term.ping()
                    last_ping = time.monotonic()
        text = strip_ansi(job["output"])
        failed = bool(re.search(r"\bE: |dpkg: error|Errors were encountered|command '.*' failed", text))
        job["status"] = "failed" if failed else "succeeded"
        if "Your System is up-to-date" in text:
            job["status"] = "succeeded"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:500]
        logger.warning("Upgrade %s/%s: %s", host.name, node, exc)
    job["finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    ok = job["status"] == "succeeded"
    journal.fire(category="update", event="pve.node.upgrade." + ("succeeded" if ok else "failed"),
                 level="info" if ok else "error", actor=job["user"], target=f"{host.name}/{node}", ok=ok,
                 message=f"Proxmox-Knoten {node} ({host.name}) aktualisiert" if ok else
                 f"Upgrade von {node} ({host.name}) fehlgeschlagen: {job.get('error') or 'siehe Ausgabe'}",
                 detail={"job": job["id"], "reboot_recommended": job["reboot_recommended"]})
    try:
        async with async_session() as db:
            await notify.push(db, category="patch", level="info" if ok else "critical",
                              title=f"Proxmox {node}: Upgrade {'abgeschlossen' if ok else 'fehlgeschlagen'}",
                              body=("Neustart empfohlen (neuer Kernel)." if job["reboot_recommended"] else "")
                              or (job.get("error") or ""),
                              link="/devices/proxmox/patching", data={"pve_job": job["id"], "node": node},
                              tenant_id=getattr(host, "tenant_id", None),
                              event="patch_done" if ok else "patch_failed")
    except Exception as exc:                                        # defensiv
        logger.debug("Meldung zum Upgrade nicht angelegt: %s", exc)


async def reboot_node(host: PveHost, node: str) -> str:
    async with pve.PveClient(host, timeout=30.0) as client:
        return await client.post(f"/nodes/{node}/status", {"command": "reboot"})
