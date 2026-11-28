# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Docker-Compose-Stacks auf Rechnern mit Agent:
#               Compose-Datei (YAML) und .env im Browser bearbeiten, pruefen,
#               sichern und neu ausrollen - wie die Stacks in Portainer.
# ==============================================================================
"""
Stacks auf einem Rechner
========================

Ein *Stack* ist ein Compose-Projekt (`docker compose ls`). Seine Dateien liegen
auf dem Rechner, nicht bei RIMPERIO. Alles laeuft ueber den vorhandenen Agent
als kurzer Shell-Auftrag (der Agent ist in der Gruppe docker):

* **Liste**     `docker compose ls -a --format json` - Name, Zustand, Dateien.
* **Lesen**     Compose-Dateien und `.env` des Projekts (Base64, damit kein
                Zeichen verloren geht), dazu `docker compose ps`.
* **Speichern** 1. neuer Inhalt in eine Temp-Datei, 2. `docker compose config -q`
                mit genau dieser Datei (anstelle der alten) - kaputtes YAML oder
                ein unbekannter Schluessel kommt so nie auf die Platte, 3. alte
                Datei als `<datei>.rimperio-<zeit>.bak` daneben, 4. schreiben.
                Gehoert die Datei root, schreibt ein Wegwerf-Container (die Gruppe
                docker darf das ohnehin).
* **Ausrollen** auf Wunsch `docker compose pull` und `up -d --remove-orphans`.
                Schlaegt das fehl, kommt automatisch die alte Datei zurueck und
                der alte Stand wird wieder gestartet.

Linux-Rechner mit `docker compose` (v2). Docker Desktop unter Windows: nicht
unterstuetzt (andere Shell, andere Pfade).
"""

from __future__ import annotations

import asyncio
import base64
import json
import posixpath
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from . import agentwake
from .models import Agent, Command

_PROJECT_OK = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,100}$")
_PATH_OK = re.compile(r"^/[^\0\n'`$\\]{1,1000}$")
MAX_FILE = 512 * 1024


class ComposeError(Exception):
    pass


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


async def _run(db: AsyncSession, agent: Agent, user: str, script: str, source: str,
               timeout: int = 120, wait: float = 90.0) -> Command:
    """Auftrag einreihen und auf das Ergebnis warten (der Agent holt live ab)."""
    if not agent.agent_uid:
        raise ComposeError("Auf diesem Gerät läuft kein RIMPERIO-Agent.")
    if (agent.platform or "linux") != "linux":
        raise ComposeError("Stacks gehen bisher nur auf Linux-Rechnern mit Docker.")
    cmd = Command(agent_id=agent.id, shell="sh", command=script, timeout=timeout, kind="shell",
                  source=source[:128], created_by=user[:64])
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    agentwake.notify(agent.id)
    deadline = asyncio.get_event_loop().time() + wait
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.0)
        await db.refresh(cmd)
        if cmd.status not in ("pending", "running"):
            return cmd
    raise ComposeError("Der Rechner hat nicht rechtzeitig geantwortet (offline oder beschäftigt). "
                       "Der Auftrag bleibt eingereiht – später erneut versuchen.")


def _markers(text: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for line in (text or "").splitlines():
        if line.startswith("@@") and "=" in line:
            k, v = line[2:].split("=", 1)
            out.setdefault(k.lower(), []).append(v)
    return out


def _fail_text(cmd: Command, m: Dict[str, List[str]]) -> str:
    if m.get("fail"):
        return m["fail"][-1]
    return (cmd.stderr or cmd.stdout or f"Befehl {cmd.status}").strip()[-600:]


LIST_SCRIPT = r"""command -v docker >/dev/null 2>&1 || { echo "@@FAIL=docker fehlt auf diesem Rechner."; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "@@FAIL=docker compose (v2) fehlt – bitte das Compose-Plugin installieren."; exit 2; }
echo "@@LIST=$(docker compose ls -a --format json 2>/dev/null | tr -d '\n')"
"""


def _parse_list(raw: str) -> List[dict]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for p in data if isinstance(data, list) else []:
        files = [f.strip() for f in str(p.get("ConfigFiles") or "").split(",") if f.strip()]
        out.append({"name": p.get("Name", ""), "status": p.get("Status", ""), "files": files,
                    "dir": posixpath.dirname(files[0]) if files else ""})
    return sorted(out, key=lambda x: x["name"])


async def list_stacks(db: AsyncSession, agent: Agent, user: str) -> List[dict]:
    cmd = await _run(db, agent, user, LIST_SCRIPT, "Stacks auflisten", timeout=60)
    m = _markers(cmd.stdout or "")
    if cmd.status != "done" or m.get("fail"):
        raise ComposeError(_fail_text(cmd, m))
    return _parse_list((m.get("list") or ["[]"])[-1])


async def _stack(db: AsyncSession, agent: Agent, user: str, project: str) -> dict:
    if not _PROJECT_OK.match(project or ""):
        raise ComposeError("Ungültiger Stack-Name.")
    st = next((s for s in await list_stacks(db, agent, user) if s["name"] == project), None)
    if st is None:
        raise ComposeError(f"Stack „{project}“ gibt es auf {agent.hostname} nicht (mehr).")
    if not st["files"]:
        raise ComposeError("Docker nennt für diesen Stack keine Compose-Datei.")
    for f in st["files"]:
        if not _PATH_OK.match(f):
            raise ComposeError(f"Pfad wird nicht unterstützt: {f}")
    return st


async def read_stack(db: AsyncSession, agent: Agent, user: str, project: str) -> dict:
    st = await _stack(db, agent, user, project)
    paths = st["files"] + [posixpath.join(st["dir"], ".env")]
    lines = ["set -u"]
    for p in paths:
        lines.append(f"""F={_q(p)}
if [ -f "$F" ]; then
  if [ -r "$F" ]; then echo "@@FILE=$F|$(wc -c < "$F" | tr -d ' ')|$(base64 -w0 < "$F" 2>/dev/null || base64 < "$F" | tr -d '\\n')"
  else echo "@@FILE=$F|-1|"; fi
fi""")
    lines.append(f'echo "@@WRITABLE=$( [ -w {_q(st["dir"])} ] && echo 1 || echo 0)"')
    lines.append(f'echo "@@PS=$(docker compose -p {_q(project)} ps -a --format json 2>/dev/null | tr -d \'\\n\')"')
    cmd = await _run(db, agent, user, "\n".join(lines), f"Stack {project} lesen", timeout=60)
    m = _markers(cmd.stdout or "")
    if cmd.status != "done":
        raise ComposeError(_fail_text(cmd, m))
    files = []
    for entry in m.get("file", []):
        path, size, b64 = (entry.split("|", 2) + ["", ""])[:3]
        item = {"path": path, "name": posixpath.basename(path), "kind": "env" if path.endswith("/.env") else "compose",
                "readable": size != "-1", "size": int(size) if size.lstrip("-").isdigit() else 0, "content": ""}
        if item["readable"]:
            try:
                item["content"] = base64.b64decode(b64).decode("utf-8", errors="replace")
            except ValueError:
                item["readable"] = False
        files.append(item)
    ps_raw = (m.get("ps") or [""])[-1]
    services = []
    # Je nach Compose-Version: eine JSON-Liste oder ein Objekt je Zeile.
    try:
        parsed = json.loads(ps_raw) if ps_raw.startswith("[") else [json.loads(x) for x in re.findall(r"\{.*?\}(?=\{|$)", ps_raw)]
    except ValueError:
        parsed = []
    for c in parsed if isinstance(parsed, list) else []:
        services.append({"service": c.get("Service"), "name": c.get("Name"), "image": c.get("Image"),
                         "state": c.get("State"), "status": c.get("Status"), "health": c.get("Health")})
    inv = {c.get("name"): c for c in ((agent.docker_inventory or {}).get("containers") or [])}
    for s in services:
        c = inv.get(s["name"]) or {}
        s["update_available"] = bool(c.get("update_available"))
        s["remote_version"] = c.get("remote_version")
    return {**st, "files_content": files, "services": services,
            "writable": (m.get("writable") or ["0"])[-1] == "1"}


def _write_block(path_var: str, tmp_var: str) -> str:
    """Datei ersetzen: direkt oder, wenn sie root gehoert, ueber einen Wegwerf-Container."""
    return f"""D="$(dirname "${path_var}")"; B="$(basename "${path_var}")"
if [ -f "${path_var}" ]; then cp -p "${path_var}" "${path_var}.rimperio-$TS.bak" 2>/dev/null || \\
  docker run --rm -v "$D:/w" alpine:3 cp -p "/w/$B" "/w/$B.rimperio-$TS.bak" || {{ echo "@@FAIL=Sicherung der alten Datei fehlgeschlagen."; exit 4; }}
  echo "@@BACKUP=${path_var}.rimperio-$TS.bak"
fi
if {{ [ -f "${path_var}" ] && [ -w "${path_var}" ]; }} || {{ [ ! -f "${path_var}" ] && [ -w "$D" ]; }}; then
  cat "${tmp_var}" > "${path_var}" || {{ echo "@@FAIL=Schreiben fehlgeschlagen."; exit 4; }}
else
  docker run --rm -i -v "$D:/w" alpine:3 sh -c "cat > '/w/$B'" < "${tmp_var}" || {{ echo "@@FAIL=Schreiben fehlgeschlagen (auch über Docker)."; exit 4; }}
fi
"""


async def save_stack(db: AsyncSession, agent: Agent, user: str, project: str, path: str, content: str,
                     deploy: bool = False, pull: bool = False, rollback: bool = True) -> dict:
    st = await _stack(db, agent, user, project)
    env_path = posixpath.join(st["dir"], ".env")
    if path not in st["files"] and path != env_path:
        raise ComposeError("Diese Datei gehört nicht zum Stack.")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE:
        raise ComposeError("Datei zu groß (höchstens 512 KB).")
    b64 = base64.b64encode(data).decode()
    files_args = " ".join("-f " + ('"$TMP"' if f == path else _q(f)) for f in st["files"])
    files_now = " ".join("-f " + _q(f) for f in st["files"])
    env_arg = '--env-file "$TMP"' if path == env_path else ""
    script = f"""set -u
TS="$(date +%Y%m%d-%H%M%S)"
P={_q(project)}; DIR={_q(st["dir"])}; F={_q(path)}
TMP="$(mktemp)"; trap 'rm -f "$TMP" "$TMP.err"' EXIT
printf '%s' {_q(b64)} | base64 -d > "$TMP" || {{ echo "@@FAIL=Inhalt nicht lesbar."; exit 3; }}
# 1) pruefen - mit genau dieser Datei anstelle der alten
if ! docker compose -p "$P" --project-directory "$DIR" {files_args} {env_arg} config -q 2> "$TMP.err"; then
  echo "@@FAIL=Prüfung fehlgeschlagen: $(tail -c 800 "$TMP.err" | tr '\\n' ' ')"; exit 3
fi
echo "@@VALID=1"
# 2) sichern und schreiben
{_write_block("F", "TMP")}echo "@@SAVED=1"
"""
    if deploy:
        script += f"""
cd "$DIR" || exit 5
"""
        if pull:
            script += f"""docker compose -p "$P" {files_now} pull 2>&1 | tail -n 30 | sed 's/^/@@OUT=/'
"""
        script += f"""if docker compose -p "$P" {files_now} up -d --remove-orphans > "$TMP.err" 2>&1; then
  tail -n 40 "$TMP.err" | sed 's/^/@@OUT=/'; echo "@@DEPLOYED=1"
else
  tail -n 40 "$TMP.err" | sed 's/^/@@OUT=/'
"""
        if rollback:
            script += f"""  BK="$F.rimperio-$TS.bak"
  if [ -f "$BK" ]; then
    cat "$BK" > "$TMP" && {{ cat "$TMP" > "$F" 2>/dev/null || docker run --rm -i -v "$DIR:/w" alpine:3 sh -c "cat > '/w/$(basename "$F")'" < "$TMP"; }}
    docker compose -p "$P" {files_now} up -d --remove-orphans 2>&1 | tail -n 20 | sed 's/^/@@OUT=/'
    echo "@@ROLLEDBACK=1"
  fi
"""
        script += """  echo "@@FAIL=Ausrollen fehlgeschlagen (Ausgabe unten)."; exit 6
fi
"""
    cmd = await _run(db, agent, user, script, f"Stack {project}: {posixpath.basename(path)} speichern"
                     + (" + ausrollen" if deploy else ""), timeout=1800, wait=600 if deploy else 90)
    m = _markers(cmd.stdout or "")
    return {"ok": cmd.status == "done" and not m.get("fail"), "valid": bool(m.get("valid")),
            "saved": bool(m.get("saved")), "deployed": bool(m.get("deployed")),
            "rolled_back": bool(m.get("rolledback")), "backup": (m.get("backup") or [None])[-1],
            "message": _fail_text(cmd, m) if (m.get("fail") or cmd.status != "done") else
            ("Gespeichert und ausgerollt." if m.get("deployed") else "Gespeichert."),
            "output": m.get("out", [])[-80:], "at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}


async def stack_action(db: AsyncSession, agent: Agent, user: str, project: str, action: str) -> dict:
    """up | pull_up | restart | stop | down (ohne Volumes)"""
    st = await _stack(db, agent, user, project)
    files_now = " ".join("-f " + _q(f) for f in st["files"])
    steps = {"up": "up -d --remove-orphans", "restart": "restart", "stop": "stop", "down": "down"}
    body = f"cd {_q(st['dir'])} || exit 5\n"
    if action == "pull_up":
        body += f"docker compose -p {_q(project)} {files_now} pull 2>&1 | tail -n 30 | sed 's/^/@@OUT=/'\n"
        action_cmd = steps["up"]
    elif action in steps:
        action_cmd = steps[action]
    else:
        raise ComposeError("Aktion: up, pull_up, restart, stop oder down.")
    body += f"""if docker compose -p {_q(project)} {files_now} {action_cmd} > /tmp/rimperio-compose.$$ 2>&1; then rc=0; else rc=$?; fi
tail -n 40 /tmp/rimperio-compose.$$ | sed 's/^/@@OUT=/'; rm -f /tmp/rimperio-compose.$$
[ $rc -eq 0 ] || {{ echo "@@FAIL=docker compose {action_cmd} fehlgeschlagen (rc $rc)."; exit 6; }}
"""
    cmd = await _run(db, agent, user, body, f"Stack {project}: {action}", timeout=1800, wait=600)
    m = _markers(cmd.stdout or "")
    ok = cmd.status == "done" and not m.get("fail")
    return {"ok": ok, "message": "Erledigt." if ok else _fail_text(cmd, m), "output": m.get("out", [])[-80:]}


async def backups(db: AsyncSession, agent: Agent, user: str, project: str) -> List[dict]:
    st = await _stack(db, agent, user, project)
    script = f"""ls -1t {_q(st['dir'])} 2>/dev/null | grep '\\.rimperio-[0-9-]*\\.bak$' | head -n 30 | while read -r n; do
  echo "@@BAK={st['dir']}/$n|$(wc -c < {_q(st['dir'])}/"$n" | tr -d ' ')"; done
"""
    cmd = await _run(db, agent, user, script, f"Stack {project}: Sicherungen", timeout=60)
    m = _markers(cmd.stdout or "")
    out = []
    for e in m.get("bak", []):
        p, size = (e.split("|", 1) + ["0"])[:2]
        out.append({"path": p, "name": posixpath.basename(p), "size": int(size) if size.isdigit() else 0})
    return out
