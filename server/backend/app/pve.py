# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Proxmox VE: Snapshot bzw. Sicherung vor dem Patchen
# ==============================================================================
"""
Proxmox VE als Sicherheitsnetz fuers Patchen.

Vor einem Patch-Lauf legt RIMPERIO auf dem Proxmox-Host einen Snapshot der VM
bzw. des Containers an (oder, wenn gewuenscht, eine vzdump-Sicherung). Laeuft
nach dem Update alles, wird der Snapshot nach der eingestellten Frist (Standard
3 Tage) wieder geloescht. Laeuft etwas nicht, bleibt er stehen - und ein Klick
rollt zurueck.

**Anmeldung.** Am besten mit einem API-Token (Rechenzentrum → Berechtigungen →
API-Token), Rechte: VM.Snapshot, VM.Snapshot.Rollback, VM.Audit, VM.PowerMgmt, VM.Backup,
Datastore.AllocateSpace/Audit (nur fuer vzdump), Sys.Audit. Benutzername und
Passwort gehen auch (Ticket-Anmeldung), aber nicht mit Zwei-Faktor.

**Zuordnung.** Welche VM zu welchem Rechner gehoert, wird ueber die
MAC-Adressen (aus der Agent-Inventur) und den Namen erkannt; eine Zuordnung von
Hand geht immer vor.

Geheimnisse (Token, Passwort) werden nie ausgeliefert.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Agent, PveHost, PveLink

logger = logging.getLogger("rimperio.pve")

_MAC = re.compile(r"(?i)\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b")
_SNAP_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class PveError(Exception):
    """Proxmox hat abgelehnt oder ist nicht erreichbar."""


def public_host(h: PveHost) -> dict:
    return {"id": h.id, "name": h.name, "url": h.url, "auth_type": h.auth_type,
            "token_id": h.token_id, "username": h.username, "secret_set": bool(h.secret),
            "verify_tls": h.verify_tls, "enabled": h.enabled, "default_storage": h.default_storage,
            "last_ok_at": _iso(h.last_ok_at), "last_error": h.last_error,
            "guests": h.guests or [], "guests_at": _iso(h.guests_at)}


def public_link(link: PveLink) -> dict:
    return {"agent_id": link.agent_id, "host_id": link.host_id, "node": link.node,
            "vmid": link.vmid, "guest_type": link.guest_type, "guest_name": link.guest_name,
            "matched_by": link.matched_by}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def snapshot_name(prefix: str = "rimperio") -> str:
    """Proxmox will: mit Buchstabe beginnen, nur [A-Za-z0-9_-], hoechstens 40 Zeichen."""
    name = f"{prefix}_{datetime.utcnow():%Y%m%d_%H%M%S}"
    name = _SNAP_SAFE.sub("_", name)
    if not name[:1].isalpha():
        name = "r" + name
    return name[:40]


class PveClient:
    """Eine Verbindung zu einem Proxmox-Host. Mit `async with` benutzen."""

    def __init__(self, host: PveHost, timeout: float = 30.0) -> None:
        self.host = host
        self.base = host.url.rstrip("/")
        if not self.base.endswith("/api2/json"):
            self.base += "/api2/json"
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._csrf = ""

    async def __aenter__(self) -> "PveClient":
        headers = {"Accept": "application/json"}
        if self.host.auth_type == "token":
            if not self.host.token_id or not self.host.secret:
                raise PveError("Für den API-Token fehlen Token-ID oder Geheimnis.")
            headers["Authorization"] = f"PVEAPIToken={self.host.token_id}={self.host.secret}"
        self._client = httpx.AsyncClient(base_url=self.base, headers=headers,
                                         verify=bool(self.host.verify_tls), timeout=self.timeout)
        if self.host.auth_type == "password":
            if not self.host.username or not self.host.secret:
                await self._client.aclose()
                raise PveError("Für die Anmeldung fehlen Benutzer oder Passwort.")
            r = await self._client.post("/access/ticket", data={
                "username": self.host.username, "password": self.host.secret})
            data = self._unwrap(r, "Anmeldung")
            if not data or "ticket" not in data:
                await self._client.aclose()
                raise PveError("Proxmox hat kein Ticket ausgestellt (Zwei-Faktor aktiv? Dann API-Token nutzen).")
            self._client.cookies.set("PVEAuthCookie", data["ticket"])
            self._csrf = data.get("CSRFPreventionToken", "")
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    @staticmethod
    def _unwrap(r: httpx.Response, what: str):
        if r.status_code >= 400:
            detail = r.reason_phrase or ""
            try:
                body = r.json()
                errs = body.get("errors")
                if errs:
                    detail = "; ".join(f"{k}: {v}" for k, v in errs.items())
                elif body.get("message"):
                    detail = body["message"]
            except ValueError:
                detail = detail or r.text[:300]
            if r.status_code == 401:
                detail += " — Anmeldung abgelehnt (Token/Passwort prüfen)"
            elif r.status_code == 403:
                detail += " — fehlende Berechtigung"
            raise PveError(f"{what}: HTTP {r.status_code} {detail}".strip())
        try:
            return r.json().get("data")
        except ValueError as exc:
            raise PveError(f"{what}: unlesbare Antwort") from exc

    async def get(self, path: str, **params):
        assert self._client
        try:
            return self._unwrap(await self._client.get(path, params=params or None), path)
        except httpx.HTTPError as exc:
            raise PveError(f"Proxmox nicht erreichbar: {exc}") from exc

    async def _write(self, method: str, path: str, data: Optional[dict] = None):
        assert self._client
        headers = {"CSRFPreventionToken": self._csrf} if self._csrf else None
        try:
            r = await self._client.request(method, path, data=data or None, headers=headers)
        except httpx.HTTPError as exc:
            raise PveError(f"Proxmox nicht erreichbar: {exc}") from exc
        return self._unwrap(r, path)

    async def post(self, path: str, data: Optional[dict] = None):
        return await self._write("POST", path, data)

    async def delete(self, path: str):
        return await self._write("DELETE", path)


# =============================================================================
# Abfragen
# =============================================================================
async def test(host: PveHost) -> dict:
    async with PveClient(host, timeout=15.0) as pve:
        version = await pve.get("/version")
        nodes = await pve.get("/nodes")
    return {"version": (version or {}).get("version", ""),
            "release": (version or {}).get("release", ""),
            "nodes": [n.get("node") for n in nodes or []]}


async def list_guests(host: PveHost) -> List[dict]:
    """Alle VMs und Container samt MAC-Adressen."""
    async with PveClient(host) as pve:
        resources = await pve.get("/cluster/resources", type="vm") or []
        gate = asyncio.Semaphore(8)

        async def detail(res: dict) -> dict:
            guest = {"vmid": int(res.get("vmid", 0)), "name": res.get("name", ""),
                     "node": res.get("node", ""), "type": res.get("type", "qemu"),
                     "status": res.get("status", ""), "template": bool(res.get("template")),
                     "macs": []}
            async with gate:
                try:
                    cfg = await pve.get(f"/nodes/{guest['node']}/{guest['type']}/{guest['vmid']}/config") or {}
                    for key, value in cfg.items():
                        if key.startswith("net"):
                            guest["macs"] += [m.lower() for m in _MAC.findall(str(value))]
                except PveError as exc:
                    guest["error"] = str(exc)
            return guest

        guests = await asyncio.gather(*(detail(r) for r in resources[:500]))
    return sorted((g for g in guests if not g["template"]), key=lambda g: (g["node"], g["vmid"]))


async def refresh_guests(db: AsyncSession, host: PveHost) -> List[dict]:
    try:
        guests = await list_guests(host)
    except PveError as exc:
        host.last_error = str(exc)
        await db.commit()
        raise
    host.guests, host.guests_at = guests, datetime.utcnow()
    host.last_ok_at, host.last_error = datetime.utcnow(), None
    await db.commit()
    return guests


def _short(name: str) -> str:
    return (name or "").split(".")[0].strip().lower()


async def automatch(db: AsyncSession) -> int:
    """Ordnet Rechner ohne Zuordnung ihrer VM zu: erst ueber MAC, dann ueber den Namen."""
    hosts = (await db.execute(select(PveHost).where(PveHost.enabled.is_(True)))).scalars().all()
    if not hosts:
        return 0
    linked = {l.agent_id for l in (await db.execute(select(PveLink))).scalars()}
    agents = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
    by_mac: Dict[str, tuple] = {}
    by_name: Dict[str, list] = {}
    for h in hosts:
        for g in h.guests or []:
            for mac in g.get("macs") or []:
                by_mac[mac.lower()] = (h, g)
            by_name.setdefault(_short(g.get("name", "")), []).append((h, g))
    made = 0
    for a in agents:
        if a.id in linked:
            continue
        hit, how = None, ""
        macs = [str(m).lower() for m in ((a.inventory or {}).get("macs") or [])]
        if a.mac_address:
            macs.append(a.mac_address.lower())
        for mac in macs:
            if mac in by_mac:
                hit, how = by_mac[mac], "mac"
                break
        if hit is None:
            candidates = by_name.get(_short(a.hostname), [])
            if len(candidates) == 1:
                hit, how = candidates[0], "name"
        if hit is None:
            continue
        h, g = hit
        db.add(PveLink(agent_id=a.id, host_id=h.id, node=g["node"], vmid=int(g["vmid"]),
                       guest_type=g.get("type", "qemu"), guest_name=g.get("name", ""), matched_by=how))
        made += 1
    if made:
        await db.commit()
    return made


async def link_for(db: AsyncSession, agent_id: int) -> Optional[tuple]:
    link = (await db.execute(select(PveLink).where(PveLink.agent_id == agent_id))).scalar_one_or_none()
    if link is None:
        return None
    host = await db.get(PveHost, link.host_id)
    if host is None or not host.enabled:
        return None
    return host, link


# =============================================================================
# Snapshot, Sicherung, Zurueckrollen
# =============================================================================
def _guest_path(link: PveLink) -> str:
    return f"/nodes/{link.node}/{link.guest_type}/{link.vmid}"


async def create_snapshot(host: PveHost, link: PveLink, name: str, description: str,
                          include_ram: bool = False) -> str:
    data = {"snapname": name, "description": description[:500]}
    if include_ram and link.guest_type == "qemu":
        data["vmstate"] = "1"
    async with PveClient(host) as pve:
        return str(await pve.post(f"{_guest_path(link)}/snapshot", data) or "")


async def delete_snapshot(host: PveHost, link_or_ref: dict, name: str) -> str:
    path = f"/nodes/{link_or_ref['node']}/{link_or_ref['guest_type']}/{link_or_ref['vmid']}/snapshot/{quote(name)}"
    async with PveClient(host) as pve:
        return str(await pve.delete(path) or "")


async def rollback_snapshot(host: PveHost, ref: dict, name: str) -> str:
    base = f"/nodes/{ref['node']}/{ref['guest_type']}/{ref['vmid']}"
    async with PveClient(host) as pve:
        return str(await pve.post(f"{base}/snapshot/{quote(name)}/rollback") or "")


async def start_guest(host: PveHost, ref: dict) -> str:
    base = f"/nodes/{ref['node']}/{ref['guest_type']}/{ref['vmid']}"
    async with PveClient(host) as pve:
        status = await pve.get(f"{base}/status/current") or {}
        if status.get("status") == "running":
            return ""
        return str(await pve.post(f"{base}/status/start") or "")


async def list_snapshots(host: PveHost, ref: dict) -> List[dict]:
    base = f"/nodes/{ref['node']}/{ref['guest_type']}/{ref['vmid']}"
    async with PveClient(host) as pve:
        return [s for s in (await pve.get(f"{base}/snapshot") or []) if s.get("name") != "current"]


async def vzdump(host: PveHost, link: PveLink, storage: str, note: str) -> str:
    data = {"vmid": str(link.vmid), "mode": "snapshot", "compress": "zstd",
            "notes-template": note[:200]}
    if storage:
        data["storage"] = storage
    async with PveClient(host) as pve:
        return str(await pve.post(f"/nodes/{link.node}/vzdump", data) or "")


async def find_backup(host: PveHost, ref: dict, storage: str, since: datetime) -> Optional[str]:
    """Die vzdump-Datei, die nach `since` fuer diese VM entstanden ist."""
    async with PveClient(host) as pve:
        items = await pve.get(f"/nodes/{ref['node']}/storage/{storage}/content",
                              content="backup", vmid=ref["vmid"]) or []
    cutoff = since.timestamp() - 60
    fresh = [i for i in items if float(i.get("ctime", 0)) >= cutoff]
    fresh.sort(key=lambda i: float(i.get("ctime", 0)), reverse=True)
    return fresh[0]["volid"] if fresh else None


async def delete_volume(host: PveHost, node: str, storage: str, volid: str) -> str:
    async with PveClient(host) as pve:
        return str(await pve.delete(f"/nodes/{node}/storage/{storage}/content/{quote(volid, safe='')}") or "")


async def task_status(host: PveHost, node: str, upid: str) -> dict:
    """{'done': bool, 'ok': bool, 'exitstatus': str}"""
    if not upid:
        return {"done": True, "ok": True, "exitstatus": "OK"}
    async with PveClient(host, timeout=15.0) as pve:
        st = await pve.get(f"/nodes/{node}/tasks/{quote(upid, safe='')}/status") or {}
    done = st.get("status") == "stopped"
    exitstatus = str(st.get("exitstatus") or "")
    return {"done": done, "ok": done and exitstatus == "OK", "exitstatus": exitstatus}


def node_of_upid(upid: str, fallback: str) -> str:
    """UPID:<node>:… — der Task laeuft auf dem Knoten, der ihn angenommen hat."""
    parts = (upid or "").split(":")
    return parts[1] if len(parts) > 2 and parts[0] == "UPID" else fallback
