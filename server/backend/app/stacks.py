# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Zusatzdienste verwalten (fremde Container, eigene Profile)
# ==============================================================================
"""
Die fuenf Zusatzdienste aus `server/deploy/optional.yml` ein- und ausschalten und ihre
Zugangsdaten anzeigen.

Alle fuenf sind **fremde** Images und heissen deshalb `rimperio-ext-*`. Ihre
Updates laufen nicht ueber RIMPERIOs Patchmanagement, sondern unter
*System -> Container*.

Wie das laeuft: In der `.env` auf dem Host steht `COMPOSE_PROFILES`. Wird dort
ein Profil ergaenzt oder gestrichen und danach `docker compose up -d` gestartet,
kommt der Dienst dazu bzw. verschwindet. Genau das macht dieses Modul - ueber
`hostexec`, weil Docker auf dem Host laeuft und nicht im Backend-Container.

Zugangsdaten: Portainer und n8n brauchen ein Passwort. Es steht in der `.env`,
wird beim Installieren ausgewuerfelt und ist in der Oberflaeche unter
*System -> Zusatzdienste* fuer Administratoren einsehbar. Das ist der ganze
Zweck der Uebung: Passwoerter, die man nur beim Installieren einmal sieht, sind
zwei Wochen spaeter verloren.

Ohne Host-Zugriff (gehaertetes Compose) laesst sich hier nichts schalten. Dann
sagt das die Oberflaeche auch so, statt einen Knopf anzubieten, der nichts tut.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Dict, List, Optional

from . import hostexec

logger = logging.getLogger("rimperio.stacks")

# Zeichen, die in einem Profilnamen vorkommen duerfen. Alles andere landet nie
# in einer Kommandozeile.
_PROFILE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")

STACKS: Dict[str, dict] = {
    "portainer": {
        "label": "Portainer",
        "hint": "Container-Oberfläche im Browser: Zustände, Protokolle, Konsole.",
        "container": "rimperio-ext-portainer",
        "legacy_container": "rimperio-portainer",
        "default_port": 9000,
        "url_template": "http://{host}:{port}",
        "credentials": {
            "user": "admin",
            "password_env": "PORTAINER_ADMIN_PASSWORD",
        },
        "requirements": {"ram_mb": 256, "disk_mb": 400},
        "warning": ("Portainer bekommt den Docker-Socket lesend. Wer die Oberfläche "
                    "erreicht, sieht damit alle Container dieses Servers."),
    },
    "tugtainer": {
        "label": "Tugtainer (Image-Update-Prüfer)",
        "hint": ("Sieht nach, ob für die fremden Images Updates bereitliegen, und "
                 "meldet sie. Eingespielt wird auf Ansage — hier oder unter "
                 "System → Container."),
        "container": "rimperio-ext-tugtainer",
        "default_port": 9412,
        "url_template": "http://{host}:{port}",
        "credentials": None,
        # Ohne gesetzten Wert startet der Container nicht. Wird beim Einschalten
        # ausgewuerfelt, damit niemand an einer kryptischen Compose-Meldung
        # haengenbleibt.
        "required_env": ["TUGTAINER_AGENT_SECRET"],
        "requirements": {"ram_mb": 256, "disk_mb": 300},
        "warning": ("Tugtainer bekommt den Docker-Socket nur lesend: er kann prüfen, "
                    "aber nichts austauschen. Wer auch das will, setzt "
                    "TUGTAINER_SOCKET_MODE=rw in der .env — und weiß, worauf er sich "
                    "einlässt."),
        "replaces": "watchtower",
    },
    "n8n": {
        "label": "n8n",
        "hint": "Automatisierung: Workflows, Benachrichtigungen, KI-Auswertung.",
        "container": "rimperio-ext-n8n",
        "legacy_container": "rimperio-n8n",
        "default_port": 5678,
        "url_template": "http://{host}:{port}",
        "credentials": {
            "user_env": "N8N_USER",
            "user": "admin",
            "password_env": "N8N_PASSWORD",
        },
        "requirements": {"ram_mb": 768, "disk_mb": 1200},
        "warning": "",
    },
    "openwebui": {
        "label": "Open WebUI (KI-Oberfläche)",
        "hint": ("Spricht dieselbe Sprache wie OpenAI, hat dahinter aber das Modell, "
                 "das man selbst wählt — auch ein lokales über Ollama. Als Anbieter "
                 "unter System → KI-Auswertung auswählbar."),
        "container": "rimperio-ext-openwebui",
        "default_port": 3000,
        "url_template": "http://{host}:{port}",
        "credentials": None,
        "required_env": ["OPENWEBUI_SECRET_KEY"],
        "requirements": {"ram_mb": 1024, "disk_mb": 2500},
        "warning": ("Open WebUI selbst ist genügsam. Das Sprachmodell dahinter ist es "
                    "nicht: Ollama auf demselben Server will mehrere Gigabyte "
                    "Arbeitsspeicher. Deshalb ist Ollama bewusst NICHT Teil dieses "
                    "Stacks — OLLAMA_BASE_URL zeigt auf eine vorhandene Installation."),
    },
    "postfix": {
        "label": "Postfix (Mailversand)",
        "hint": "Damit der Server Mails verschicken kann. Eingestellt unter System → Mail.",
        "container": "rimperio-ext-postfix",
        "legacy_container": "rimperio-postfix",
        "default_port": 1587,
        "credentials": None,
        "requirements": {"ram_mb": 128, "disk_mb": 200},
        "warning": "",
    },
}

# Watchtower ist raus (siehe server/deploy/optional.yml). Wer es noch laufen hat, soll
# das erfahren statt es stillschweigend weiterlaufen zu lassen: es tauscht
# Container eigenmaechtig aus, und genau das will man auf einem Log-Server
# nicht.
RETIRED = {
    "watchtower": {
        "label": "Watchtower",
        "container": "rimperio-watchtower",
        "successor": "tugtainer",
        "reason": ("Watchtower hat Container von selbst ausgetauscht — nachts, ohne "
                   "Ansage und hinterher schwer zuzuordnen. Tugtainer nimmt seinen "
                   "Platz ein und macht nur eines: nachsehen und melden."),
    },
}


# =============================================================================
# .env auf dem Host lesen und schreiben
# =============================================================================
def _env_path() -> str:
    return f"{hostexec.host_paths()['install_dir']}/.env"


async def read_env() -> Dict[str, str]:
    """Liest die .env vom Host als Zuordnung."""
    raw = await hostexec.read_host_file(_env_path())
    values: Dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


async def write_env_values(updates: Dict[str, str]) -> bool:
    """Setzt Werte in der .env. Vorhandene Zeilen werden ersetzt, neue angehaengt.

    Bewusst zeilenweise statt "Datei neu schreiben": in der .env stehen
    Kommentare und Werte, die niemand hier kennt (eigene Anpassungen des
    Betreibers). Die duerfen nicht verschwinden, nur weil ein Profil dazukommt.
    """
    raw = await hostexec.read_host_file(_env_path())
    if raw is None:
        return False

    lines = raw.splitlines()
    remaining = dict(updates)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[index] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# --- Zusatzdienste (System -> Zusatzdienste) ---")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")

    result = await hostexec.write_host_file(_env_path(), "\n".join(lines) + "\n", mode="0600")
    return result.ok


# =============================================================================
# Profile
# =============================================================================
def _parse_profiles(value: str) -> List[str]:
    return [p.strip() for p in re.split(r"[,\s]+", value or "") if p.strip()]


async def active_profiles(env: Optional[Dict[str, str]] = None) -> List[str]:
    env = env if env is not None else await read_env()
    return _parse_profiles(env.get("COMPOSE_PROFILES", ""))


async def compose_files(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Welche Compose-Dateien gelten? Ohne Angabe nur die Hauptdatei."""
    env = env if env is not None else await read_env()
    configured = env.get("COMPOSE_FILE", "")
    if configured:
        return [part for part in configured.split(":") if part.strip()]
    return ["docker-compose.yml"]


async def _running_containers() -> Dict[str, str]:
    """Welche der bekannten Container laufen gerade — und in welchem Zustand?"""
    if not hostexec.nsenter_available():
        return {}
    result = await hostexec.run_host(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"],
        timeout=20.0)
    if not result.ok:
        return {}

    states: Dict[str, str] = {}
    wanted = {spec["container"] for spec in STACKS.values()}
    # Container aus einer aelteren Installation heissen noch anders - sie sollen
    # trotzdem als "laeuft" erkannt werden, sonst meldet die Uebersicht nach dem
    # Update faelschlich "nicht vorhanden".
    wanted |= {spec["legacy_container"] for spec in STACKS.values()
               if spec.get("legacy_container")}
    wanted |= {spec["container"] for spec in RETIRED.values()}
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip() in wanted:
            states[parts[0].strip()] = parts[1].strip()
    return states


# =============================================================================
# Uebersicht
# =============================================================================
async def overview(host_hint: str = "") -> dict:
    """Was ist eingeschaltet, was laeuft, wie kommt man hin?"""
    if not hostexec.nsenter_available():
        return {
            "available": False,
            "reason": ("Kein Zugriff auf den Host. Zusatzdienste lassen sich nur "
                       "schalten, wenn das Backend mit privileged/pid:host läuft "
                       "(Standard-docker-compose.yml). Auf der Kommandozeile geht es "
                       "weiterhin: COMPOSE_PROFILES in der .env setzen und "
                       "'docker compose up -d' ausführen."),
            "stacks": [],
        }

    env = await read_env()
    enabled = await active_profiles(env)
    running = await _running_containers()
    host = host_hint or "SERVER-IP"

    items = []
    for key, spec in STACKS.items():
        state = running.get(spec["container"], "")
        if not state and spec.get("legacy_container"):
            state = running.get(spec["legacy_container"], "")
        port = spec.get("default_port")
        if port and (custom := env.get(f"{key.upper()}_PORT")):
            try:
                port = int(custom)
            except ValueError:
                pass

        items.append({
            "id": key,
            "label": spec["label"],
            "hint": spec["hint"],
            "warning": spec.get("warning", ""),
            "enabled": key in enabled,
            "container": spec["container"],
            "state": state or "nicht vorhanden",
            "running": state.lower() == "running",
            "url": (spec["url_template"].format(host=host, port=port)
                    if spec.get("url_template") and port else ""),
            "has_credentials": bool(spec.get("credentials")),
            "requirements": spec["requirements"],
        })

    # Laeuft noch etwas, das es nicht mehr geben sollte?
    retired = []
    for key, spec in RETIRED.items():
        state = running.get(spec["container"], "")
        if state or key in enabled:
            retired.append({
                "id": key, "label": spec["label"], "container": spec["container"],
                "state": state or "im Profil eingetragen",
                "running": state.lower() == "running",
                "successor": spec["successor"], "reason": spec["reason"],
            })

    return {
        "available": True,
        "stacks": items,
        "retired": retired,
        "compose_files": await compose_files(env),
        "profiles": enabled,
        "optional_file": "server/deploy/optional.yml",
    }


async def credentials(stack: str) -> dict:
    """Die Zugangsdaten eines Dienstes — nur fuer Administratoren gedacht."""
    spec = STACKS.get(stack)
    if not spec:
        raise ValueError(f"Unbekannter Zusatzdienst '{stack}'.")
    if not spec.get("credentials"):
        return {"stack": stack, "has_credentials": False,
                "message": f"{spec['label']} kennt keine eigenen Zugangsdaten."}
    if not hostexec.nsenter_available():
        raise RuntimeError("Kein Zugriff auf den Host — die .env ist nicht lesbar.")

    env = await read_env()
    config = spec["credentials"]
    user = env.get(config.get("user_env", ""), "") or config.get("user", "")
    password = env.get(config["password_env"], "")

    return {
        "stack": stack,
        "label": spec["label"],
        "has_credentials": True,
        "user": user,
        "password": password,
        "password_set": bool(password),
        "env_key": config["password_env"],
        "note": ("Das Passwort steht in der .env auf dem Server. Wird es hier geändert, "
                 "greift die Änderung erst, nachdem der Dienst neu gestartet wurde."),
    }


async def set_password(stack: str, password: str = "") -> dict:
    """Setzt ein neues Passwort. Ohne Angabe wird eines ausgewuerfelt."""
    spec = STACKS.get(stack)
    if not spec or not spec.get("credentials"):
        raise ValueError(f"Für '{stack}' gibt es kein Passwort zu setzen.")

    password = (password or "").strip() or secrets.token_urlsafe(18)
    if len(password) < 12:
        raise ValueError("Das Passwort muss mindestens 12 Zeichen haben.")

    key = spec["credentials"]["password_env"]
    if not await write_env_values({key: password}):
        raise RuntimeError("Die .env auf dem Host konnte nicht geschrieben werden.")

    # Portainer liest sein Startpasswort aus einer Datei, nicht aus der Umgebung.
    if stack == "portainer":
        target = f"{hostexec.host_paths()['install_dir']}/data/portainer_password"
        written = await hostexec.write_host_file(target, password, mode="0600")
        if not written.ok:
            raise RuntimeError("Die Passwortdatei für Portainer konnte nicht "
                               "geschrieben werden.")

    logger.warning("Passwort für Zusatzdienst '%s' neu gesetzt", stack)
    return {"stack": stack, "password": password, "env_key": key,
            "note": ("Damit es gilt, muss der Dienst neu gestartet werden — "
                     "der Knopf 'Neu starten' erledigt das.")}


# =============================================================================
# Ein- und ausschalten
# =============================================================================
async def _compose(args: List[str], timeout: float = 300.0) -> hostexec.HostResult:
    install_dir = hostexec.host_paths()["install_dir"]
    quoted = " ".join(f"'{a}'" for a in args)
    return await hostexec.run_host(
        ["sh", "-c", f"cd '{install_dir}' && docker compose {quoted} 2>&1"],
        timeout=timeout)


async def _ensure_optional_file_listed(env: Dict[str, str]) -> None:
    """Sorgt dafuer, dass server/deploy/optional.yml ueberhaupt gelesen wird.

    Ohne diesen Eintrag kennt Compose die vier Dienste gar nicht - ein Profil
    einzuschalten waere dann wirkungslos, und zwar stillschweigend.
    """
    files = await compose_files(env)
    if "server/deploy/optional.yml" in files:
        return
    if "docker-compose.yml" not in files:
        files.insert(0, "docker-compose.yml")
    files.append("server/deploy/optional.yml")
    await write_env_values({"COMPOSE_FILE": ":".join(files)})


async def toggle(stack: str, enable: bool) -> dict:
    """Schaltet einen Zusatzdienst ein oder aus und wendet es sofort an."""
    if stack in RETIRED and enable:
        spec = RETIRED[stack]
        raise ValueError(f"{spec['label']} gibt es hier nicht mehr. {spec['reason']} "
                         f"Nachfolger: {spec['successor']}.")
    if stack in RETIRED and not enable:
        return await _remove_retired(stack)
    if stack not in STACKS:
        raise ValueError(f"Unbekannter Zusatzdienst '{stack}'.")
    if not _PROFILE_PATTERN.match(stack):
        raise ValueError("Unzulässiger Profilname.")
    if not hostexec.nsenter_available():
        raise RuntimeError(
            "Kein Zugriff auf den Host. Zusatzdienste lassen sich nur schalten, wenn "
            "das Backend mit privileged/pid:host läuft.")

    env = await read_env()
    profiles = await active_profiles(env)
    spec = STACKS[stack]

    if enable:
        # Fehlt ein Pflicht-Passwort, startet der Container nicht und Compose
        # bricht mit einer kryptischen Meldung ab. Lieber vorher eines setzen.
        config = spec.get("credentials")
        if config and not env.get(config["password_env"]):
            await set_password(stack)

        # Dienste ohne eigenes Passwort, die trotzdem ein Geheimnis brauchen
        # (Open WebUI, Tugtainer): fehlt es, startet der Container gar nicht
        # erst - mit einer Meldung, die niemandem weiterhilft. Also hier
        # auswuerfeln.
        missing = {name: secrets.token_urlsafe(32)
                   for name in spec.get("required_env", []) if not env.get(name)}
        if missing:
            if not await write_env_values(missing):
                raise RuntimeError("Die .env auf dem Host konnte nicht geschrieben werden.")
            logger.warning("Geheimnis für '%s' erzeugt: %s", stack, ", ".join(missing))

        await _ensure_optional_file_listed(await read_env())
        if stack not in profiles:
            profiles.append(stack)
    else:
        profiles = [p for p in profiles if p != stack]

    if not await write_env_values({"COMPOSE_PROFILES": ",".join(profiles)}):
        raise RuntimeError("Die .env auf dem Host konnte nicht geschrieben werden.")

    if enable:
        result = await _compose(["--profile", stack, "up", "-d", stack])
        action = "gestartet"
    else:
        # `down` waere zu grob - das nimmt den ganzen Stack mit. Nur diesen
        # einen Container stoppen und entfernen.
        result = await _compose(["--profile", stack, "rm", "-sf", stack], timeout=120.0)
        action = "gestoppt"

    output = (result.stdout or result.stderr or "").strip()[-2000:]
    if not result.ok:
        logger.error("Zusatzdienst '%s' %s fehlgeschlagen: %s", stack, action, output)
        raise RuntimeError(f"{spec['label']} konnte nicht {action} werden: {output}")

    logger.warning("Zusatzdienst '%s' %s", stack, action)
    return {"stack": stack, "enabled": enable, "action": action, "output": output}


async def _remove_retired(stack: str) -> dict:
    """Raeumt einen abgeloesten Dienst weg (derzeit: Watchtower).

    Compose kennt den Dienst nach dem Update gar nicht mehr, ein
    `compose rm` liefe also ins Leere. Deshalb direkt ueber Docker - und das
    Profil aus der .env streichen, damit er nicht beim naechsten `up` wieder
    auftaucht.
    """
    spec = RETIRED[stack]
    env = await read_env()
    profiles = [p for p in await active_profiles(env) if p != stack]
    await write_env_values({"COMPOSE_PROFILES": ",".join(profiles)})

    result = await hostexec.run_host(
        ["sh", "-c", f"docker rm -f '{spec['container']}' 2>&1 || true"], timeout=120.0)
    logger.warning("Abgeloester Dienst '%s' entfernt", stack)
    return {"stack": stack, "enabled": False, "action": "entfernt",
            "output": (result.stdout or result.stderr or "").strip()[-2000:],
            "message": f"{spec['label']} wurde entfernt. Nachfolger: {spec['successor']}."}


async def restart(stack: str) -> dict:
    """Startet einen Zusatzdienst neu (z.B. nach einer Passwortaenderung)."""
    if stack not in STACKS:
        raise ValueError(f"Unbekannter Zusatzdienst '{stack}'.")
    if not hostexec.nsenter_available():
        raise RuntimeError("Kein Zugriff auf den Host.")

    result = await _compose(["--profile", stack, "up", "-d", "--force-recreate", stack])
    output = (result.stdout or result.stderr or "").strip()[-2000:]
    if not result.ok:
        raise RuntimeError(f"Neustart fehlgeschlagen: {output}")
    return {"stack": stack, "restarted": True, "output": output}


async def logs(stack: str, lines: int = 200) -> List[str]:
    """Die letzten Protokollzeilen eines Zusatzdienstes."""
    spec = STACKS.get(stack)
    if not spec:
        raise ValueError(f"Unbekannter Zusatzdienst '{stack}'.")
    if not hostexec.nsenter_available():
        return []
    result = await hostexec.run_host(
        ["docker", "logs", "--tail", str(max(10, min(lines, 2000))), spec["container"]],
        timeout=30.0)
    return hostexec.split_lines((result.stdout or "") + (result.stderr or ""), limit=lines)


# =============================================================================
# Reicht die Maschine?
# =============================================================================
async def capacity_check(wanted: List[str]) -> dict:
    """Haelt der Server das aus, was ausgewaehlt wurde?

    Dieselbe Rechnung wie im Installer, nur zur Laufzeit: RIMPERIO selbst als
    Grundlast, dazu jeder gewaehlte Zusatzdienst. Das Ergebnis ist eine
    Einschaetzung, keine Garantie - deshalb blockiert es nichts, sondern warnt.
    """
    base = {"ram_mb": 1024, "disk_mb": 4096}
    needed = dict(base)
    for key in wanted:
        spec = STACKS.get(key)
        if not spec:
            continue
        needed["ram_mb"] += spec["requirements"]["ram_mb"]
        needed["disk_mb"] += spec["requirements"]["disk_mb"]

    have = {"ram_mb": 0, "disk_mb": 0, "cpus": 0}
    if hostexec.nsenter_available():
        script = ("awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo; echo '---'; "
                  "df -Pm / | awk 'NR==2 {print $4}'; echo '---'; nproc")
        result = await hostexec.run_host(["sh", "-c", script], timeout=15.0)
        if result.ok:
            parts = (result.stdout or "").split("---")
            for index, name in enumerate(("ram_mb", "disk_mb", "cpus")):
                try:
                    have[name] = int((parts[index] if index < len(parts) else "").strip() or 0)
                except ValueError:
                    have[name] = 0

    problems = []
    if have["ram_mb"] and have["ram_mb"] < needed["ram_mb"]:
        problems.append(f"Arbeitsspeicher: {have['ram_mb']} MB vorhanden, "
                        f"etwa {needed['ram_mb']} MB empfohlen.")
    if have["disk_mb"] and have["disk_mb"] < needed["disk_mb"]:
        problems.append(f"Plattenplatz: {have['disk_mb']} MB frei, "
                        f"etwa {needed['disk_mb']} MB empfohlen.")
    if have["cpus"] and have["cpus"] < 2 and len(wanted) > 1:
        problems.append(f"Prozessorkerne: {have['cpus']} vorhanden, ab zwei "
                        f"Zusatzdiensten sind 2 empfohlen.")

    return {
        "wanted": wanted,
        "needed": needed,
        "available": have,
        "problems": problems,
        "verdict": "ok" if not problems else "tight",
        "message": ("Der Server sollte das tragen." if not problems else
                    "Das wird knapp — es läuft, kann aber langsam werden:"),
    }
