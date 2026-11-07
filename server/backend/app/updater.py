# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.14.12.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Patchmanagement: Versionsvergleich, Update, Rueckfall
# ==============================================================================
"""
Vergleicht den installierten Stand mit dem Stand auf GitHub und stoesst Update
bzw. Rueckfall an.

Aufgabenteilung:

* Dieses Modul redet mit GitHub, liest den Zustand vom Host und startet das
  Wartungsskript. Es fuehrt selbst *keine* Docker-Befehle aus.
* Das eigentliche Update macht `scripts/rimperio-update.sh` auf dem Host. Das
  Skript wird bei jedem Lauf frisch aus dem Container auf den Host geschrieben,
  damit Skript und Backend nie auseinanderlaufen (und damit eine alte
  Installation ohne dieses Skript trotzdem aktualisiert werden kann).

Zustandsfuehrung: Das Skript schreibt fortlaufend nach
`<install>/data/update-state.json`. Die Oberflaeche fragt diesen Zustand ab.
Das ist bewusst eine Datei auf dem Host und keine Tabelle in der Datenbank -
waehrend eines Updates ist die Datenbank zeitweise nicht erreichbar.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select

from .config import settings
from . import hostexec
from .events import UPDATE_AVAILABLE, bus

logger = logging.getLogger("rimperio.updater")

# Repository, gegen das geprueft wird. Ueberschreibbar fuer Forks/Testzweige.
REPO_SLUG = os.getenv("RIMPERIO_REPO_SLUG", "Phydran6/RIMPERIO-Server")
REPO_BRANCH = os.getenv("RIMPERIO_BRANCH", "main")
REPO_URL = os.getenv("RIMPERIO_REPO", f"https://github.com/{REPO_SLUG}.git")

_API = "https://api.github.com"

# GitHub erlaubt ohne Anmeldung 60 Anfragen pro Stunde - deshalb zwischenspeichern.
_REMOTE_CACHE_TTL = int(os.getenv("UPDATE_CHECK_CACHE_SECONDS", "900"))
_remote_cache: Optional[dict] = None
_remote_cache_expires: float = 0.0

# Das Wartungsskript liegt im Backend-Image und wird vor jedem Lauf auf den Host
# geschrieben.
SCRIPT_SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "rimperio-update.sh"


# =============================================================================
# Installierter Stand (Host)
# =============================================================================
async def local_state() -> dict:
    """Was ist gerade installiert? Version, Git-Stand, Verzeichnis."""
    paths = hostexec.host_paths()
    state = {
        "version": settings.app_version,
        "install_dir": paths["install_dir"],
        "branch": REPO_BRANCH,
        "commit": None,
        "commit_short": None,
        "commit_date": None,
        "is_git": False,
        "host_access": hostexec.nsenter_available(),
        "note": "",
    }

    if not state["host_access"]:
        state["note"] = ("Kein Zugriff auf den Host - Version stammt aus dem laufenden "
                         "Container. Update ueber die Oberflaeche ist nicht moeglich.")
        return state

    install_dir = paths["install_dir"]

    # Alles in einem Aufruf: drei einzelne Host-Befehle kosten sonst je nach Last
    # mehrere Sekunden, und der Systemcheck hat ein Zeitlimit.
    # `safe.directory` verhindert Gits Beschwerde ueber fremde Besitzverhaeltnisse.
    script = (
        f'head -n1 "{install_dir}/VERSION" 2>/dev/null; echo "---"; '
        f'git -c safe.directory="{install_dir}" -C "{install_dir}" rev-parse HEAD 2>/dev/null; echo "---"; '
        f'git -c safe.directory="{install_dir}" -C "{install_dir}" log -1 --format=%cI 2>/dev/null'
    )
    result = await hostexec.run_host(["sh", "-c", script], timeout=15.0)
    if not result.ok:
        state["note"] = ("Der Zustand der Installation auf dem Host war nicht lesbar: "
                         + (result.error or result.stderr or "unbekannter Fehler").strip())
        return state

    parts = (result.stdout or "").split("---")
    version = parts[0].strip() if len(parts) > 0 else ""
    commit = parts[1].strip() if len(parts) > 1 else ""
    commit_date = parts[2].strip() if len(parts) > 2 else ""

    if version:
        state["version"] = version.splitlines()[0].strip()
    if commit:
        state["is_git"] = True
        state["commit"] = commit
        state["commit_short"] = commit[:7]
        state["commit_date"] = commit_date or None
    else:
        state["note"] = ("Die Installation ist kein Git-Repository. Das Update holt die "
                         "Dateien dann per frischem Clone von GitHub.")

    return state


# =============================================================================
# Stand auf GitHub
# =============================================================================
async def remote_state(force: bool = False, ref: str = "") -> dict:
    """Neuester Stand im Repository. Ergebnis wird zwischengespeichert.

    `ref` ist der Zweig, das Tag oder der Commit, gegen den geprueft wird. Leer
    bedeutet: der eingestellte Kanal entscheidet (siehe `resolve_target_ref`).
    """
    global _remote_cache, _remote_cache_expires

    target = (ref or REPO_BRANCH).strip() or REPO_BRANCH

    # Der Zwischenspeicher gilt nur fuer denselben Bezugspunkt - sonst
    # antwortet eine Abfrage fuer ein Tag mit dem Stand des Zweiges.
    if (not force and _remote_cache and time.time() < _remote_cache_expires
            and _remote_cache.get("ref") == target):
        return _remote_cache

    info = {
        "repo": REPO_SLUG,
        "branch": REPO_BRANCH,
        "ref": target,
        "reachable": False,
        "commit": None,
        "commit_short": None,
        "commit_date": None,
        "commit_message": None,
        "version": None,
        "error": None,
        "checked_at": time.time(),
    }

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "RIMPERIO-Updater"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
            response = await client.get(f"{_API}/repos/{REPO_SLUG}/commits/{target}")
            if response.status_code == 404:
                info["error"] = f"Repository, Zweig oder Release nicht gefunden ({REPO_SLUG}@{target})."
                return _cache_remote(info)
            if response.status_code == 403:
                info["error"] = ("GitHub hat die Anfrage abgelehnt (Limit fuer anonyme Abfragen). "
                                 "Spaeter erneut versuchen oder GITHUB_TOKEN setzen.")
                return _cache_remote(info)
            response.raise_for_status()
            data = response.json()

            info["reachable"] = True
            info["commit"] = data.get("sha")
            info["commit_short"] = (data.get("sha") or "")[:7] or None
            commit = data.get("commit") or {}
            info["commit_message"] = (commit.get("message") or "").strip().splitlines()[0] if commit.get("message") else None
            info["commit_date"] = ((commit.get("committer") or {}).get("date")
                                   or (commit.get("author") or {}).get("date"))

            # VERSION-Datei ist optional - fehlt sie, bleibt der Commit der Massstab.
            try:
                raw = await client.get(
                    f"https://raw.githubusercontent.com/{REPO_SLUG}/{target}/VERSION"
                )
                if raw.status_code == 200 and raw.text.strip():
                    info["version"] = raw.text.strip().splitlines()[0].strip()
            except httpx.HTTPError:
                pass

    except httpx.HTTPError as exc:
        info["error"] = f"GitHub nicht erreichbar: {exc}"
    except Exception as exc:                                   # defensiv
        info["error"] = f"Update-Pruefung fehlgeschlagen: {exc}"

    return _cache_remote(info)


_branch_version_cache: Optional[str] = None
_branch_version_expires: float = 0.0


async def branch_version(force: bool = False) -> Optional[str]:
    """VERSION auf dem Zweig - eigener kleiner Zwischenspeicher.

    Gebraucht, wenn der Kanal auf ein Release zeigt: dann soll die Seite
    trotzdem sagen koennen, dass auf dem Zweig schon etwas Neueres liegt, fuer
    das nur noch kein Release angelegt wurde. Sonst steht dort "entspricht
    GitHub", obwohl GitHub laengst weiter ist.
    """
    global _branch_version_cache, _branch_version_expires
    if not force and time.time() < _branch_version_expires:
        return _branch_version_cache
    version = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            raw = await client.get(
                f"https://raw.githubusercontent.com/{REPO_SLUG}/{REPO_BRANCH}/VERSION")
            if raw.status_code == 200 and raw.text.strip():
                version = raw.text.strip().splitlines()[0].strip()
    except Exception:                                               # defensiv
        version = None
    _branch_version_cache = version
    _branch_version_expires = time.time() + (_REMOTE_CACHE_TTL if version else 60)
    return version


def _cache_remote(info: dict) -> dict:
    global _remote_cache, _remote_cache_expires
    _remote_cache = info
    # Fehlschlaege nur kurz merken, damit ein Klick auf "Erneut pruefen" hilft.
    _remote_cache_expires = time.time() + (_REMOTE_CACHE_TTL if info.get("reachable") else 60)
    return info


# =============================================================================
# Gesamtbild
# =============================================================================
def parse_version(value: str) -> tuple:
    """'2026.09.09.22.00.00' -> (2026, 9, 9, 22, 0, 0). Unlesbares -> ().

    Auf sechs Stellen begrenzt, damit ein Zusatz wie '-rc1' den Vergleich nicht
    kippt: verglichen wird der Zeitstempel, nicht das Anhaengsel.
    """
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(p) for p in parts[:6]) if parts else ()


def compare(local: dict, remote: dict) -> tuple:
    """Ist ein Update da? Gibt (verfuegbar, Begruendung, Verhaeltnis) zurueck.

    Das Verhaeltnis ist eines von: 'behind' (der Server hinkt hinterher, es gibt
    also wirklich ein Update), 'same', 'ahead' (der Server ist NEUER als das,
    worauf der Kanal zeigt) oder 'unknown'.

    Warum die Reihenfolge zaehlt und nicht blosse Ungleichheit: Der Kanal
    'stabil' zeigt auf das neueste veroeffentlichte Release. Bleibt das
    Release-Anlegen mal aus, ist dieses Release aelter als der installierte
    Stand. Ein reiner Ungleichheitsvergleich meldet dann 'Update verfuegbar'
    und das Einspielen faehrt den Server in Wahrheit zurueck - ein Downgrade,
    als Update getarnt. Genau das darf nicht passieren.
    """
    if not remote.get("reachable"):
        return False, (remote.get("error") or "Stand auf GitHub unbekannt."), "unknown"

    local_version = parse_version(local.get("version"))
    remote_version = parse_version(remote.get("version"))

    # 1. Versionsstaende, sofern beide lesbar: die tragen die Reihenfolge.
    if local_version and remote_version:
        if remote_version > local_version:
            return True, "Auf GitHub liegt ein neuerer Stand.", "behind"
        if remote_version < local_version:
            return False, (
                f"Der installierte Stand ({local.get('version')}) ist NEUER als das, "
                f"worauf der eingestellte Kanal zeigt ({remote.get('version')}). "
                f"Einspielen waere ein Rueckschritt. Fehlt ein Release fuer den "
                f"aktuellen Stand?"
            ), "ahead"
        # Gleiche Version: der Commit entscheidet (Zweig weitergelaufen,
        # ohne dass die VERSION-Datei angehoben wurde).

    # 2. Gleiche oder unlesbare Version: Commits vergleichen.
    if local.get("commit") and remote.get("commit"):
        if local["commit"] == remote["commit"]:
            return False, "Der installierte Stand entspricht GitHub.", "same"
        # Ohne Versionsangabe laesst sich die Richtung nicht bestimmen - dann
        # gilt "es gibt etwas anderes", so wie bisher.
        if local_version and remote_version and local_version == remote_version:
            return True, ("Gleiche Version, aber ein anderer Commit - auf dem Zweig "
                          "liegt Neues."), "behind"
        return True, "Auf GitHub liegt ein anderer Stand.", "behind"

    # 3. Nur die Versionsdatei ist lesbar.
    if remote_version and not local_version:
        return True, "Auf GitHub steht eine Version, hier ist keine lesbar.", "unknown"
    if remote.get("version"):
        available = remote["version"] != local.get("version")
        return available, ("Auf GitHub steht eine andere Version." if available
                           else "Die Version entspricht dem Stand auf GitHub."), \
            ("behind" if available else "same")

    return False, "Kein Vergleich moeglich (weder Commit noch VERSION-Datei lesbar).", "unknown"


async def version_check(force: bool = False) -> dict:
    """Nur der Versionsvergleich - ohne Sicherungen und Laufzustand.

    Der Systemcheck nutzt diesen schlanken Weg; die Update-Seite das volle Bild.
    """
    channel = await load_channel()
    ref = await resolve_target_ref(channel)
    local = await local_state()
    remote = await remote_state(force=force, ref=ref)
    available, reason, relation = compare(local, remote)
    return {"local": local, "remote": remote, "channel": channel,
            "update_available": available, "reason": reason, "relation": relation}


async def update_status(force: bool = False) -> dict:
    """Installierter Stand + GitHub-Stand + Bewertung + laufender Vorgang.

    `force` kommt nur vom Knopf "Auf Updates pruefen" - dann auch die
    Release-Liste frisch. Der Beobachter (alle 120 s) nutzt version_check und
    laesst sie im Zwischenspeicher: sonst waere das GitHub-Limit fuer anonyme
    Abfragen (60 pro Stunde) allein durch ihn erschoepft.
    """
    channel = await load_channel()
    ref = await resolve_target_ref(channel, force=force)
    local = await local_state()
    remote = await remote_state(force=force, ref=ref)
    run = await read_run_state()
    backups = await list_backups()
    available, reason, relation = compare(local, remote)

    # Zeigt der Kanal auf ein Release, kann der Zweig schon weiter sein.
    unreleased = None
    if ref != REPO_BRANCH:
        newest = await branch_version(force=force)
        reference = remote.get("version") or ""
        if newest and parse_version(newest) and parse_version(newest) > parse_version(reference):
            unreleased = {"version": newest, "branch": REPO_BRANCH}

    oneliner = (f"curl -sSL https://raw.githubusercontent.com/{REPO_SLUG}/{REPO_BRANCH}/install.sh "
                f"| sudo bash -s -- update -y")
    if ref != REPO_BRANCH:
        oneliner += f" --ref {ref}"

    return {
        "local": local,
        "remote": remote,
        "channel": channel,
        "target_ref": ref,
        "update_available": available,
        "reason": reason,
        # 'behind' | 'same' | 'ahead' | 'unknown' - die Oberflaeche zeigt bei
        # 'ahead' keinen Update-Knopf, sondern den Hinweis auf das fehlende Release.
        "relation": relation,
        "is_downgrade": relation == "ahead",
        "repo_url": REPO_URL,
        "run": run,
        "backups": backups,
        "can_update": bool(local.get("host_access")),
        "webhook": webhook_info(),
        "watcher": {"interval_seconds": WATCH_INTERVAL, "enabled": WATCH_ENABLED},
        "oneliner": oneliner,
        # Neuerer Stand auf dem Zweig, fuer den es noch kein Release gibt.
        "unreleased": unreleased,
        # Fuer das Terminal auf der Update-Seite: genau die Zeile, die nach dem
        # Enter auf dem Host laeuft (Sicherungs-Schalter wird dort umgesetzt).
        "command": build_command("apply", database_backup=True, ref=ref),
        "install_dir": hostexec.host_paths()["install_dir"],
    }


# =============================================================================
# Laufender Vorgang
# =============================================================================
async def read_run_state() -> dict:
    """Liest den Zustand des letzten/laufenden Wartungslaufs vom Host."""
    paths = hostexec.host_paths()
    if not hostexec.nsenter_available():
        return {"status": "unknown", "message": "Kein Zugriff auf den Host."}

    raw = await hostexec.read_host_file(paths["state_file"])
    if not raw:
        return {"status": "idle", "message": "Bisher kein Update ueber die Oberflaeche gelaufen."}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "unknown", "message": "Zustandsdatei ist unlesbar."}
    if not isinstance(state, dict):
        return {"status": "unknown", "message": "Zustandsdatei hat ein unerwartetes Format."}
    return state


async def read_run_log(lines: int = 200) -> list:
    """Die letzten Zeilen des Wartungsprotokolls."""
    paths = hostexec.host_paths()
    raw = await hostexec.read_host_file(paths["log_file"], timeout=15.0)
    if raw is None:
        return []
    return hostexec.split_lines(raw, limit=lines)


async def log_size() -> int:
    """Wie gross ist das Wartungsprotokoll gerade? (Bytes, 0 wenn es fehlt.)"""
    paths = hostexec.host_paths()
    result = await hostexec.run_host(
        ["sh", "-c", f'wc -c < "{paths["log_file"]}" 2>/dev/null || echo 0'], timeout=10.0)
    try:
        return int((result.stdout or "0").strip() or 0)
    except ValueError:
        return 0


async def read_log_from(offset: int, limit_bytes: int = 65536) -> tuple:
    """Alles, was seit `offset` dazugekommen ist. Gibt (Text, neuer Offset).

    Damit laesst sich das Protokoll wie ein `tail -f` mitlesen, ohne die ganze
    Datei bei jedem Takt erneut durch den Host zu schleusen - waehrend eines
    Updates schreibt das Skript zuegig, und die Datei wird schnell gross.

    Wird die Datei kleiner als der Offset (ein neuer Lauf faengt von vorne an),
    beginnt das Mitlesen wieder bei Null - sonst bliebe die Ausgabe stumm.
    """
    paths = hostexec.host_paths()
    size = await log_size()
    if size < offset:
        offset = 0                    # neuer Lauf, Datei wurde ueberschrieben
    if size <= offset:
        return "", offset

    result = await hostexec.run_host(
        ["sh", "-c",
         f'tail -c +{offset + 1} "{paths["log_file"]}" 2>/dev/null | head -c {int(limit_bytes)}'],
        timeout=15.0)
    if not result.ok:
        return "", offset

    chunk = result.stdout or ""
    return chunk, offset + len(chunk.encode("utf-8", "replace"))


async def stream_run_log(poll_seconds: float = 1.0, idle_stop_seconds: float = 900.0):
    """Server-Sent Events mit der laufenden Ausgabe des Wartungslaufs.

    Wozu: Ein Fortschrittsbalken sagt "43 %". Er sagt nicht, *woran* es gerade
    haengt - und wenn etwas schiefgeht, sagt er gar nichts. Wer zusehen kann,
    wie `docker compose build` durchlaeuft, weiss im Fehlerfall sofort, wo es
    klemmte, statt hinterher zu raten.

    Der Strom endet von selbst, wenn der Lauf fertig ist und danach eine Weile
    nichts mehr kommt - ein offener Kanal, an dem niemand mehr haengt, soll
    nicht ewig weiterlaufen.
    """
    import json as _json

    offset = 0
    quiet_for = 0.0
    finished_at: Optional[float] = None

    # Erst den bisherigen Stand nachreichen, damit wer spaeter dazukommt den
    # Anfang nicht verpasst.
    history = await read_run_log(lines=400)
    if history:
        payload = _json.dumps({"lines": history, "initial": True}, ensure_ascii=False)
        yield f"event: update.output\ndata: {payload}\n\n"
        offset = await log_size()

    while True:
        chunk, offset = await read_log_from(offset)
        if chunk:
            quiet_for = 0.0
            lines = [line.rstrip() for line in chunk.splitlines() if line.strip()]
            if lines:
                payload = _json.dumps({"lines": lines, "initial": False}, ensure_ascii=False)
                yield f"event: update.output\ndata: {payload}\n\n"
        else:
            quiet_for += poll_seconds
            if quiet_for >= 15.0:
                quiet_for = 0.0
                yield ": heartbeat\n\n"

        state = await read_run_state()
        payload = _json.dumps(state, ensure_ascii=False, default=str)
        yield f"event: update.state\ndata: {payload}\n\n"

        if state.get("status") not in ("running", "unknown"):
            # Fertig - aber noch kurz dranbleiben, damit die letzten Zeilen
            # sicher ankommen.
            finished_at = finished_at or time.time()
            if time.time() - finished_at > 10:
                yield "event: update.done\ndata: {}\n\n"
                return
        else:
            finished_at = None

        await asyncio.sleep(poll_seconds)


async def list_backups() -> list:
    """Vorhandene Sicherungen (fuer den Rueckfall).

    Bewusst ein einziger Host-Aufruf: pro Sicherung einzeln nachzufragen dauert
    auf einem beschaeftigten Server zu lange. Jede Zeile ist
    `Name<TAB>Groesse<TAB>backup.json in einer Zeile`.
    """
    paths = hostexec.host_paths()
    if not hostexec.nsenter_available():
        return []

    root = paths["backup_dir"]
    script = (
        f'[ -d "{root}" ] || exit 0; '
        f'for name in $(ls -1 "{root}" 2>/dev/null | sort -r | head -n 20); do '
        f'  size=$(du -sh "{root}/$name" 2>/dev/null | cut -f1); '
        f'  meta=$(cat "{root}/$name/backup.json" 2>/dev/null | tr -d "\\n\\r"); '
        f'  printf "%s\\t%s\\t%s\\n" "$name" "$size" "$meta"; '
        f'done'
    )
    result = await hostexec.run_host(["sh", "-c", script], timeout=25.0)
    if not result.ok:
        return []

    backups = []
    for line in hostexec.split_lines(result.stdout, limit=20):
        parts = line.split("\t")
        name = parts[0].strip()
        if not name:
            continue
        entry = {"name": name, "created_at": None, "version": None, "commit": None,
                 "database_dump": False, "size": (parts[1].strip() if len(parts) > 1 else None)}
        if len(parts) > 2 and parts[2].strip():
            try:
                meta = json.loads(parts[2])
                entry.update({
                    "created_at": meta.get("created_at"),
                    "version": meta.get("version"),
                    "commit": meta.get("commit"),
                    "database_dump": bool(meta.get("database_dump")),
                })
            except (json.JSONDecodeError, TypeError):
                pass
        backups.append(entry)
    return backups


# =============================================================================
# Wartungslauf starten
# =============================================================================
async def _install_script() -> Optional[str]:
    """Legt das Wartungsskript auf dem Host ab. Gibt einen Fehlertext zurueck."""
    if not SCRIPT_SOURCE.exists():
        return f"Wartungsskript fehlt im Image ({SCRIPT_SOURCE})."
    content = SCRIPT_SOURCE.read_text(encoding="utf-8")
    # Zeilenenden vereinheitlichen - eine CRLF-Datei startet auf Linux nicht.
    content = content.replace("\r\n", "\n")
    paths = hostexec.host_paths()
    result = await hostexec.write_host_file(paths["script"], content, mode="0750")
    if not result.ok:
        return (result.error or result.stderr or "Unbekannter Fehler").strip()
    return None


def build_command(action: str, database_backup: bool = True,
                  backup_name: str = "", ref: str = "") -> list:
    """Die Befehlszeile, die auf dem Host laeuft.

    Eine Stelle fuer beides: den echten Start und die Anzeige im Terminal der
    Oberflaeche. So steht dort genau das, was nach dem Enter auch passiert.
    """
    paths = hostexec.host_paths()
    command = [
        "/bin/bash", paths["script"], action,
        "--dir", paths["install_dir"],
        "--repo", REPO_URL,
        "--branch", REPO_BRANCH,
    ]
    if action == "apply":
        command.append("--db-backup" if database_backup else "--no-db-backup")
        if ref and ref != REPO_BRANCH:
            if not _GIT_REF_RE.match(ref) or ".." in ref:
                raise ValueError("Ungültige Versionsangabe.")
            command.extend(["--ref", ref])
    if action == "rollback" and backup_name:
        command.extend(["--backup", backup_name])
    return command


async def start_run(action: str, database_backup: bool = True,
                    backup_name: str = "", ref: str = "") -> dict:
    """Startet `apply` oder `rollback` auf dem Host.

    Kehrt sofort zurueck - der Lauf ersetzt waehrenddessen diesen Container.
    """
    if action not in ("apply", "rollback"):
        raise ValueError(f"Unbekannte Aktion: {action}")

    if not hostexec.nsenter_available():
        return {"started": False,
                "error": ("Kein Zugriff auf den Host. Update ueber die Oberflaeche ist nur "
                          "moeglich, wenn das Backend mit privileged/pid:host laeuft "
                          "(Standard-docker-compose.yml).")}

    running = await read_run_state()
    if running.get("status") == "running":
        return {"started": False,
                "error": "Es laeuft bereits ein Wartungsvorgang.",
                "run": running}

    script_error = await _install_script()
    if script_error:
        return {"started": False, "error": f"Wartungsskript konnte nicht abgelegt werden: {script_error}"}

    paths = hostexec.host_paths()
    if action == "apply":
        # Welchen Stand holen? Ohne Angabe der Zweig, sonst das gewaehlte Release.
        ref = (ref or await resolve_target_ref(await load_channel())).strip()
    command = build_command(action, database_backup=database_backup,
                            backup_name=backup_name, ref=ref)

    # Jeder Lauf bekommt ein frisches Protokoll, der vorige bleibt als .1
    # liegen. Frueher wurde nur angehaengt: die Datei wuchs mit jedem Update,
    # und die Live-Ansicht begann mit der Ausgabe des *vorigen* Laufs.
    log_file = paths["log_file"]
    await hostexec.run_host(
        ["sh", "-c", f'[ -f "{log_file}" ] && mv -f "{log_file}" "{log_file}.1"; : > "{log_file}" 2>/dev/null; true'],
        timeout=10.0)

    unit = f"rimperio-{action}-{int(time.time())}"
    result = await hostexec.spawn_host(command, unit_name=unit, log_path=paths["log_file"])
    if not result.ok:
        return {"started": False,
                "error": (result.error or result.stderr or "Start fehlgeschlagen").strip()}

    logger.warning("Wartungslauf '%s' gestartet (Unit %s)", action, unit)
    return {"started": True, "action": action, "unit": unit,
            "message": ("Update laeuft. Die Oberflaeche ist waehrenddessen kurz nicht "
                        "erreichbar - diese Seite meldet sich von selbst zurueck."
                        if action == "apply" else
                        "Rueckfall laeuft. Die Oberflaeche ist waehrenddessen kurz nicht erreichbar.")}


# =============================================================================
# Kanal: welchen Stand soll dieser Server ueberhaupt fahren?
# =============================================================================
"""
Nicht jeder will immer den letzten Commit. Drei Moeglichkeiten:

* `stable`  - das zuletzt veroeffentlichte Release (GitHub Release, kein Prerelease).
  Das ist die Voreinstellung fuer alle, die einfach nur einen Server betreiben.
* `edge`    - der Kopf des Zweiges. Alles, was gepusht wird, steht sofort bereit.
* `pinned`  - genau ein Release/Tag/Commit, festgenagelt. Der Server bleibt dort
  stehen, bis jemand ihn bewusst weiterzieht.

Die Wahl liegt in der settings-Tabelle, damit sie ein Update ueberlebt.
"""

CHANNEL_SETTING_KEY = "update_channel"
_GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,199}$")

CHANNELS = {
    "stable": {
        "label": "Stabil (letztes Release)",
        "label_en": "Stable (latest release)",
        "hint": "Nur veröffentlichte Releases. Empfohlen für den Regelbetrieb.",
    },
    "edge": {
        "label": f"Aktuell ({REPO_BRANCH})",
        "label_en": f"Latest ({REPO_BRANCH})",
        "hint": "Jeder Push landet sofort als Angebot hier. Für Test- und Entwicklungsserver.",
    },
    "pinned": {
        "label": "Festgelegte Version",
        "label_en": "Pinned version",
        "hint": "Ein bestimmtes Release oder Tag. Der Server bleibt darauf stehen.",
    },
}

DEFAULT_CHANNEL = {"channel": "stable", "ref": "", "auto_offer": True}


async def load_channel() -> dict:
    """Liest die Kanal-Einstellung. Faellt auf 'stable' zurueck."""
    from .database import async_session
    from .models import Setting

    try:
        async with async_session() as session:
            row = (await session.execute(
                select(Setting).where(Setting.key == CHANNEL_SETTING_KEY)
            )).scalar_one_or_none()
    except Exception as exc:                                        # DB noch nicht da
        logger.debug("Kanal-Einstellung nicht lesbar (%s) - nehme Standard", exc)
        return dict(DEFAULT_CHANNEL)

    config = dict(DEFAULT_CHANNEL)
    if row and isinstance(row.value, dict):
        config.update({k: v for k, v in row.value.items() if k in DEFAULT_CHANNEL})
    if config["channel"] not in CHANNELS:
        config["channel"] = "stable"
    return config


async def save_channel(channel: str, ref: str = "", auto_offer: bool = True) -> dict:
    """Speichert die Kanal-Einstellung."""
    from .database import async_session
    from .models import Setting

    if channel not in CHANNELS:
        raise ValueError(f"Unbekannter Kanal '{channel}' (erlaubt: {', '.join(CHANNELS)}).")
    ref = (ref or "").strip()
    if channel == "pinned" and not ref:
        raise ValueError("Für eine festgelegte Version fehlt die Angabe, welche.")
    # Der Bezugspunkt geht als Argument an git und in GitHub-Adressen. Ein Wert
    # wie "--upload-pack=..." waere fuer git eine Option statt eines Namens.
    if ref and (not _GIT_REF_RE.match(ref) or ".." in ref or ref.endswith((".lock", "/", "."))):
        raise ValueError("Ungültige Versionsangabe (erlaubt: Zweig, Tag oder Commit).")

    config = {"channel": channel, "ref": ref, "auto_offer": bool(auto_offer)}
    async with async_session() as session:
        row = (await session.execute(
            select(Setting).where(Setting.key == CHANNEL_SETTING_KEY)
        )).scalar_one_or_none()
        if row:
            row.value = config
        else:
            session.add(Setting(key=CHANNEL_SETTING_KEY, value=config,
                                description="Welchen Stand dieser Server fahren soll"))
        await session.commit()

    # Der Zwischenspeicher gilt fuer den alten Bezugspunkt - verwerfen.
    global _remote_cache, _remote_cache_expires
    _remote_cache, _remote_cache_expires = None, 0.0
    bus.clear(UPDATE_AVAILABLE)
    logger.warning("Update-Kanal gesetzt: %s%s", channel, f" ({ref})" if ref else "")
    return config


async def resolve_target_ref(config: Optional[dict] = None, force: bool = False) -> str:
    """Welcher Git-Bezugspunkt gehoert zum eingestellten Kanal?

    `force`: die Release-Liste neu von GitHub holen. Sonst sieht "Auf Updates
    pruefen" ein gerade veroeffentlichtes Release bis zu 15 min lang nicht -
    die Liste lag im Zwischenspeicher, nur der Commit wurde neu abgefragt.
    """
    config = config or await load_channel()
    channel = config.get("channel", "stable")

    if channel == "edge":
        return REPO_BRANCH
    if channel == "pinned":
        return (config.get("ref") or "").strip() or REPO_BRANCH

    latest = await latest_release(force=force)
    return (latest or {}).get("tag") or REPO_BRANCH


# =============================================================================
# Releases
# =============================================================================
_releases_cache: Optional[list] = None
_releases_cache_expires: float = 0.0


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "RIMPERIO-Updater"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def list_releases(force: bool = False, limit: int = 30) -> dict:
    """Alle Releases des Repositories - fuer die Auswahl in der Oberflaeche.

    Hat das Repository (noch) keine Releases, werden ersatzweise die Tags
    gelistet. Ganz ohne beides bleibt der Zweig die einzige Wahl.
    """
    global _releases_cache, _releases_cache_expires

    channels = [{"id": key, **value} for key, value in CHANNELS.items()]

    if not force and _releases_cache is not None and time.time() < _releases_cache_expires:
        # Die Kanaele gehoeren IMMER dazu. Frueher fehlten sie in der Antwort
        # aus dem Zwischenspeicher - wer die Update-Seite ein zweites Mal
        # oeffnete, sah keine Kanalauswahl mehr, nur noch "Uebernehmen".
        return {"releases": _releases_cache, "cached": True, "repo": REPO_SLUG,
                "branch": REPO_BRANCH, "source": "cache", "error": None,
                "channels": channels}

    releases: list = []
    error = None
    source = "releases"

    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_github_headers()) as client:
            response = await client.get(f"{_API}/repos/{REPO_SLUG}/releases",
                                        params={"per_page": min(limit, 100)})
            if response.status_code == 403:
                error = ("GitHub hat die Anfrage abgelehnt (Limit für anonyme Abfragen). "
                         "GITHUB_TOKEN setzen oder später erneut versuchen.")
            elif response.status_code == 404:
                error = f"Repository nicht gefunden ({REPO_SLUG})."
            else:
                response.raise_for_status()
                for entry in response.json():
                    releases.append({
                        "tag": entry.get("tag_name"),
                        "name": entry.get("name") or entry.get("tag_name"),
                        "published_at": entry.get("published_at") or entry.get("created_at"),
                        "prerelease": bool(entry.get("prerelease")),
                        "draft": bool(entry.get("draft")),
                        "url": entry.get("html_url"),
                        "notes": (entry.get("body") or "").strip()[:4000],
                    })

            # Kein Release veroeffentlicht? Dann sind Tags der naechstbeste Massstab.
            if not releases and not error:
                source = "tags"
                tags = await client.get(f"{_API}/repos/{REPO_SLUG}/tags",
                                        params={"per_page": min(limit, 100)})
                if tags.status_code == 200:
                    for entry in tags.json():
                        releases.append({
                            "tag": entry.get("name"),
                            "name": entry.get("name"),
                            "published_at": None,
                            "prerelease": False,
                            "draft": False,
                            "url": f"https://github.com/{REPO_SLUG}/releases/tag/{entry.get('name')}",
                            "notes": "",
                        })
    except httpx.HTTPError as exc:
        error = f"GitHub nicht erreichbar: {exc}"
    except Exception as exc:                                        # defensiv
        error = f"Release-Abfrage fehlgeschlagen: {exc}"

    releases = [r for r in releases if r.get("tag") and not r.get("draft")]
    if not error:
        _releases_cache = releases
        _releases_cache_expires = time.time() + _REMOTE_CACHE_TTL

    return {
        "releases": releases,
        "source": source,
        "error": error,
        "cached": False,
        "repo": REPO_SLUG,
        "branch": REPO_BRANCH,
        "channels": channels,
    }


async def latest_release(force: bool = False) -> Optional[dict]:
    """Das neueste veroeffentlichte Release (ohne Prerelease)."""
    data = await list_releases(force=force)
    for entry in data.get("releases", []):
        if not entry.get("prerelease"):
            return entry
    releases = data.get("releases") or []
    return releases[0] if releases else None


# =============================================================================
# Beobachter: neuer Stand auf GitHub -> sofort an alle offenen Oberflaechen
# =============================================================================
"""
Der Wunsch: "sobald ich auf GitHub etwas pushe, sollen alle laufenden Server
das sofort mitbekommen".

Zwei Wege fuehren dahin, und beide enden im selben Ereignisverteiler:

1. GitHub-Webhook. Wirklich sofort - aber nur, wenn der Server aus dem Internet
   erreichbar ist. Einrichtung: die URL aus `webhook_info()` in den Repository-
   Einstellungen als Webhook eintragen, Ereignis "push", Secret aus der .env.
2. Eigener Beobachter. Fragt GitHub im kurzen Takt (Standard 120 s) und nutzt
   dabei ETags: unveraenderte Antworten kosten kein Abfragekontingent. Das ist
   der Weg fuer alle Server hinter einer Firewall.

Erst wenn wirklich etwas Neues da ist, gibt es ein Ereignis - kein Ereignis
pro Abfrage.
"""

WATCH_ENABLED = os.getenv("RIMPERIO_UPDATE_WATCH", "true").strip().lower() not in ("0", "false", "no")
WATCH_INTERVAL = max(30, int(os.getenv("RIMPERIO_UPDATE_WATCH_INTERVAL", "120")))

# Woran wir merken, dass sich etwas geaendert hat.
_last_announced_commit: Optional[str] = None


def webhook_info() -> dict:
    """Wie der GitHub-Webhook einzurichten ist (das Secret selbst bleibt geheim)."""
    secret = os.getenv("RIMPERIO_WEBHOOK_SECRET", "").strip()
    from . import public_url
    base = public_url.site_url().rstrip("/")
    return {
        "configured": bool(secret),
        "path": "/api/updates/webhook",
        "url": f"{base}/api/updates/webhook" if base else "",
        "content_type": "application/json",
        "events": ["push"],
        "hint": ("RIMPERIO_WEBHOOK_SECRET in der .env setzen und denselben Wert im "
                 "Repository unter Settings → Webhooks eintragen. Ohne Secret nimmt "
                 "der Server keine Webhook-Meldungen an."),
    }


async def announce_if_new(reason: str = "watcher", force_check: bool = True) -> dict:
    """Prueft den Stand und meldet ihn, falls er sich geaendert hat."""
    global _last_announced_commit

    status = await version_check(force=force_check)
    remote = status.get("remote") or {}
    commit = remote.get("commit")

    if not status.get("update_available"):
        # Wieder gleichauf (z.B. nach einem Update): den Hinweis zurueckziehen,
        # sonst haengt in jedem offenen Fenster ein Banner, das nicht mehr stimmt.
        if _last_announced_commit is not None:
            _last_announced_commit = None
            bus.clear(UPDATE_AVAILABLE)
            bus.publish("update.cleared", {"reason": status.get("reason")}, sticky=False)
        return {"announced": False, "reason": status.get("reason")}

    if commit and commit == _last_announced_commit:
        return {"announced": False, "reason": "Bereits gemeldet."}

    _last_announced_commit = commit
    payload = {
        "source": reason,
        "reason": status.get("reason"),
        "channel": status.get("channel", {}).get("channel"),
        "installed_version": (status.get("local") or {}).get("version"),
        "available_version": remote.get("version"),
        "commit": remote.get("commit_short"),
        "commit_message": remote.get("commit_message"),
        "commit_date": remote.get("commit_date"),
        "repo": REPO_SLUG,
    }
    bus.publish(UPDATE_AVAILABLE, payload)
    logger.warning("Neuer Stand auf GitHub gemeldet (%s): %s",
                   reason, remote.get("commit_short") or remote.get("version"))
    # Auch nach aussen (Mail/Telegram/Webhook), falls so eingestellt.
    from . import alerts
    await alerts.dispatch(
        "update_available", "Neuer Stand auf GitHub",
        (f"Installiert: {payload['installed_version'] or '?'}\n"
         f"Verfügbar:   {payload['available_version'] or payload['commit'] or '?'}\n"
         f"{payload['commit_message'] or ''}\n\n"
         f"Einspielen unter RIMPERIO-Server → Updates."),
        level="info", key=f"update:{commit}", detail=payload)
    return {"announced": True, **payload}


async def watch_task() -> None:
    """Hintergrundaufgabe: haelt Ausschau nach einem neuen Stand."""
    if not WATCH_ENABLED:
        logger.info("Update-Beobachter ist abgeschaltet (RIMPERIO_UPDATE_WATCH=false).")
        return

    # Kurz warten, damit Datenbank und Migrationen zuerst durchlaufen.
    await asyncio.sleep(20)
    logger.info("Update-Beobachter läuft (alle %ss)", WATCH_INTERVAL)

    while True:
        try:
            await announce_if_new(reason="watcher")
        except Exception as exc:                                    # defensiv
            logger.warning("Update-Beobachter: %s", exc)
        await asyncio.sleep(WATCH_INTERVAL)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Prueft die GitHub-Signatur (`X-Hub-Signature-256`).

    Ohne gesetztes Secret nimmt der Server gar keine Meldung an: ein offener
    Endpunkt waere sonst eine Einladung, den Server im Takt Anfragen an GitHub
    schicken zu lassen.
    """
    secret = os.getenv("RIMPERIO_WEBHOOK_SECRET", "").strip()
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())
