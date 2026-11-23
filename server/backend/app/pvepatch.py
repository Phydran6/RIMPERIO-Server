# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Proxmox-Knoten patchen: rollierend Knoten fuer Knoten,
#               von Hand oder im Wartungsfenster, mit Neustart nach Kernel-Update
# ==============================================================================
"""
Patchen der Proxmox-Knoten selbst (nicht der Gaeste - die patcht der Agent).

Ein **Patch-Lauf** geht einen Host (Einzelknoten oder Cluster) Knoten fuer
Knoten durch, nie zwei gleichzeitig:

    1. Paketlisten auffrischen (apt update ueber die API, auf Wunsch)
    2. verfuegbare Pakete lesen - nichts da: weiter zum naechsten Knoten
    3. pveupgrade ueber die Knoten-Konsole (pveterm.start_node_upgrade)
    4. Neustart je nach Einstellung (nie / bei neuem Kernel / immer) und
       warten, bis der Knoten wieder online ist
    5. erst dann der naechste Knoten

Schlaegt ein Schritt fehl, haelt der Lauf an - ein Cluster soll nicht mit
mehreren halb aktualisierten Knoten dastehen.

**Zeitplan je Host** (Einstellung `pve_patching`): Wochentage, Beginn,
Dauer des Fensters, Zeitzone. Ein Lauf startet einmal je Fenster; nach dem
Ende des Fensters wird kein weiterer Knoten mehr angefangen.

Laeufe liegen im Speicher (mit Ausgabe ueber pveterm.JOBS) und als kurzer
Verlauf ohne Ausgabe in der Einstellung `pve_patch_history`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from . import journal, pve, pveterm
from .models import PveHost, Setting

logger = logging.getLogger("rimperio.pvepatch")

CONFIG_KEY = "pve_patching"
HISTORY_KEY = "pve_patch_history"
HISTORY_MAX = 50
LOOP_SECONDS = 60
LINK = "/devices/proxmox/patching"

REBOOT_MODES = ("never", "kernel", "always")

DEFAULT_HOST_CONFIG: Dict[str, Any] = {
    "enabled": False,                 # Zeitplan aktiv?
    "days": [5],                      # 0 = Montag … 6 = Sonntag (Standard: Samstag)
    "start": "03:00",
    "duration_minutes": 240,
    "timezone": "Europe/Berlin",
    "refresh": True,                  # vorher apt update
    "only_security": False,           # nur laufen, wenn Sicherheitsupdates dabei sind
    "reboot": "kernel",               # never | kernel | always (nach einem Upgrade)
    "reboot_timeout_minutes": 20,
    "nodes": [],                      # leer = alle Knoten; sonst nur diese
}

# Laeufe im Speicher: id -> Lauf
RUNS: Dict[str, dict] = {}


# -----------------------------------------------------------------------------
# Einstellungen
# -----------------------------------------------------------------------------
def _hhmm(value: str) -> tuple:
    parts = str(value or "").split(":")
    if len(parts) != 2:
        raise ValueError(f"Uhrzeit '{value}' bitte als HH:MM angeben.")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"Uhrzeit '{value}' ist ungültig.")
    return h, m


def clean_host_config(raw: Optional[dict]) -> dict:
    """Prueft und ergaenzt die Einstellung eines Hosts."""
    cfg = dict(DEFAULT_HOST_CONFIG)
    cfg.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_HOST_CONFIG})
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["refresh"] = bool(cfg["refresh"])
    cfg["only_security"] = bool(cfg["only_security"])
    days = sorted({int(d) for d in (cfg["days"] or [])})
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("Wochentage: 0 (Montag) bis 6 (Sonntag).")
    if cfg["enabled"] and not days:
        raise ValueError("Für den Zeitplan mindestens einen Wochentag wählen.")
    cfg["days"] = days
    h, m = _hhmm(cfg["start"])
    cfg["start"] = f"{h:02d}:{m:02d}"
    cfg["duration_minutes"] = max(30, min(int(cfg["duration_minutes"]), 24 * 60))
    if cfg["reboot"] not in REBOOT_MODES:
        raise ValueError(f"Neustart: {', '.join(REBOOT_MODES)}.")
    cfg["reboot_timeout_minutes"] = max(5, min(int(cfg["reboot_timeout_minutes"]), 120))
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(cfg["timezone"])
    except Exception as exc:
        raise ValueError(f"Unbekannte Zeitzone '{cfg['timezone']}'.") from exc
    cfg["nodes"] = [str(n).strip() for n in (cfg["nodes"] or []) if str(n).strip()][:64]
    return cfg


async def _read(key: str, default):
    from .database import async_session
    async with async_session() as db:
        row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        return row.value if row and row.value is not None else default


async def _write(key: str, value, description: str) -> None:
    from .database import async_session
    async with async_session() as db:
        row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value, description=description))
        await db.commit()


async def load_config() -> dict:
    """{"hosts": {"<id>": {...}}, "last": {"<id>": "<ISO des letzten Fensters>"}}"""
    raw = await _read(CONFIG_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    hosts = {}
    for key, value in (raw.get("hosts") or {}).items():
        try:
            hosts[str(key)] = clean_host_config(value)
        except (ValueError, TypeError):
            hosts[str(key)] = dict(DEFAULT_HOST_CONFIG)
    return {"hosts": hosts, "last": dict(raw.get("last") or {})}


async def host_config(host_id: int) -> dict:
    return (await load_config())["hosts"].get(str(host_id)) or dict(DEFAULT_HOST_CONFIG)


async def save_host_config(host_id: int, raw: dict) -> dict:
    cfg = clean_host_config(raw)
    data = await load_config()
    data["hosts"][str(host_id)] = cfg
    await _write(CONFIG_KEY, data, "Patchen der Proxmox-Knoten: Zeitplan je Host")
    return cfg


async def _mark_window(host_id: int, begin_iso: str) -> None:
    data = await load_config()
    data["last"][str(host_id)] = begin_iso
    await _write(CONFIG_KEY, data, "Patchen der Proxmox-Knoten: Zeitplan je Host")


async def history() -> List[dict]:
    rows = await _read(HISTORY_KEY, [])
    return rows if isinstance(rows, list) else []


async def _remember(run: dict) -> None:
    rows = [r for r in await history() if r.get("id") != run["id"]]
    rows.insert(0, public_run(run))
    await _write(HISTORY_KEY, rows[:HISTORY_MAX], "Verlauf: Patch-Läufe der Proxmox-Knoten")


# -----------------------------------------------------------------------------
# Zeitfenster
# -----------------------------------------------------------------------------
def _tz(cfg: dict):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(cfg.get("timezone") or "Europe/Berlin")
    except Exception:
        return ZoneInfo("UTC")


def _to_local(cfg: dict, utc: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    return utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(_tz(cfg)).replace(tzinfo=None)


def _to_utc(cfg: dict, local: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    return local.replace(tzinfo=_tz(cfg)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def window_state(cfg: dict, now: Optional[datetime] = None) -> dict:
    """{"open": bool, "begin": UTC, "end": UTC, "next": UTC} fuer den Zeitplan."""
    if not cfg.get("enabled"):
        return {"open": False, "begin": None, "end": None, "next": None}
    now = now or datetime.utcnow()
    loc = _to_local(cfg, now)
    h, m = _hhmm(cfg["start"])
    dur = timedelta(minutes=int(cfg["duration_minutes"]))
    days = set(cfg["days"])
    for back in (1, 0):
        day = loc.date() - timedelta(days=back)
        begin = datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m)
        if day.weekday() in days and begin <= loc < begin + dur:
            return {"open": True, "begin": _to_utc(cfg, begin), "end": _to_utc(cfg, begin + dur), "next": None}
    for ahead in range(0, 8):
        day = loc.date() + timedelta(days=ahead)
        begin = datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m)
        if day.weekday() in days and begin > loc:
            return {"open": False, "begin": None, "end": None, "next": _to_utc(cfg, begin)}
    return {"open": False, "begin": None, "end": None, "next": None}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# -----------------------------------------------------------------------------
# Ein Lauf
# -----------------------------------------------------------------------------
def public_run(run: dict) -> dict:
    return {k: v for k, v in run.items() if not k.startswith("_")}


def active_run(host_id: int) -> Optional[dict]:
    return next((r for r in RUNS.values() if r["host_id"] == host_id and r["status"] == "running"), None)


def runs_for(host_id: Optional[int] = None) -> List[dict]:
    return [public_run(r) for r in sorted(RUNS.values(), key=lambda r: r["started_at"], reverse=True)
            if host_id is None or r["host_id"] == host_id][:20]


def _log(run: dict, text: str, level: str = "info") -> None:
    run["log"].append({"ts": _iso(datetime.utcnow()), "level": level, "text": text})
    run["log"] = run["log"][-200:]
    getattr(logger, "warning" if level == "error" else "info")("Proxmox-Patchlauf %s (%s): %s",
                                                              run["id"], run["host"], text)


def start_run(host: PveHost, cfg: dict, user: str, trigger: str = "manual",
              deadline: Optional[datetime] = None, nodes: Optional[List[str]] = None) -> dict:
    """Startet einen rollierenden Lauf ueber alle (bzw. die gewaehlten) Knoten."""
    if active_run(host.id):
        raise pve.PveError(f"Auf {host.name} läuft bereits ein Patch-Lauf.")
    if any(j["host_id"] == host.id and j["status"] == "running" for j in pveterm.JOBS.values()):
        raise pve.PveError(f"Auf {host.name} läuft gerade ein Upgrade – erst danach.")
    run = {"id": uuid.uuid4().hex[:12], "host_id": host.id, "host": host.name, "user": user,
           "trigger": trigger, "status": "running", "started_at": _iso(datetime.utcnow()),
           "finished_at": None, "deadline": _iso(deadline), "reboot": cfg["reboot"],
           "refresh": cfg["refresh"], "only_security": cfg["only_security"],
           "nodes": [], "log": [], "error": None,
           "_wanted": list(nodes if nodes is not None else cfg.get("nodes") or []),
           "_deadline": deadline, "_timeout": int(cfg["reboot_timeout_minutes"])}
    RUNS[run["id"]] = run
    # Alte, fertige Laeufe aus dem Speicher werfen.
    for rid in [r["id"] for r in sorted(RUNS.values(), key=lambda r: r["started_at"])[:-30]]:
        if RUNS[rid]["status"] != "running":
            RUNS.pop(rid, None)
    asyncio.create_task(_execute(host, run))
    journal.fire(category="update", event="pve.patch.started", level="warning", actor=user,
                 target=host.name, message=f"Patch-Lauf der Proxmox-Knoten auf {host.name} gestartet "
                 f"({'Zeitplan' if trigger == 'schedule' else 'von Hand'}).")
    return public_run(run)


async def _wait_task(host: PveHost, node: str, upid: str, timeout: float = 300) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            st = await pve.task_status(host, pve.node_of_upid(upid, node), upid)
            if st["done"]:
                return st["ok"]
        except pve.PveError:
            pass
        await asyncio.sleep(5)
    return False


async def _node_online(host: PveHost, node: str) -> Optional[bool]:
    try:
        async with pve.PveClient(host, timeout=10.0) as client:
            rows = await client.get("/nodes") or []
    except pve.PveError:
        return None                                 # Host (bzw. dieser Knoten als Einstieg) weg
    for n in rows:
        if n.get("node") == node:
            return n.get("status") == "online"
    return None


async def _wait_reboot(host: PveHost, node: str, timeout_minutes: int) -> bool:
    """Erst offline (oder nicht erreichbar), dann wieder online."""
    end = time.monotonic() + timeout_minutes * 60
    went_down = False
    down_deadline = time.monotonic() + 300          # spaetestens nach 5 min muss er weg sein
    while time.monotonic() < end:
        await asyncio.sleep(10)
        state = await _node_online(host, node)
        if not went_down:
            if state is not True:
                went_down = True
            elif time.monotonic() > down_deadline:
                return False
            continue
        if state is True:
            await asyncio.sleep(30)                 # Dienste (pveproxy, Cluster) hochkommen lassen
            return True
    return False


async def _execute(host: PveHost, run: dict) -> None:
    from . import notify
    from .database import async_session
    try:
        async with pve.PveClient(host, timeout=20.0) as client:
            rows = await client.get("/nodes") or []
        names = sorted(n.get("node") for n in rows if n.get("node"))
        offline = {n.get("node") for n in rows if n.get("status") != "online"}
        wanted = run["_wanted"]
        if wanted:
            missing = [n for n in wanted if n not in names]
            if missing:
                _log(run, f"Unbekannte Knoten übersprungen: {', '.join(missing)}", "warning")
            names = [n for n in names if n in wanted]
        if not names:
            raise pve.PveError("Keine Knoten gefunden.")
        run["nodes"] = [{"node": n, "status": "pending", "count": 0, "job": None, "rebooted": False,
                         "note": ""} for n in names]
        for entry in run["nodes"]:
            node = entry["node"]
            if run["_deadline"] and datetime.utcnow() >= run["_deadline"]:
                entry["status"] = "skipped"
                entry["note"] = "Wartungsfenster vorbei"
                _log(run, f"{node}: Wartungsfenster vorbei – nicht mehr begonnen.", "warning")
                continue
            if node in offline:
                entry["status"] = "skipped"
                entry["note"] = "offline"
                _log(run, f"{node}: offline – übersprungen.", "warning")
                continue
            entry["status"] = "running"
            if run["refresh"]:
                _log(run, f"{node}: Paketlisten auffrischen …")
                try:
                    upid = await pveterm.refresh_node_updates(host, node)
                    if not await _wait_task(host, node, upid):
                        _log(run, f"{node}: apt update meldete einen Fehler – weiter mit dem vorhandenen Stand.",
                             "warning")
                except pve.PveError as exc:
                    _log(run, f"{node}: Paketlisten nicht aufgefrischt ({exc}) – weiter.", "warning")
            info = await pveterm.node_updates(host, node)
            entry["count"] = info["count"]
            entry["kernel_update"] = info["kernel_update"]
            if not info["count"]:
                entry["status"] = "current"
                entry["note"] = "aktuell"
                _log(run, f"{node}: aktuell.")
                continue
            elif run["only_security"] and not any(i["security"] for i in info["items"]):
                entry["status"] = "skipped"
                entry["note"] = "keine Sicherheitsupdates"
                _log(run, f"{node}: {info['count']} Paket(e), aber kein Sicherheitsupdate – übersprungen.")
                continue
            else:
                _log(run, f"{node}: {info['count']} Paket(e) werden installiert"
                     + (" (neuer Kernel)" if info["kernel_update"] else "") + " …")
                job = await pveterm.start_node_upgrade(host, node, run["user"])
                entry["job"] = job["id"]
                while pveterm.JOBS.get(job["id"], {}).get("status") == "running":
                    await asyncio.sleep(5)
                done = pveterm.JOBS.get(job["id"]) or {}
                if done.get("status") != "succeeded":
                    entry["status"] = "failed"
                    entry["note"] = done.get("error") or "Upgrade fehlgeschlagen – siehe Ausgabe"
                    raise pve.PveError(f"{node}: {entry['note']}")
                entry["reboot_recommended"] = bool(done.get("reboot_recommended") or info["kernel_update"])
                _log(run, f"{node}: Upgrade abgeschlossen.")
            need_reboot = run["reboot"] == "always" or (
                run["reboot"] == "kernel" and entry.get("reboot_recommended"))
            if need_reboot:
                if len(run["nodes"]) > 1 and not await _quorate(host):
                    entry["status"] = "failed"
                    entry["note"] = "Cluster ohne Quorum – kein Neustart"
                    raise pve.PveError(f"{node}: Cluster hat kein Quorum – Neustart abgebrochen.")
                _log(run, f"{node}: Neustart …", "warning")
                await pveterm.reboot_node(host, node)
                if not await _wait_reboot(host, node, run["_timeout"]):
                    entry["status"] = "failed"
                    entry["note"] = "nach dem Neustart nicht wieder online"
                    raise pve.PveError(f"{node}: nach {run['_timeout']} min nicht wieder online.")
                entry["rebooted"] = True
                _log(run, f"{node}: wieder online.")
            elif entry.get("reboot_recommended"):
                entry["note"] = "Neustart empfohlen"
            entry["status"] = "done" if entry["status"] == "running" else entry["status"]
        run["status"] = "succeeded"
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)[:500]
        for entry in run.get("nodes", []):
            if entry["status"] == "pending":
                entry["status"] = "skipped"
                entry["note"] = "Lauf angehalten"
        _log(run, f"Angehalten: {exc}", "error")
    run["finished_at"] = _iso(datetime.utcnow())
    ok = run["status"] == "succeeded"
    done_nodes = [e["node"] for e in run.get("nodes", []) if e["status"] == "done"]
    journal.fire(category="update", event="pve.patch." + ("succeeded" if ok else "failed"),
                 level="info" if ok else "error", actor=run["user"], target=host.name, ok=ok,
                 message=(f"Proxmox {host.name}: Patch-Lauf abgeschlossen "
                          f"({len(done_nodes)} Knoten aktualisiert)." if ok else
                          f"Proxmox {host.name}: Patch-Lauf angehalten – {run['error']}"),
                 detail={"run": run["id"], "nodes": run.get("nodes", [])})
    try:
        await _remember(run)
    except Exception as exc:                                        # defensiv
        logger.debug("Verlauf nicht gespeichert: %s", exc)
    if ok and not done_nodes and run["trigger"] == "schedule":
        return                                                      # nichts zu tun gewesen - still
    try:
        async with async_session() as db:
            await notify.push(db, category="patch", level="info" if ok else "critical",
                              title=f"Proxmox {host.name}: Patch-Lauf {'abgeschlossen' if ok else 'angehalten'}",
                              body=(f"Aktualisiert: {', '.join(done_nodes)}" if done_nodes else "Alles aktuell.")
                              if ok else (run["error"] or ""),
                              link=LINK, data={"pve_run": run["id"]},
                              event="patch_done" if ok else "patch_failed")
    except Exception as exc:                                        # defensiv
        logger.debug("Meldung zum Patch-Lauf nicht angelegt: %s", exc)


async def _quorate(host: PveHost) -> bool:
    try:
        async with pve.PveClient(host, timeout=10.0) as client:
            rows = await client.get("/cluster/status") or []
    except pve.PveError:
        return False
    cluster = next((r for r in rows if r.get("type") == "cluster"), None)
    return cluster is None or bool(cluster.get("quorate"))


# -----------------------------------------------------------------------------
# Zeitplan
# -----------------------------------------------------------------------------
async def tick(now: Optional[datetime] = None) -> None:
    from .database import async_session
    now = now or datetime.utcnow()
    data = await load_config()
    if not any(c.get("enabled") for c in data["hosts"].values()):
        return
    async with async_session() as db:
        hosts = (await db.execute(select(PveHost).where(PveHost.enabled.is_(True)))).scalars().all()
        for h in hosts:
            db.expunge(h)
    for h in hosts:
        cfg = data["hosts"].get(str(h.id))
        if not cfg or not cfg.get("enabled"):
            continue
        state = window_state(cfg, now)
        if not state["open"]:
            continue
        begin = _iso(state["begin"])
        if data["last"].get(str(h.id)) == begin or active_run(h.id):
            continue
        await _mark_window(h.id, begin)
        try:
            start_run(h, cfg, "Zeitplan", trigger="schedule", deadline=state["end"])
        except pve.PveError as exc:
            logger.info("Proxmox %s: geplanter Patch-Lauf nicht gestartet: %s", h.name, exc)


async def loop() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await tick()
        except Exception as exc:                                    # defensiv
            logger.warning("Proxmox-Patch-Takt: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)


async def overview(hosts: List[PveHost]) -> dict:
    """Zeitplan, naechstes Fenster und Laeufe je Host - fuer Oberflaeche und App."""
    data = await load_config()
    out = []
    for h in hosts:
        cfg = data["hosts"].get(str(h.id)) or dict(DEFAULT_HOST_CONFIG)
        st = window_state(cfg)
        out.append({"host_id": h.id, "host": h.name, "config": cfg,
                    "window_open": st["open"], "next_window": _iso(st["next"] or st["begin"]),
                    "running": public_run(active_run(h.id)) if active_run(h.id) else None})
    return {"hosts": out, "runs": runs_for(), "history": await history(),
            "defaults": DEFAULT_HOST_CONFIG, "reboot_modes": list(REBOOT_MODES)}
