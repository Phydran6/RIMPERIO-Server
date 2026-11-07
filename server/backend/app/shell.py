# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Terminal im Browser (Root-Shell auf dem Host)
# ==============================================================================
"""
Eine echte Shell im Browser.

Was hier passiert, in klaren Worten: ueber `nsenter` wird eine Login-Shell in
den Namensraeumen des Host-Init gestartet - also **root auf dem Server**, nicht
im Container. Ein Pseudo-Terminal haengt daran, und die Bytes gehen ueber einen
WebSocket in den Browser.

Das Vorbild ist die Konsole in Proxmox VE: man klickt auf "Konsole" und tippt
Befehle auf dem System. Genau das gibt es hier, unter *RIMPERIO-Server -> Konsole*.

**Das ist der weitreichendste Knopf im ganzen Programm.** Wer ihn erreicht, hat
den Server. Deshalb:

* nur Administratoren, und der Token wird bei jedem Verbindungsaufbau geprueft;
* nur, wenn der Zugriff auf den Host ueberhaupt offensteht (also nicht beim
  gehaerteten Compose - dort gibt es die Shell schlicht nicht);
* jede Sitzung steht im Systemtagebuch: wer, wann, wie lange, von welcher IP;
* Sitzungen mit einer Obergrenze, damit nicht hundert vergessene Browser-Tabs
  hundert Shells offen halten;
* Leerlauf beendet die Sitzung von selbst.

**Zur Voreinstellung.** Frueher war die Konsole ab Werk aus und musste in der
`.env` eingeschaltet werden. Das klingt vorsichtig, war es aber nicht wirklich:
Wer diese Oberflaeche als Administrator erreicht, kann ohnehin Updates
einspielen, Container neu bauen und den Host neu starten - also in jedem Fall
Code auf diesem Server ausfuehren. Ein zusaetzlicher Schalter davor hat keinen
Angreifer aufgehalten, nur den Betreiber. Jetzt ist sie an, und wer sie nicht
haben will, setzt `RIMPERIO_WEBSHELL=false`. Dann antwortet der Endpunkt mit 403
und es wird gar nichts gestartet.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from . import hostexec

logger = logging.getLogger("rimperio.shell")

# Ohne diesen Schalter gibt es die Shell nicht. Standardmaessig an - siehe die
# Begruendung oben. Abschalten: RIMPERIO_WEBSHELL=false.
ENABLED = os.getenv("RIMPERIO_WEBSHELL", "true").strip().lower() not in ("0", "false", "no", "off")

# Wie viele Sitzungen gleichzeitig offen sein duerfen.
MAX_SESSIONS = int(os.getenv("RIMPERIO_WEBSHELL_MAX_SESSIONS", "3"))

# Nach so vielen Sekunden ohne Tastendruck wird die Sitzung beendet.
IDLE_TIMEOUT = int(os.getenv("RIMPERIO_WEBSHELL_IDLE_TIMEOUT", "900"))

# Wie viele Bytes auf einmal aus dem Terminal geholt werden.
READ_SIZE = 8192


@dataclass
class ShellSession:
    """Eine laufende Sitzung."""
    id: str
    username: str
    pid: int
    fd: int
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    rows: int = 24
    cols: int = 80
    source_ip: str = ""

    @property
    def age_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_activity

    def info(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "pid": self.pid,
            "started_at": self.started_at,
            "age_seconds": round(self.age_seconds),
            "idle_seconds": round(self.idle_seconds),
            "source_ip": self.source_ip,
            "size": {"rows": self.rows, "cols": self.cols},
        }


_sessions: Dict[str, ShellSession] = {}


# =============================================================================
# Verfuegbarkeit
# =============================================================================
def availability() -> dict:
    """Gibt es die Shell hier - und wenn nein, warum nicht?"""
    if not ENABLED:
        return {
            "available": False,
            "reason": ("Die Konsole ist abgeschaltet (RIMPERIO_WEBSHELL=false in der .env). "
                       "Zum Einschalten den Wert entfernen oder auf true setzen und das "
                       "Backend neu starten. Sie öffnet eine Root-Shell auf dem Server — "
                       "wer sie erreicht, hat den Server."),
            "enable_hint": "RIMPERIO_WEBSHELL=true",
        }
    if not hostexec.nsenter_available():
        return {
            "available": False,
            "reason": ("Kein Zugriff auf den Host. Das Terminal braucht ein Backend mit "
                       "privileged/pid:host (Standard-docker-compose.yml). Beim "
                       "gehärteten Compose gibt es es bewusst nicht."),
        }
    return {
        "available": True,
        "max_sessions": MAX_SESSIONS,
        "idle_timeout_seconds": IDLE_TIMEOUT,
        "open_sessions": len(_sessions),
        "warning": ("Dieses Terminal läuft als root auf dem Server. Jede Sitzung wird "
                    "protokolliert."),
    }


def sessions() -> list:
    """Die offenen Sitzungen - fuer die Uebersicht in der Oberflaeche."""
    return [session.info() for session in _sessions.values()]


# =============================================================================
# Sitzung starten und beenden
# =============================================================================
def open_session(username: str, rows: int = 24, cols: int = 80,
                 source_ip: str = "") -> ShellSession:
    """Startet eine Root-Shell auf dem Host mit angehaengtem Terminal."""
    if not ENABLED:
        raise PermissionError("Die Konsole ist abgeschaltet (RIMPERIO_WEBSHELL=false).")
    if not hostexec.nsenter_available():
        raise PermissionError("Kein Zugriff auf den Host — das Terminal ist hier nicht möglich.")

    _reap_idle()
    if len(_sessions) >= MAX_SESSIONS:
        raise RuntimeError(
            f"Es sind bereits {len(_sessions)} Terminal-Sitzungen offen "
            f"(Grenze: {MAX_SESSIONS}). Erst eine davon schließen.")

    rows, cols = _sane_size(rows, cols)

    # `-l` macht daraus eine Login-Shell: /etc/profile laeuft, PATH und Umgebung
    # sehen so aus wie bei einer Anmeldung ueber SSH. Ohne das fehlen auf vielen
    # Systemen die halben Befehle.
    command = [
        "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
        "/bin/sh", "-c", "exec ${SHELL:-/bin/bash} -l",
    ]

    pid, fd = pty.fork()
    if pid == 0:
        # Kindprozess. Ab hier darf nichts mehr zurueckkehren - im Fehlerfall
        # sofort beenden, sonst laeuft eine Kopie des Servers weiter.
        try:
            os.environ["TERM"] = "xterm-256color"
            os.environ["LANG"] = os.environ.get("LANG", "C.UTF-8")
            os.execvp(command[0], command)
        except Exception:                                           # defensiv
            os._exit(127)

    # Elternprozess.
    _set_size(fd, rows, cols)
    os.set_blocking(fd, False)

    session = ShellSession(id=uuid.uuid4().hex[:12], username=username,
                           pid=pid, fd=fd, rows=rows, cols=cols)
    session.source_ip = source_ip
    _sessions[session.id] = session

    logger.warning("TERMINAL GEÖFFNET: Benutzer '%s', Sitzung %s, PID %s "
                   "— Root-Shell auf dem Host", username, session.id, pid)
    _journal(category="shell", event="shell.opened", level="warning",
             message="Konsole geöffnet — Root-Shell auf dem Server.",
             actor=username, source_ip=source_ip, target=f"pid {pid}")
    return session


def _journal(**kwargs) -> None:
    """Eintrag ins Systemtagebuch, ohne dass dieses Modul davon abhaengt.

    Bewusst spaet importiert: `shell` wird auch von Werkzeugen eingebunden, die
    keine Datenbank haben. Faellt der Eintrag aus, laeuft die Shell trotzdem -
    im Anwendungsprotokoll steht sie ohnehin.
    """
    try:
        from . import journal
        journal.fire(**kwargs)
    except Exception:                                               # defensiv
        pass


def close_session(session_id: str, reason: str = "beendet") -> bool:
    """Beendet eine Sitzung und raeumt Prozess und Terminal weg."""
    session = _sessions.pop(session_id, None)
    if not session:
        return False

    try:
        os.close(session.fd)
    except OSError:
        pass

    # Erst hoeflich fragen, dann nachdruecklich. Ohne das zweite bleibt ein
    # haengender Prozess als Zombie stehen.
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.kill(session.pid, sig)
        except ProcessLookupError:
            break
        except OSError:
            break
        time.sleep(0.1)
        try:
            done, _ = os.waitpid(session.pid, os.WNOHANG)
            if done:
                break
        except ChildProcessError:
            break

    try:
        os.waitpid(session.pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass

    logger.warning("Terminal geschlossen: Sitzung %s von '%s' nach %.0fs (%s)",
                   session.id, session.username, session.age_seconds, reason)
    _journal(category="shell", event="shell.closed",
             message=f"Konsole geschlossen nach {session.age_seconds:.0f}s ({reason}).",
             actor=session.username, source_ip=getattr(session, "source_ip", ""),
             duration_ms=int(session.age_seconds * 1000))
    return True


def _reap_idle() -> int:
    """Beendet Sitzungen, in denen zu lange nichts passiert ist."""
    stale = [s.id for s in _sessions.values() if s.idle_seconds > IDLE_TIMEOUT]
    for session_id in stale:
        close_session(session_id, reason=f"Leerlauf über {IDLE_TIMEOUT}s")
    return len(stale)


# =============================================================================
# Ein- und Ausgabe
# =============================================================================
def _sane_size(rows: int, cols: int) -> tuple:
    """Haelt die Terminalgroesse in einem Bereich, den ein PTY vertraegt."""
    return (max(4, min(int(rows or 24), 300)),
            max(20, min(int(cols or 80), 500)))


def _set_size(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError as exc:
        logger.debug("Terminalgröße nicht gesetzt: %s", exc)


def resize(session_id: str, rows: int, cols: int) -> bool:
    """Uebernimmt die Fenstergroesse aus dem Browser.

    Ohne das bricht jedes Programm mit eigener Bildschirmausgabe (top, nano,
    less) die Zeilen an der falschen Stelle um.
    """
    session = _sessions.get(session_id)
    if not session:
        return False
    session.rows, session.cols = _sane_size(rows, cols)
    _set_size(session.fd, session.rows, session.cols)
    return True


def write(session_id: str, data: str) -> bool:
    """Schickt Tastatureingaben an die Shell."""
    session = _sessions.get(session_id)
    if not session:
        return False
    session.last_activity = time.time()
    try:
        os.write(session.fd, data.encode("utf-8", "replace"))
        return True
    except OSError:
        close_session(session_id, reason="Schreiben fehlgeschlagen")
        return False


async def read_stream(session_id: str):
    """Liefert fortlaufend, was die Shell ausgibt.

    Warum eine Warteschleife statt `loop.add_reader`: der Dateideskriptor eines
    PTY verhaelt sich je nach Plattform unterschiedlich, wenn die Gegenseite
    stirbt. Ein kurzes Nachsehen im Takt ist unspektakulaer, aber robust - und
    bei Terminalausgabe faellt es nicht ins Gewicht.
    """
    session = _sessions.get(session_id)
    if not session:
        return

    loop = asyncio.get_running_loop()
    while session_id in _sessions:
        try:
            chunk = await loop.run_in_executor(None, _read_nonblocking, session.fd)
        except OSError:
            break

        if chunk is None:                    # Shell beendet
            break
        if chunk:
            yield chunk.decode("utf-8", "replace")
            continue

        if session.idle_seconds > IDLE_TIMEOUT:
            yield (f"\r\n\x1b[33m[RIMPERIO] Sitzung nach {IDLE_TIMEOUT}s ohne Eingabe "
                   f"beendet.\x1b[0m\r\n")
            break
        await asyncio.sleep(0.05)

    close_session(session_id, reason="Shell beendet")


def _read_nonblocking(fd: int) -> Optional[bytes]:
    """b'' = gerade nichts da, None = Gegenseite ist weg."""
    try:
        return os.read(fd, READ_SIZE)
    except BlockingIOError:
        return b""
    except OSError:
        return None


def shutdown_all(reason: str = "Backend fährt herunter") -> int:
    """Alle Sitzungen beenden (beim Herunterfahren)."""
    count = len(_sessions)
    for session_id in list(_sessions):
        close_session(session_id, reason=reason)
    return count
