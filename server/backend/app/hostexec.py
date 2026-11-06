# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.14.12.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Befehle auf dem Host ausfuehren (fuer Update & Diagnose)
# ==============================================================================
"""
Kleiner Helfer, um vom Backend-Container aus Befehle auf dem *Host* auszufuehren.

Warum das noetig ist: Ein Update baut Container neu und startet sie neu - der
Container kann sich nicht selbst aus dem laufenden Betrieb heraus ersetzen.
Also muss der Befehl ausserhalb des Containers laufen.

Wie das geht: Der Backend-Container laeuft mit `privileged: true` und
`pid: "host"` (siehe docker-compose.yml, dort auch die Sicherheitsabwaegung).
Damit ist PID 1 der Init-Prozess des Hosts, und `nsenter -t 1 ...` betritt
dessen Namensraeume. Genau dieser Weg wird bereits fuer den Neustart-Knopf
genutzt.

Zwei Betriebsarten:

* `run_host(...)`   - kurzer Befehl, wir warten auf das Ergebnis.
* `spawn_host(...)` - langlaufender Befehl, der den Container ueberleben muss.

Fuer den zweiten Fall reicht ein normaler Hintergrundprozess NICHT: ein per
nsenter gestarteter Prozess bleibt in der Control-Group des Containers und wird
mitgetoetet, sobald Docker den Container stoppt - also mitten im Update. Daher
wird bevorzugt `systemd-run` benutzt: das laesst den Host-Init den Prozess als
eigene Unit starten, komplett losgeloest vom Container. Nur wenn es kein
systemd gibt, faellt der Helfer auf `setsid`+`nohup` zurueck (dann steht das in
der Rueckmeldung, damit man im Fehlerfall weiss, woran es lag).
"""

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger("rimperio.hostexec")

# Installationsverzeichnis auf dem Host (dort liegen docker-compose.yml und .env).
INSTALL_DIR = os.getenv("RIMPERIO_INSTALL_DIR", "/opt/rimperio")

# Namensraeume des Host-Init betreten. "--" trennt sauber vom eigentlichen Befehl.
_NSENTER = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"]


@dataclass
class HostResult:
    """Ergebnis eines Host-Befehls."""
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    error: str = ""          # gesetzt, wenn der Befehl gar nicht erst startete

    @property
    def output(self) -> str:
        """stdout, sonst stderr - fuer kurze Auskuenfte wie `git rev-parse`."""
        return (self.stdout or self.stderr or "").strip()


def nsenter_available() -> bool:
    """Prueft, ob der Weg auf den Host ueberhaupt offensteht."""
    if not shutil.which("nsenter"):
        return False
    # Ohne pid:host zeigt /proc/1 auf den Container-Init - dann bringt nsenter nichts.
    return Path("/proc/1/ns/mnt").exists()


async def run_host(
    command: Sequence[str],
    timeout: float = 60.0,
    input_text: Optional[str] = None,
) -> HostResult:
    """Fuehrt einen Befehl auf dem Host aus und wartet auf das Ergebnis.

    `command` ist eine Argumentliste (keine Shell). Wer eine Pipeline braucht,
    uebergibt bewusst ["sh", "-lc", "..."].
    """
    if not nsenter_available():
        return HostResult(
            ok=False, returncode=-1, stdout="", stderr="",
            error="Kein Zugriff auf den Host (nsenter fehlt oder pid:host ist nicht aktiv).",
        )

    argv = [*_NSENTER, *command]
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return HostResult(ok=False, returncode=-1, stdout="", stderr="", error=str(exc))

    payload = input_text.encode("utf-8") if input_text is not None else None
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return HostResult(
            ok=False, returncode=-1, stdout="", stderr="",
            error=f"Zeitueberschreitung nach {timeout:.0f}s: {' '.join(command)}",
        )

    return HostResult(
        ok=process.returncode == 0,
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=(stdout or b"").decode("utf-8", "replace"),
        stderr=(stderr or b"").decode("utf-8", "replace"),
    )


async def read_host_file(path: str, timeout: float = 10.0) -> Optional[str]:
    """Liest eine Datei vom Host. None, wenn sie fehlt oder nicht lesbar ist."""
    result = await run_host(["cat", path], timeout=timeout)
    return result.stdout if result.ok else None


async def write_host_file(path: str, content: str, mode: str = "0644", timeout: float = 20.0) -> HostResult:
    """Schreibt eine Datei auf den Host (Verzeichnis wird angelegt)."""
    directory = str(Path(path).parent)
    # Inhalt kommt ueber stdin - so landen weder Anfuehrungszeichen noch
    # Sonderzeichen in einer Kommandozeile.
    script = f'mkdir -p "{directory}" && cat > "{path}" && chmod {mode} "{path}"'
    return await run_host(["sh", "-c", script], timeout=timeout, input_text=content)


async def spawn_host(
    command: Sequence[str],
    unit_name: str,
    log_path: str,
) -> HostResult:
    """Startet einen langlaufenden Befehl auf dem Host, losgeloest vom Container.

    Wichtig fuer das Update: der Befehl baut die Container neu und beendet dabei
    auch dieses Backend. Ein Kindprozess des Containers wuerde dabei sterben.
    """
    log_dir = str(Path(log_path).parent)

    # Der eigentliche Befehl, samt Umleitung ins Protokoll, als EIN Wort fuer
    # `sh -c`. Doppelt gequotet: einmal fuer die aeussere Shell, einmal fuer die
    # innere, die den Befehl schliesslich ausfuehrt.
    inner = " ".join(_shell_quote(part) for part in command)
    inner_with_log = _shell_quote('%s >> "%s" 2>&1' % (inner, log_path))

    # 1. Weg: systemd-run - der Host-Init uebernimmt den Prozess.
    #    --collect raeumt die Unit nach dem Ende selbst wieder auf.
    systemd_cmd = [
        "sh", "-c",
        f'mkdir -p "{log_dir}" && '
        f'command -v systemd-run >/dev/null 2>&1 && '
        f'systemd-run --collect --unit={_shell_quote(unit_name)} '
        f'--description="RIMPERIO Wartungslauf" '
        f'/bin/sh -c {inner_with_log}',
    ]
    result = await run_host(systemd_cmd, timeout=30.0)
    if result.ok:
        return result

    logger.warning("systemd-run nicht nutzbar (%s) - weiche auf setsid aus",
                   (result.stderr or result.error or "").strip())

    # 2. Weg: setsid + nohup. Laeuft ohne systemd, haengt aber weiter an der
    #    Control-Group des Containers - beim Neubau kann der Lauf abbrechen.
    fallback_cmd = [
        "sh", "-c",
        f'mkdir -p "{log_dir}" && '
        f'setsid nohup /bin/sh -c {inner_with_log} '
        f'< /dev/null >> "{log_path}" 2>&1 &',
    ]
    fallback = await run_host(fallback_cmd, timeout=30.0)
    if fallback.ok:
        fallback.stderr = (fallback.stderr or "") + "\nHinweis: ohne systemd gestartet."
    return fallback


def _shell_quote(value: str) -> str:
    """Einfaches Quoting fuer sh -c (einfache Anfuehrungszeichen)."""
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def host_paths() -> dict:
    """Die Pfade, mit denen Update und Diagnose auf dem Host arbeiten."""
    base = INSTALL_DIR.rstrip("/") or "/opt/rimperio"
    return {
        "install_dir": base,
        "state_dir": f"{base}/data",
        "state_file": f"{base}/data/update-state.json",
        "log_file": f"{base}/data/update.log",
        "script": f"{base}/scripts/rimperio-update.sh",
        "backup_dir": f"{base}-backups",
    }


def split_lines(text: str, limit: int = 200) -> List[str]:
    """Ausgabe in Zeilen zerlegen und bei `limit` abschneiden (fuer die Oberflaeche)."""
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    return lines[-limit:]
