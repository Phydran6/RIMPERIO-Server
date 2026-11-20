# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.20.22.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Linux-Systeme selbst einbinden (ueber SSH).
# ==============================================================================
"""
Ein Linux-System per SSH einbinden, ohne dort von Hand etwas zu tun.

Der Agent laeuft danach **nicht als root**, sondern unter einem eigenen
Dienstkonto (Standard ``rimperio``, in der Oberflaeche waehlbar; fehlt es,
legt der Installer es an). Alles liegt unter ``/opt/RMM/rimperio``. ``sudo``
braucht nur die Einrichtung selbst.

Was passiert
------------
1. **Fingerabdruck zeigen.** Beim ersten Verbinden kennt niemand den
   Hostschluessel des Zielsystems. Er wird geholt und dem Administrator
   gezeigt; erst wenn der bestaetigt, geht es weiter - gegen genau diesen
   Schluessel. So kann sich kein Dritter dazwischenschalten.
2. **Anmelden** mit einem *normalen* Benutzer und dessen Passwort. Root ueber
   SSH ist auf den meisten Systemen ohnehin gesperrt und waere die schlechtere
   Wahl. Fuer die Installation wird `sudo` benutzt.
3. **Pruefen**, ob das System passt: Linux, systemd, python3 vorhanden, und ob
   der Benutzer `sudo` darf.
4. **Installer uebertragen** - direkt ueber die bestehende SSH-Verbindung
   (SFTP). Das Zielsystem muss dafuer weder ins Internet noch den RIMPERIO-Server
   erreichen; der kurze Agent-Dienst ist im Installer selbst enthalten.
5. **Installer ausfuehren** mit einer Einladung, die der Server vorher erzeugt
   hat, und dem gewaehlten Dienstkonto. Der Installer tauscht die Einladung in
   einen eigenen Geraeteschluessel - wie beim Einzeiler von Hand.
6. **RMM-Agent einrichten** (Patchen, Checks, Inventur, Befehle) mit
   ``install-agent.sh`` und einer zweiten Einladung, unter demselben
   Dienstkonto. Das Programm laedt das Zielsystem selbst aus dem GitHub-Release
   (curl oder wget). Ohne diesen Schritt erschien das Geraet nur als
   Log-Quelle.
7. **Nachsehen**, unter welchem Konto die Dienste laufen - root waere ein Fehler.
8. **Warten**, bis wirklich Logs ankommen. Erst dann gilt das Geraet als
   eingebunden.

Zugangsdaten
------------
Benutzername, Passwort und sudo-Passwort leben **nur fuer diesen einen
Vorgang** in einer lokalen Variablen. Sie werden nie in der Datenbank
gespeichert, nie ins Tagebuch geschrieben und am Ende - auch im Fehlerfall -
mit `wipe()` ueberschrieben. Im Tagebuch steht nur, wer wann welches Geraet
eingebunden hat und ob es geklappt hat.

Testbarkeit
-----------
Die SSH-Aufrufe laufen ueber die schmale Schnittstelle `SshSession`. Der echte
Weg ist `ParamikoSession`; die Tests schieben eine Attrappe unter, sodass die
Ablauflogik ohne echten SSH-Server prueffbar ist.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import logging
import re
import secrets
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import discovery, journal, tokens
from .config import settings
from .models import Agent, DiscoveredDevice, Log

logger = logging.getLogger("rimperio.onboard")

# Wie lange auf die erste Logzeile gewartet wird, nachdem der Agent steht.
DELIVERY_TIMEOUT = 120
DELIVERY_POLL = 5

# Laufzeit der Einladung: knapp, sie wird ja sofort benutzt.
ENROLL_HOURS = 1

# Dienstkonto, unter dem der Agent auf dem Ziel laeuft - nie root.
DEFAULT_SERVICE_USER = "rimperio"
SERVICE_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# So heisst der Dienst des Log-Agents (agent/logship/install-linux.sh).
LOG_AGENT_SERVICE = "rimperio-log-agent"
# ... und der des RMM-Agents (agent/installer/linux/install-agent.sh).
RMM_AGENT_SERVICE = "rimperio-agent"

# Installer im Host-Installationsverzeichnis, jeweils mit einem Text, der
# sicher darin steht (falsche Datei = Abbruch statt Ausfuehren).
LOG_INSTALLER = ("agent/logship/install-linux.sh", "RIMPERIO Linux Agent")
RMM_INSTALLER = ("agent/installer/linux/install-agent.sh", "RIMPERIO-Agent (Fernwartung)")


def valid_service_user(name: str) -> bool:
    """Linux-Kontoname ohne Sonderzeichen - und nicht root."""
    return bool(SERVICE_USER_RE.match(name or "")) and name != "root"


# =============================================================================
# Zugangsdaten - fluechtig
# =============================================================================
@dataclass
class Credentials:
    """Nur fuer einen Vorgang. Nach `wipe()` sind die Felder leer."""
    username: str
    password: str
    sudo_password: str = ""

    def effective_sudo(self) -> str:
        """sudo-Passwort, sonst das Anmeldepasswort (ueblich bei einem Konto)."""
        return self.sudo_password or self.password

    def wipe(self) -> None:
        # Python haelt keine Referenz auf den alten String fest, sobald hier
        # ueberschrieben wird; mehr als das geht in CPython nicht erzwingen.
        self.username = self.password = self.sudo_password = ""


class OnboardError(Exception):
    """Ein Schritt ist schiefgegangen - die Meldung ist fuer die Oberflaeche gedacht."""


# =============================================================================
# SSH-Schnittstelle
# =============================================================================
@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def text(self) -> str:
        return (self.stdout or self.stderr or "").strip()


class SshSession(Protocol):
    def run(self, command: str, *, sudo: bool = False, sudo_password: str = "",
            timeout: float = 60.0) -> CommandResult: ...
    def put_file(self, content: str, remote_path: str, mode: int = 0o700) -> None: ...
    def close(self) -> None: ...


def fingerprint_sha256(key_bytes: bytes) -> str:
    """OpenSSH-Schreibweise des Hostschluessels: "SHA256:abc…" (ohne Padding)."""
    digest = hashlib.sha256(key_bytes).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


# --- Echte Umsetzung mit paramiko --------------------------------------------
def read_host_fingerprint(host: str, port: int = 22, timeout: float = 8.0) -> dict:
    """Holt den Hostschluessel OHNE Anmeldung - fuer die Bestaetigung."""
    import paramiko

    transport = paramiko.Transport((host, port))
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
    finally:
        transport.close()
    return {
        "fingerprint": fingerprint_sha256(key.asbytes()),
        "key_type": key.get_name(),
    }


class _PinnedPolicy:
    """Akzeptiert genau den vom Administrator bestaetigten Schluessel - sonst Abbruch."""

    def __init__(self, expected: str) -> None:
        self.expected = expected

    def missing_host_key(self, client, hostname, key) -> None:
        import paramiko

        actual = fingerprint_sha256(key.asbytes())
        if self.expected and actual != self.expected:
            raise paramiko.SSHException(
                f"Hostschlüssel weicht ab (erwartet {self.expected}, bekommen {actual}). "
                "Vorgang abgebrochen.")


class ParamikoSession:
    """SSH-Verbindung ueber paramiko. Alle Methoden blockieren - im Executor nutzen."""

    def __init__(self, host: str, port: int, credentials: Credentials,
                 expected_fingerprint: str, connect_timeout: float = 12.0) -> None:
        import paramiko

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(_PinnedPolicy(expected_fingerprint))
        self._client.connect(
            hostname=host, port=port,
            username=credentials.username, password=credentials.password,
            look_for_keys=False, allow_agent=False,
            timeout=connect_timeout, auth_timeout=connect_timeout, banner_timeout=connect_timeout,
        )

    def run(self, command: str, *, sudo: bool = False, sudo_password: str = "",
            timeout: float = 60.0) -> CommandResult:
        if sudo:
            # -S liest das Passwort von stdin, -p '' unterdrueckt die Aufforderung,
            # -k ignoriert ein zwischengespeichertes sudo-Ticket (immer frisch pruefen).
            command = f"sudo -S -k -p '' bash -c {shlex.quote(command)}"
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        if sudo:
            stdin.write(sudo_password + "\n")
            stdin.flush()
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return CommandResult(exit_code=code, stdout=out, stderr=err)

    def put_file(self, content: str, remote_path: str, mode: int = 0o700) -> None:
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as handle:
                handle.write(content)
            sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


SessionFactory = Callable[[str, int, Credentials, str], SshSession]


def _default_factory(host: str, port: int, creds: Credentials, fp: str) -> SshSession:
    return ParamikoSession(host, port, creds, fp)


# =============================================================================
# Reine Helfer (ohne SSH - getestet)
# =============================================================================
def parse_os_release(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def build_install_command(script_path: str, *, fqdn: str, token: str,
                          ip_fallback: str = "", port: int = 443,
                          insecure: bool = False, min_level: str = "info",
                          service_user: str = DEFAULT_SERVICE_USER) -> str:
    """Die Argumentzeile fuer install-linux.sh. Werte werden sicher gequotet."""
    if not valid_service_user(service_user):
        raise OnboardError(f"Ungültiges Dienstkonto „{service_user}“ (nicht root, nur a-z, 0-9, _ und -).")
    args = ["bash", script_path, "--mode", "https", "--yes",
            "--fqdn", fqdn, "--token", token, "--port", str(port),
            "--min-level", min_level, "--user", service_user]
    if ip_fallback:
        args += ["--ip", ip_fallback]
    if insecure:
        args += ["--insecure"]
    return " ".join(shlex.quote(a) for a in args)


def build_rmm_install_command(script_path: str, *, fqdn: str, token: str,
                              insecure: bool = False, site: str = "Standard",
                              service_user: str = DEFAULT_SERVICE_USER) -> str:
    """Die Argumentzeile fuer install-agent.sh (RMM-Agent). Werte sicher gequotet."""
    if not valid_service_user(service_user):
        raise OnboardError(f"Ungültiges Dienstkonto „{service_user}“ (nicht root, nur a-z, 0-9, _ und -).")
    args = ["bash", script_path, "--server", f"https://{fqdn}", "--secret", token,
            "--site", site or "Standard", "--user", service_user]
    if insecure:
        args += ["--insecure"]
    return " ".join(shlex.quote(a) for a in args)


def resolve_server_address(device: dict, *, site_url: Optional[str],
                           nets: "discovery.HostNetworks") -> dict:
    """Unter welcher Adresse der Agent den Server erreicht.

    Vorrang hat ein konfigurierter Name (SITE_URL) - ein HTTPS-Zertifikat lautet
    auf einen Namen, nicht auf eine IP. Fehlt er, nimmt der Agent die Adresse des
    Servers im Subnetz des Ziels und akzeptiert das selbstsignierte Zertifikat.
    """
    host = ""
    if site_url:
        cleaned = site_url.split("://", 1)[-1].split("/", 1)[0]
        host = cleaned.split(":", 1)[0]
    if host:
        return {"fqdn": host, "ip_fallback": "", "insecure": False, "has_name": True}

    local = device.get("syslog_target")
    if not local and nets and nets.interfaces:
        local = nets.local_address_for(device["ip_address"]) or nets.interfaces[0]["address"]
    return {"fqdn": local or "", "ip_fallback": local or "", "insecure": True, "has_name": False}


# =============================================================================
# Zustand je Geraet
# =============================================================================
@dataclass
class OnboardState:
    device_id: int
    running: bool = True
    phase: str = ""
    steps: List[dict] = field(default_factory=list)   # {"text","ok"}
    ok: bool = False
    error: str = ""
    agent_hostname: str = ""
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at: Optional[str] = None

    def step(self, text: str, ok: bool = True) -> None:
        self.steps.append({"text": text, "ok": ok})
        self.phase = text

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id, "running": self.running, "phase": self.phase,
            "steps": list(self.steps), "ok": self.ok, "error": self.error,
            "agent_hostname": self.agent_hostname,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


STATES: Dict[int, OnboardState] = {}
_locks: Dict[int, asyncio.Lock] = {}
_tasks: set = set()


def _lock_for(device_id: int) -> asyncio.Lock:
    return _locks.setdefault(device_id, asyncio.Lock())


def state_for(device_id: int) -> Optional[dict]:
    st = STATES.get(device_id)
    return st.as_dict() if st else None


def is_running(device_id: int) -> bool:
    st = STATES.get(device_id)
    return bool(st and st.running)


# =============================================================================
# Blockierender SSH-Teil
# =============================================================================
def _run_ssh(device: dict, creds: Credentials, fingerprint: str, enroll_token: str,
             server: dict, state: OnboardState,
             factory: SessionFactory = _default_factory,
             service_user: str = DEFAULT_SERVICE_USER,
             rmm_token: str = "") -> str:
    """Verbindet, prueft, installiert. Gibt den Hostnamen des Ziels zurueck.

    Laeuft im Thread-Executor (paramiko blockiert). Wirft OnboardError mit einer
    Meldung fuer die Oberflaeche. Ohne ``rmm_token`` nur der Log-Agent.
    """
    host = device["ip_address"]
    is_root = creds.username == "root"
    session: Optional[SshSession] = None
    try:
        try:
            session = factory(host, 22, creds, fingerprint)
        except Exception as exc:
            raise OnboardError(f"SSH-Verbindung fehlgeschlagen: {exc}") from exc
        state.step("Verbunden und angemeldet")

        uname = session.run("uname -s", timeout=20)
        if not uname.ok or "linux" not in uname.text.lower():
            raise OnboardError(f"Das ist kein Linux-System (uname: {uname.text or 'keine Antwort'}).")

        release = parse_os_release(session.run("cat /etc/os-release 2>/dev/null", timeout=20).stdout)
        pretty = release.get("PRETTY_NAME", "Linux")
        state.step(f"Erkannt: {pretty}")

        if session.run("command -v systemctl", timeout=20).ok is False:
            raise OnboardError("Kein systemd gefunden - der HTTPS-Agent braucht systemd.")
        if session.run("command -v python3", timeout=20).ok is False:
            raise OnboardError("python3 fehlt auf dem Zielsystem. Bitte zuerst installieren.")

        # sudo-Recht pruefen (ausser als root).
        if not is_root:
            check = session.run("true", sudo=True, sudo_password=creds.effective_sudo(), timeout=25)
            if not check.ok:
                raise OnboardError(
                    "Der Benutzer darf kein sudo (oder das Passwort stimmt nicht). "
                    "Entweder ein sudo-Konto nehmen oder den Agent von Hand einrichten.")
            state.step("sudo-Recht bestätigt")
        else:
            state.step("Als root angemeldet")

        downloader = session.run("command -v curl || command -v wget", timeout=20)
        if rmm_token and not downloader.ok:
            raise OnboardError(
                "Weder curl noch wget gefunden. Der RMM-Agent lädt sein Programm aus dem "
                "GitHub-Release — bitte curl installieren (z. B. apt install curl).")

        hostname = session.run("hostname -f 2>/dev/null || hostname", timeout=20).text or host
        state.agent_hostname = hostname

        # Beide Installer kommen vom Server, nicht aus dem Netz - so laeuft genau
        # der Stand, zu dem dieser Server passt. Gelesen wird vorab: fehlt einer,
        # bricht das Einbinden ab, bevor auf dem Ziel etwas geaendert ist.
        log_installer = _installer_source(*LOG_INSTALLER)
        rmm_installer = _installer_source(*RMM_INSTALLER) if rmm_token else ""

        # 1) Log-Agent
        _run_installer(
            session, state, log_installer, is_root, creds,
            lambda path: build_install_command(
                path, fqdn=server["fqdn"], token=enroll_token,
                ip_fallback=server["ip_fallback"], insecure=server["insecure"],
                service_user=service_user),
            what="Log-Agent", timeout=180)
        _check_account(session, state, LOG_AGENT_SERVICE, "Log-Agent")

        # 2) RMM-Agent (Patchen, Checks, Inventur, Befehle)
        if rmm_token:
            _run_installer(
                session, state, rmm_installer, is_root, creds,
                lambda path: build_rmm_install_command(
                    path, fqdn=server["fqdn"], token=rmm_token,
                    insecure=server["insecure"], service_user=service_user),
                what="RMM-Agent", timeout=300,
                failed_hint="Der Log-Agent läuft bereits; nur der RMM-Agent fehlt. ")
            _check_account(session, state, RMM_AGENT_SERVICE, "RMM-Agent")
        return hostname
    finally:
        if session is not None:
            session.close()


def _run_installer(session: SshSession, state: OnboardState, content: str, is_root: bool,
                   creds: Credentials, build_command: Callable[[str], str], *, what: str,
                   timeout: float, failed_hint: str = "") -> None:
    """Ein Installer: uebertragen, ausfuehren, wieder loeschen.

    ``build_command`` bekommt den Pfad auf dem Ziel und liefert die Befehlszeile.
    """
    remote_script = f"/tmp/rimperio-install-{secrets.token_hex(6)}.sh"
    command = build_command(remote_script)   # prueft das Dienstkonto vor dem Uebertragen
    session.put_file(content, remote_script, mode=0o700)
    try:
        result = session.run(command, sudo=not is_root,
                             sudo_password=creds.effective_sudo(), timeout=timeout)
    finally:
        # Aufraeumen, egal wie es ausging - im Aufruf stand die Einladung.
        session.run(f"rm -f {shlex.quote(remote_script)}", sudo=not is_root,
                    sudo_password=creds.effective_sudo(), timeout=20)
    if not result.ok:
        tail = "\n".join(result.text.splitlines()[-8:])
        logger.warning("Einbinden: %s-Installer fehlgeschlagen (Exit %s):\n%s",
                       what, result.exit_code, result.text[-4000:])
        raise OnboardError(f"{failed_hint}Der Installer des {what}s ist fehlgeschlagen:\n{tail}")
    state.step(f"{what} installiert und gestartet (unter /opt/RMM/rimperio)")


def _check_account(session: SshSession, state: OnboardState, service: str, what: str) -> None:
    """Unter welchem Konto laeuft der Dienst? root waere ein Fehler."""
    runs_as = session.run(
        f"systemctl show -p User {service} 2>/dev/null | cut -d= -f2", timeout=20).text
    if runs_as and runs_as != "root":
        state.step(f"{what} läuft als Dienstkonto „{runs_as}“, nicht als root")
    else:
        state.step(f"Achtung: Der {what} läuft als „{runs_as or 'root'}“ — bitte auf dem System prüfen.",
                   ok=False)


def _installer_source(relative: str, marker: str) -> str:
    """Den Inhalt eines Installers aus dem Host-Installationsverzeichnis.

    Der Backend-Container hat agent/ nicht im Image; er liest es ueber den
    Host (pid:host + privileged). Bei jedem Einbinden frisch gelesen - sonst
    verteilte ein Backend nach einem Update der Dateien noch den alten Stand.
    """
    from . import hostexec

    paths = hostexec.host_paths()
    candidate = f"{paths['install_dir']}/{relative}"
    content = None
    try:
        # synchroner Aufruf im Executor-Thread - eigener Loop
        content = asyncio.run(hostexec.read_host_file(candidate))
    except RuntimeError:
        # Es laeuft schon eine Schleife (sollte im Executor nicht passieren).
        loop = asyncio.new_event_loop()
        try:
            content = loop.run_until_complete(hostexec.read_host_file(candidate))
        finally:
            loop.close()
    if not content or marker not in content:
        raise OnboardError(
            "Der Linux-Installer ließ sich auf dem Server nicht finden "
            f"({candidate}). Im gehärteten Betrieb ist das automatische "
            "Einbinden nicht möglich — bitte den Einzeiler von Hand nutzen.")
    return content


# =============================================================================
# Ablauf (async)
# =============================================================================
async def start(device_id: int, creds: Credentials, fingerprint: str,
                actor: str, source_ip: str,
                factory: SessionFactory = _default_factory,
                service_user: str = DEFAULT_SERVICE_USER,
                with_rmm: bool = True) -> bool:
    """Startet das Einbinden im Hintergrund. False, wenn fuer dieses Geraet schon einer laeuft."""
    lock = _lock_for(device_id)
    if lock.locked() or is_running(device_id):
        creds.wipe()
        return False
    STATES[device_id] = OnboardState(device_id=device_id)
    task = asyncio.create_task(_orchestrate(device_id, creds, fingerprint, actor, source_ip, factory,
                                            service_user, with_rmm))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def _orchestrate(device_id: int, creds: Credentials, fingerprint: str,
                       actor: str, source_ip: str, factory: SessionFactory,
                       service_user: str = DEFAULT_SERVICE_USER,
                       with_rmm: bool = True) -> None:
    from .database import async_session

    state = STATES[device_id]
    started = time.monotonic()
    async with _lock_for(device_id):
        try:
            async with async_session() as session:
                device, enroll_token, rmm_token, server = await _prepare(
                    session, device_id, actor, with_rmm=with_rmm)

            loop = asyncio.get_running_loop()
            hostname = await loop.run_in_executor(
                None, functools.partial(
                    _run_ssh, device, creds, fingerprint, enroll_token, server, state, factory,
                    service_user, rmm_token=rmm_token))

            state.phase = "Warte auf die erste Logzeile…"
            delivering = await _await_delivery(hostname, device["ip_address"])
            state.step("Logs kommen an" if delivering
                       else "Log-Agent läuft. Es kam noch keine Logzeile an — das kann einen Moment dauern.",
                       ok=delivering)
            rmm_ok = None
            if with_rmm:
                rmm_ok = await _rmm_enrolled(hostname)
                state.step("RMM-Agent angemeldet — erscheint unter Geräte → Rechner" if rmm_ok
                           else "Der RMM-Agent ist installiert, aber auf dem Server nicht als angemeldet zu sehen.",
                           ok=rmm_ok)
            state.ok = True
            state.step("Eingebunden")

            async with async_session() as session:
                await journal.record(
                    session, category="discovery", event="onboard.ok",
                    message=f"{hostname} ({device['ip_address']}) per SSH eingebunden"
                            + (" (Log- und RMM-Agent)." if with_rmm else " (nur Log-Agent)."),
                    actor=actor, source_ip=source_ip, target=hostname, ok=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    detail={"delivering": delivering, "rmm": rmm_ok, "has_name": server["has_name"],
                            "service_user": service_user})
            logger.info("Einbinden: %s (%s) fertig - Logs: %s, RMM: %s",
                        hostname, device["ip_address"], delivering, rmm_ok)
        except OnboardError as exc:
            await _fail(state, device_id, actor, source_ip, str(exc))
        except Exception as exc:
            logger.exception("Einbinden fehlgeschlagen")
            await _fail(state, device_id, actor, source_ip, f"Unerwarteter Fehler: {exc}")
        finally:
            creds.wipe()
            state.running = False
            state.finished_at = datetime.utcnow().isoformat()


async def _prepare(session: AsyncSession, device_id: int, actor: str, *, with_rmm: bool = True):
    """Geraet laden, Einladungen erzeugen, Serveradresse bestimmen.

    Je Agent eine eigene Einladung fuer genau eine Anmeldung: Log-Agent und
    RMM-Agent tauschen sie jeweils gegen ihren eigenen Schluessel.
    """
    device_row = await session.get(DiscoveredDevice, device_id)
    if not device_row:
        raise OnboardError("System nicht gefunden — vermutlich aus der Liste entfernt.")
    if device_row.kind not in ("linux", "appliance", "unknown"):
        raise OnboardError(f"Automatisches Einbinden ist nur für Linux gedacht (erkannt: {device_row.kind}).")

    nets = await discovery.host_networks()
    devices = await discovery.list_devices(session, nets)
    device = next((d for d in devices if d["id"] == device_id), None)
    if device is None:
        raise OnboardError("System nicht gefunden.")

    from . import public_url
    server = resolve_server_address(device, site_url=public_url.site_url() or None, nets=nets)
    if not server["fqdn"]:
        raise OnboardError(
            "Es ließ sich keine Serveradresse bestimmen, unter der der Agent RIMPERIO erreicht. "
            "Bitte unter Einstellungen → HTTPS & Adresse die öffentliche Adresse hinterlegen.")

    row, plain = await tokens.create(
        session, name=f"onboard:{device['ip_address']}"[:100], kind="enroll",
        device_type="linux_agent", expires_in_hours=ENROLL_HOURS, max_uses=1,
        created_by=f"onboard:{actor}"[:50],
        note=f"Automatisch beim Einbinden von {device['ip_address']} erzeugt.")
    rmm_plain = ""
    if with_rmm:
        _, rmm_plain = await tokens.create(
            session, name=f"onboard-rmm:{device['ip_address']}"[:100], kind="enroll",
            device_type="linux_agent", expires_in_hours=ENROLL_HOURS, max_uses=1,
            created_by=f"onboard:{actor}"[:50],
            note=f"Automatisch beim Einbinden von {device['ip_address']} erzeugt (RMM-Agent).")
    await session.commit()
    return device, plain, rmm_plain, server


async def _rmm_enrolled(hostname: str) -> bool:
    """Hat sich der RMM-Agent dieses Rechners in den letzten Minuten angemeldet?"""
    from .database import async_session

    names = {hostname.lower(), hostname.split(".")[0].lower()}
    since = datetime.utcnow() - timedelta(minutes=10)
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(Agent).where(func.lower(Agent.hostname).in_(names),
                                    Agent.rmm_token_hash.isnot(None)))).scalars().all()
    except Exception as exc:
        logger.warning("Einbinden: RMM-Anmeldung nicht prüfbar: %s", exc)
        return False
    return any(a.rmm_last_seen and a.rmm_last_seen >= since for a in rows)


async def _await_delivery(hostname: str, ip: str) -> bool:
    """Pollt, bis eine frische Logzeile dieses Geraets auftaucht."""
    from .database import async_session

    short = hostname.split(".")[0]
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    since = datetime.utcnow() - timedelta(minutes=2)
    while time.monotonic() < deadline:
        await asyncio.sleep(DELIVERY_POLL)
        try:
            async with async_session() as session:
                found = (await session.execute(
                    select(Log.id).where(
                        Log.timestamp >= since,
                        (Log.hostname == hostname) | (Log.hostname == short) | (Log.ip_address == ip),
                    ).limit(1))).first()
                if found:
                    return True
                agent = (await session.execute(
                    select(Agent).where((Agent.hostname == hostname) | (Agent.ip_address == ip))
                    .order_by(Agent.last_seen.desc().nullslast()))).scalars().first()
                if agent and agent.last_seen and agent.last_seen >= since:
                    return True
        except Exception as exc:
            logger.debug("Warten auf Logzeile: %s", exc)
    return False


async def _fail(state: OnboardState, device_id: int, actor: str, source_ip: str, message: str) -> None:
    from .database import async_session

    state.error = message
    state.ok = False
    state.step(message, ok=False)
    # Auch ins Container-Log: Bisher stand ein Abbruch nur in der Oberflaeche und
    # im Tagebuch, `docker logs rimperio-app-backend` blieb leer.
    logger.warning("Einbinden von Gerät %s fehlgeschlagen: %s", device_id, message)
    try:
        async with async_session() as session:
            await journal.record(
                session, category="discovery", event="onboard.failed", level="warning",
                message=f"Einbinden von Gerät {device_id} fehlgeschlagen: {message.splitlines()[0]}",
                actor=actor, source_ip=source_ip, ok=False)
    except Exception:
        pass
