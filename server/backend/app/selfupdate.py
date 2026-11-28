# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Autopilot: RIMPERIO haelt sich selbst aktuell
#               (Releases, fremde Images, Sicherheitsupdates des Hosts).
# ==============================================================================
"""
RIMPERIO haelt sich selbst aktuell
==================================

Bisher hiess "aktuell bleiben": jemand schaut nach, klickt auf Update - oder
fragt jemanden, der es tut. Der Autopilot macht das selbst und holt nur dann
einen Menschen, wenn es wirklich einen braucht.

Was er tut (jeder Teil einzeln abschaltbar unter RIMPERIO-Server → Updates):

1. **RIMPERIO-Releases** (Kanal "Stabil"): Fehlerbehebungen (PATCH) und neue
   Funktionen (MINOR) spielt er im Wartungsfenster ein - mit Datenbank-
   Sicherung vorher und dem automatischen Rueckfall des Wartungsskripts, falls
   die Oberflaeche danach nicht antwortet. **Sicherheits-Releases** (im
   Release-Text: Sicherheit, Security, CVE) wartet er nicht auf das Fenster.
2. **Freigabe noetig** sind Hauptversionen (MAJOR - etwas Inkompatibles) und
   Releases, deren Text "[Freigabe]" oder "BREAKING" enthaelt. Dann kommt eine
   Meldung mit Freigabe-Knopf - in der Oberflaeche und als Push in der App.
   Nach der Freigabe laeuft das Update sofort.
3. **Fremde Images** (Caddy, Postfix, n8n, Portainer …): neuer Stand desselben
   Tags wird im Wartungsfenster geholt. Datenbank-Images nur, wenn ausdruecklich
   eingeschaltet (die gehoeren zu den DB-Updates mit Probelauf).
4. **Host-Betriebssystem**: auf Wunsch richtet er `unattended-upgrades`
   (Debian/Ubuntu) fuer Sicherheitsupdates ein - ohne automatischen Neustart.

Ist ein automatisches Update fehlgeschlagen (Rueckfall gelaufen), versucht er
dieselbe Version nicht noch einmal von selbst, sondern meldet sich.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select

from .config import settings
from .versioning import compare_versions, version_key

logger = logging.getLogger("rimperio.selfupdate")

SETTING_KEY = "self_update"
STATE_KEY = "self_update_state"
LOOP_SECONDS = 600

DEFAULT: Dict[str, Any] = {
    "enabled": True,
    "auto_patch": True,            # 0.3.0 -> 0.3.1
    "auto_minor": True,            # 0.3.x -> 0.4.0
    "security_now": True,          # Sicherheits-Releases ohne Wartungsfenster
    "images": True,                # fremde Images (gleicher Tag, neuer Stand)
    "images_db": False,            # auch PostgreSQL & Co.
    "host_security": False,        # unattended-upgrades auf dem Host
    "window": {"days": [0, 1, 2, 3, 4, 5, 6], "start": "03:00", "end": "05:00", "tz": "Europe/Berlin"},
}

_SECURITY = re.compile(r"(?i)\b(sicherheit|security|cve-\d{4}-\d+|schwachstelle|vulnerab)")
_APPROVAL = re.compile(r"(?i)(\[freigabe\]|breaking|freigabe n[öo]tig|manuelle? freigabe)")


# -----------------------------------------------------------------------------
# Einstellungen und Zustand
# -----------------------------------------------------------------------------
async def _get(key: str, default: dict) -> dict:
    from .database import async_session
    from .models import Setting
    async with async_session() as db:
        row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    out = dict(default)
    if row and isinstance(row.value, dict):
        out.update(row.value)
    return out


async def _put(key: str, value: dict, description: str = "") -> None:
    from .database import async_session
    from .models import Setting
    async with async_session() as db:
        row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value, description=description))
        await db.commit()


async def load_config() -> dict:
    cfg = await _get(SETTING_KEY, DEFAULT)
    cfg["window"] = {**DEFAULT["window"], **(cfg.get("window") or {})}
    return cfg


async def save_config(incoming: dict) -> dict:
    cfg = await load_config()
    for k in ("enabled", "auto_patch", "auto_minor", "security_now", "images", "images_db", "host_security"):
        if k in incoming and incoming[k] is not None:
            cfg[k] = bool(incoming[k])
    w = incoming.get("window")
    if isinstance(w, dict):
        days = [int(d) for d in (w.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6]
        for t in ("start", "end"):
            if w.get(t) and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(w[t])):
                raise ValueError("Uhrzeit bitte als HH:MM.")
        cfg["window"] = {**cfg["window"], **{k: v for k, v in w.items() if k in ("start", "end", "tz")},
                         "days": days or cfg["window"]["days"]}
    await _put(SETTING_KEY, cfg, "Autopilot: RIMPERIO haelt sich selbst aktuell")
    if cfg.get("host_security"):
        asyncio.create_task(_ensure_host_security())
    return cfg


async def load_state() -> dict:
    return await _get(STATE_KEY, {"pending": None, "approved": [], "failed": [], "skipped": [],
                                  "last_check": None, "last_images": None, "history": [],
                                  "host_security": None, "awaiting": None})


async def _save_state(st: dict) -> None:
    st["history"] = (st.get("history") or [])[-30:]
    await _put(STATE_KEY, st, "Zustand des Autopiloten")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# -----------------------------------------------------------------------------
# Entscheiden
# -----------------------------------------------------------------------------
def classify(installed: str, release: dict) -> dict:
    """Welche Art Update ist das, und darf es von selbst laufen?"""
    tag = release.get("tag") or ""
    notes = f"{release.get('name') or ''}\n{release.get('notes') or ''}"
    a, b = version_key(installed), version_key(tag)
    level = "unknown"
    if a and b and a[0] == b[0] == 1:                # SemVer (1, (major, minor, patch), pre)
        (ma, mi, pa), (mb, mib, pb) = a[1][:3], b[1][:3]
        level = "major" if mb != ma else "minor" if mib != mi else "patch"
    elif a and b and a[0] == 0 and b[0] == 1:
        level = "minor"                              # alte Zeitstempel-Version -> SemVer
    security = bool(_SECURITY.search(notes))
    approval = level in ("major", "unknown") or bool(_APPROVAL.search(notes))
    return {"tag": tag, "level": level, "security": security, "needs_approval": approval,
            "name": release.get("name") or tag, "url": release.get("url"), "notes": (release.get("notes") or "")[:3000]}


def in_window(window: dict, now_utc: Optional[datetime] = None) -> bool:
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(window.get("tz") or "Europe/Berlin")
    except Exception:                                               # defensiv
        tz = ZoneInfo("UTC")
    now = (now_utc or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    start, end = window.get("start", "03:00"), window.get("end", "05:00")
    cur = now.strftime("%H:%M")
    days = window.get("days") or list(range(7))
    if start <= end:
        return now.weekday() in days and start <= cur < end
    # ueber Mitternacht (22:00-02:00): Tag des Beginns zaehlt
    if cur >= start:
        return now.weekday() in days
    return (now - timedelta(days=1)).weekday() in days and cur < end


async def plan(force: bool = False) -> dict:
    """Was wuerde der Autopilot jetzt tun? (auch fuer die Oberflaeche)"""
    from . import updater
    cfg, st = await load_config(), await load_state()
    channel = await updater.load_channel()
    installed = settings.app_version
    out: Dict[str, Any] = {"config": cfg, "state": st, "installed": installed, "channel": channel.get("channel"),
                           "in_window": in_window(cfg["window"]), "candidate": None, "action": "none", "reason": ""}
    if channel.get("channel") != "stable":
        out["reason"] = ("Der Autopilot spielt nur Releases ein (Kanal „Stabil“). Auf diesem Kanal bleibt "
                         "es beim Hinweis.")
        return out
    latest = await updater.latest_release(force=force)
    if not latest or compare_versions(latest.get("tag"), installed) != 1:
        out["reason"] = "RIMPERIO ist aktuell."
        return out
    c = classify(installed, latest)
    out["candidate"] = c
    tag = c["tag"]
    if tag in (st.get("failed") or []):
        out.update(action="blocked", reason=f"{tag} ist beim letzten Versuch fehlgeschlagen (Rückfall). "
                                             "Bitte von Hand einspielen oder das Protokoll ansehen.")
    elif tag in (st.get("skipped") or []):
        out.update(action="skipped", reason=f"{tag} wurde übersprungen.")
    elif c["needs_approval"] and tag not in (st.get("approved") or []):
        out.update(action="await_approval", reason=("Hauptversion – " if c["level"] == "major" else "")
                   + f"{tag} braucht eine Freigabe (Oberfläche oder App).")
    elif not cfg.get("enabled"):
        out.update(action="manual", reason="Autopilot ist aus – Update nur von Hand.")
    elif tag in (st.get("approved") or []):
        out.update(action="install_now", reason=f"{tag} ist freigegeben.")
    elif c["level"] == "patch" and not cfg.get("auto_patch") or c["level"] == "minor" and not cfg.get("auto_minor"):
        out.update(action="manual", reason=f"{c['level'].upper()}-Updates sind auf „von Hand“ gestellt.")
    elif c["security"] and cfg.get("security_now"):
        out.update(action="install_now", reason=f"Sicherheits-Release {tag} – wird sofort eingespielt.")
    elif out["in_window"]:
        out.update(action="install_now", reason=f"{tag} im Wartungsfenster.")
    else:
        w = cfg["window"]
        out.update(action="wait_window", reason=f"{tag} kommt im nächsten Wartungsfenster ({w['start']}–{w['end']}).")
    return out


# -----------------------------------------------------------------------------
# Handeln
# -----------------------------------------------------------------------------
async def _notify(title: str, body: str, level: str = "info", action: str = "", data: Optional[dict] = None,
                  key: str = "") -> None:
    from . import notify
    from .database import async_session
    try:
        async with async_session() as db:
            await notify.push(db, category="system", title=title, body=body, level=level, link="/updates",
                              action=action, data=data or {}, event="update_available", key=key or title)
    except Exception as exc:                                        # defensiv
        logger.warning("Meldung des Autopiloten nicht abgesetzt: %s", exc)


async def _resolve_approval(tag: str) -> None:
    from . import notify
    from .database import async_session
    try:
        async with async_session() as db:
            await notify.resolve(db, action="rimperio_update", match={"version": tag})
    except Exception:                                               # defensiv
        pass


async def approve(tag: str, user: str) -> dict:
    st = await load_state()
    if tag not in st["approved"]:
        st["approved"].append(tag)
    st["failed"] = [t for t in st.get("failed") or [] if t != tag]
    st["skipped"] = [t for t in st.get("skipped") or [] if t != tag]
    st["history"].append({"at": _now(), "event": "approved", "version": tag, "by": user})
    await _save_state(st)
    await _resolve_approval(tag)
    asyncio.create_task(tick())                       # sofort, nicht erst in 10 Minuten
    return {"approved": tag}


async def skip(tag: str, user: str) -> dict:
    st = await load_state()
    if tag not in st["skipped"]:
        st["skipped"].append(tag)
    st["history"].append({"at": _now(), "event": "skipped", "version": tag, "by": user})
    await _save_state(st)
    await _resolve_approval(tag)
    return {"skipped": tag}


async def _check_pending(st: dict) -> None:
    """Nach einem automatischen Update: hat es geklappt?"""
    p = st.get("pending")
    if not p:
        return
    from . import updater
    if compare_versions(settings.app_version, p["version"]) >= 0:
        st["history"].append({"at": _now(), "event": "installed", "version": p["version"]})
        st["pending"] = None
        st["approved"] = [t for t in st.get("approved") or [] if t != p["version"]]
        await _save_state(st)
        await _notify(f"RIMPERIO {p['version']} eingespielt", f"Der Autopilot hat RIMPERIO auf {p['version']} "
                      "aktualisiert. Alles läuft.", key=f"selfupdate:ok:{p['version']}")
        return
    run = await updater.read_run_state()
    started = datetime.fromisoformat(p["started_at"].rstrip("Z"))
    if run.get("status") == "running" and datetime.utcnow() - started < timedelta(hours=2):
        return
    if run.get("status") in ("failed", "error") or datetime.utcnow() - started > timedelta(minutes=45):
        st["failed"] = sorted(set((st.get("failed") or []) + [p["version"]]))
        st["history"].append({"at": _now(), "event": "failed", "version": p["version"],
                              "detail": (run.get("message") or "")[:300]})
        st["pending"] = None
        await _save_state(st)
        await _notify(f"Automatisches Update auf {p['version']} fehlgeschlagen",
                      f"Der Rückfall auf {settings.app_version} lief, RIMPERIO arbeitet weiter. {run.get('message') or ''} "
                      "Bitte unter RIMPERIO-Server → Updates das Protokoll ansehen; die Version wird nicht noch "
                      "einmal automatisch versucht.", level="critical", key=f"selfupdate:fail:{p['version']}")


async def _images(cfg: dict, st: dict) -> None:
    """Fremde Images: einmal je Wartungsfenster neue Staende desselben Tags holen."""
    if not cfg.get("images") or not in_window(cfg["window"]):
        return
    last = st.get("last_images")
    if last and datetime.utcnow() - datetime.fromisoformat(last.rstrip("Z")) < timedelta(hours=20):
        return
    from . import containers
    st["last_images"] = _now()
    await _save_state(st)
    try:
        data = await containers.check_updates(force=True)
    except Exception as exc:                                        # defensiv
        logger.warning("Autopilot: Image-Prüfung fehlgeschlagen: %s", exc)
        return
    done, failed = [], []
    for item in data.get("containers") or []:
        if not (item.get("update") or {}).get("update_available"):
            continue
        comp = item.get("component") or ""
        if not cfg.get("images_db") and re.search(r"(?i)postgres|mariadb|mysql|mongo|redis|valkey", item.get("image") or ""):
            continue
        try:
            await containers.pull_and_recreate(comp)
            done.append(comp)
        except Exception as exc:
            failed.append(f"{comp}: {exc}")
    if done or failed:
        st = await load_state()
        st["history"].append({"at": _now(), "event": "images", "done": done, "failed": failed[:5]})
        await _save_state(st)
        await _notify("Fremde Images aktualisiert" if not failed else "Fremde Images: nicht alle aktualisiert",
                      ("Aktualisiert: " + ", ".join(done) if done else "") + ("\nFehler: " + "; ".join(failed) if failed else ""),
                      level="warning" if failed else "info", key=f"selfupdate:images:{_now()[:10]}")


HOST_SECURITY_SCRIPT = r"""set -e
if ! command -v apt-get >/dev/null 2>&1; then echo "UNSUPPORTED: kein apt (nur Debian/Ubuntu)"; exit 0; fi
export DEBIAN_FRONTEND=noninteractive
dpkg -s unattended-upgrades >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq unattended-upgrades; }
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52rimperio-unattended <<'EOF'
// RIMPERIO: nur Sicherheitsupdates, kein automatischer Neustart
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
echo "OK: $(unattended-upgrade --version 2>/dev/null | head -1)"
"""


async def _ensure_host_security() -> dict:
    from . import hostexec
    if not hostexec.nsenter_available():
        res = {"ok": False, "message": "Kein Zugriff auf den Host (gehärtetes Compose)."}
    else:
        r = await hostexec.run_host(["sh", "-c", HOST_SECURITY_SCRIPT], timeout=900.0)
        out = (r.stdout or "").strip().splitlines()[-1:] or [""]
        ok = r.ok and out[0].startswith("OK")
        res = {"ok": ok, "message": out[0] if (ok or out[0]) else (r.stderr or r.error or "fehlgeschlagen")[-300:]}
    st = await load_state()
    st["host_security"] = {**res, "at": _now()}
    await _save_state(st)
    return res


async def tick() -> dict:
    from . import updater
    st = await load_state()
    await _check_pending(st)
    st = await load_state()
    p = await plan(force=False)
    st["last_check"] = _now()
    c = p.get("candidate")
    if p["action"] == "await_approval" and c and st.get("awaiting") != c["tag"]:
        st["awaiting"] = c["tag"]
        await _save_state(st)
        await _notify(f"RIMPERIO {c['tag']} wartet auf Freigabe",
                      (f"Installiert: {p['installed']}. {c['name']}. "
                       + ("Hauptversion – kann Inkompatibles enthalten. " if c["level"] == "major" else "")
                       + ("Enthält Sicherheitskorrekturen. " if c["security"] else "")
                       + "Freigeben in der App oder unter RIMPERIO-Server → Updates."),
                      level="critical" if c["security"] else "warning", action="rimperio_update",
                      data={"version": c["tag"], "level": c["level"], "security": c["security"]},
                      key=f"selfupdate:approve:{c['tag']}")
    elif p["action"] == "install_now" and c and not st.get("pending"):
        run = await updater.read_run_state()
        if run.get("status") != "running":
            res = await updater.start_run("apply", database_backup=True, ref=c["tag"])
            if res.get("started"):
                st["pending"] = {"version": c["tag"], "started_at": _now(), "reason": p["reason"]}
                st["history"].append({"at": _now(), "event": "started", "version": c["tag"], "reason": p["reason"]})
                logger.warning("Autopilot: Update auf %s gestartet (%s)", c["tag"], p["reason"])
            else:
                st["history"].append({"at": _now(), "event": "not_started", "version": c["tag"],
                                      "detail": (res.get("error") or "")[:300]})
    await _save_state(st)
    cfg = await load_config()
    await _images(cfg, await load_state())
    if cfg.get("host_security") and not (await load_state()).get("host_security"):
        await _ensure_host_security()
    return p


async def loop() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await tick()
        except Exception as exc:                                    # defensiv
            logger.warning("Autopilot: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)
