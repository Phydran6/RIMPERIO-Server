# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Paket fuer Synology DSM 7 (.spk): der RIMPERIO-Agent
#               als Paket, sichtbar und steuerbar im DSM-Paketzentrum.
# ==============================================================================
"""
RIMPERIO-Agent als DSM-Paket
============================

Bisher sprach RIMPERIO mit dem NAS nur von aussen (DSM-API per HTTPS) - auf dem
NAS selbst war nichts zu sehen. Dieses Modul baut ein Paket fuer das
Paketzentrum (DSM 7.0 und neuer):

* **Sichtbar** im Paketzentrum unter "Installiert" als "RIMPERIO Agent" -
  starten, anhalten, deinstallieren wie jedes andere Paket.
* **Vorbelegt**: Server-Adresse und Einladung stehen schon im Paket (der
  Assistent beim Installieren zeigt sie und laesst sie aendern). Der Agent
  meldet sich beim ersten Start selbst an und erscheint unter Geraete -> Rechner.
* **Laeuft als Paketbenutzer** (DSM 7 erlaubt Drittanbietern kein root) -
  Live-Werte, Inventur, Befehle, Checks und Logs gehen; DSM-Updates und
  Paket-Updates laufen weiter ueber die DSM-API (Geraete -> NAS).
* **Architektur**: x86_64 (Intel/AMD), armv8 (aarch64), armv7 - das Paket ist
  "noarch" und waehlt das passende Programm beim Start. Die Programme kommen
  aus dem GitHub-Release; erreicht der Server GitHub nicht, laedt das NAS sie
  beim ersten Start selbst.

Installieren: DSM -> Paketzentrum -> "Manuelle Installation" -> .spk waehlen.
Dafuer muss unter Paketzentrum -> Einstellungen -> "Vertrauensebene" der Punkt
"Synology Inc. und vertrauenswuerdige Herausgeber" bzw. "Alle Herausgeber"
gewaehlt sein (das Paket ist nicht von Synology signiert).
"""

from __future__ import annotations

import io
import json
import logging
import os
import tarfile
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

from .config import settings

logger = logging.getLogger("rimperio.dsmpackage")

PACKAGE = "RIMPERIOAgent"
RELEASE_BASE = "https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download"
ARCHES = ("amd64", "arm64", "armv7")
CACHE = Path(os.getenv("RIMPERIO_AGENT_CACHE", "/tmp/rimperio-agent-cache"))
CACHE_HOURS = 12
ASSETS = Path(__file__).parent / "assets"


def _spk_version() -> str:
    """DSM will Ziffern und Punkte, dazu -<build>: 0.4.0 -> 0.4.0-0001.
    DSM vergleicht zuerst die Version selbst, die Build-Nummer bleibt fest."""
    v = (settings.app_version or "0.0.0").split("-")[0].split("+")[0]
    parts = ([p for p in v.split(".") if p.isdigit()] + ["0", "0", "0"])[:3]
    return ".".join(parts) + "-0001"


async def _binary(arch: str) -> Optional[bytes]:
    """Agent-Programm aus dem Release (zwischengespeichert)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"rimperio-agent-linux-{arch}"
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_HOURS * 3600 and path.stat().st_size > 1_000_000:
        return path.read_bytes()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
            r = await c.get(f"{RELEASE_BASE}/rimperio-agent-linux-{arch}")
        if r.status_code == 200 and len(r.content) > 1_000_000 and r.content[:4] == b"\x7fELF":
            path.write_bytes(r.content)
            return r.content
        logger.warning("Agent-Programm %s: HTTP %s", arch, r.status_code)
    except httpx.HTTPError as exc:
        logger.warning("Agent-Programm %s nicht geladen: %s", arch, exc)
    if path.exists():
        return path.read_bytes()            # alter Stand ist besser als keiner
    return None


def _add(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(time.time())
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


START_STOP = r"""#!/bin/sh
# RIMPERIO Agent - Start/Stopp fuer das DSM-Paketzentrum.
PKGVAR="${SYNOPKG_PKGVAR:-/var/packages/RIMPERIOAgent/var}"
PKGDEST="${SYNOPKG_PKGDEST:-/var/packages/RIMPERIOAgent/target}"
PID="$PKGVAR/agent.pid"
LOG="$PKGVAR/agent.log"
BASE_URL="__BASE_URL__"
export RIMPERIO_HOME="$PKGVAR"
# Paketform: Inventur kennzeichnet das NAS, Deinstallation aus RIMPERIO loescht nur die Anmeldung.
export RIMPERIO_PACKAGE=synology

arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo amd64 ;;
    aarch64|arm64|armv8*) echo arm64 ;;
    armv7*|armv8l) echo armv7 ;;
    *) echo "" ;;
  esac
}

binary() {
  A="$(arch)"
  [ -n "$A" ] || { echo "Architektur $(uname -m) wird nicht unterstützt" >> "$LOG"; return 1; }
  B="$PKGVAR/bin/rimperio-agent"
  if [ ! -x "$B" ]; then
    mkdir -p "$PKGVAR/bin"
    if [ -f "$PKGDEST/bin/rimperio-agent-linux-$A" ]; then
      cp "$PKGDEST/bin/rimperio-agent-linux-$A" "$B"
    else
      echo "Lade rimperio-agent-linux-$A ..." >> "$LOG"
      (curl -fsSL -o "$B" "$BASE_URL/rimperio-agent-linux-$A" || wget -qO "$B" "$BASE_URL/rimperio-agent-linux-$A") >> "$LOG" 2>&1 || { rm -f "$B"; return 1; }
    fi
    chmod 0755 "$B"
  fi
  echo "$B"
}

running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "$1" in
  start)
    running && exit 0
    B="$(binary)" || exit 1
    mkdir -p "$PKGVAR/etc" "$PKGVAR/var"
    [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ] && mv -f "$LOG" "$LOG.1"
    nohup "$B" run >> "$LOG" 2>&1 &
    echo $! > "$PID"
    exit 0 ;;
  stop)
    if running; then kill "$(cat "$PID")"; sleep 2; running && kill -9 "$(cat "$PID")"; fi
    rm -f "$PID"; exit 0 ;;
  status)
    running && exit 0 || exit 3 ;;
  log)
    echo "$LOG"; exit 0 ;;
  *) exit 1 ;;
esac
"""

POSTINST = r"""#!/bin/sh
# Nach dem Installieren: Konfiguration mit Server und Einladung anlegen (nur beim
# ersten Mal - ein Upgrade behaelt die Anmeldung des Geraets).
PKGVAR="${SYNOPKG_PKGVAR:-/var/packages/RIMPERIOAgent/var}"
CONF="$PKGVAR/etc/agent.json"
mkdir -p "$PKGVAR/etc" "$PKGVAR/var" "$PKGVAR/bin"
rm -f "$PKGVAR/bin/rimperio-agent"     # neues Programm aus dem Paket uebernehmen
if [ ! -f "$CONF" ]; then
  SERVER="${wizard_server:-__SERVER__}"
  SECRET="${wizard_secret:-__SECRET__}"
  SITE="${wizard_site:-__SITE__}"
  INSECURE="__INSECURE__"
  [ "${wizard_insecure:-}" = "true" ] && INSECURE=true
  esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  cat > "$CONF" <<EOF
{"server_url": "$(esc "$SERVER")", "enroll_secret": "$(esc "$SECRET")", "site": "$(esc "$SITE")",
 "description": "Synology DSM (Paket RIMPERIO Agent)", "interval": 60, "insecure": $INSECURE}
EOF
  chmod 600 "$CONF"
fi
exit 0
"""

NOOP = "#!/bin/sh\nexit 0\n"

PREUNINST = r"""#!/bin/sh
# Beim Deinstallieren: beim Server abmelden (Eintrag bleibt, Agent verschwindet).
PKGVAR="${SYNOPKG_PKGVAR:-/var/packages/RIMPERIOAgent/var}"
if [ "${pkgwizard_keep:-}" != "true" ] && [ "$SYNOPKG_PKG_STATUS" = "UNINSTALL" ] && [ -x "$PKGVAR/bin/rimperio-agent" ]; then
  RIMPERIO_HOME="$PKGVAR" "$PKGVAR/bin/rimperio-agent" cleanup >/dev/null 2>&1 || true
fi
exit 0
"""


def _wizard(server: str, secret: str, site: str) -> bytes:
    return json.dumps([{
        "step_title": "Verbindung zu RIMPERIO",
        "items": [{
            "type": "textfield", "desc": "Diese Angaben kommen aus RIMPERIO und passen schon. Ändern nur, wenn "
                                         "das NAS den Server unter einer anderen Adresse erreicht.",
            "subitems": [
                {"key": "wizard_server", "desc": "Server-Adresse", "defaultValue": server,
                 "validator": {"allowBlank": False}},
                {"key": "wizard_secret", "desc": "Einladung (Zugangsschlüssel)", "defaultValue": secret,
                 "validator": {"allowBlank": False}},
                {"key": "wizard_site", "desc": "Standort", "defaultValue": site or "Standard"},
            ]}, {
            "type": "singleselect", "desc": "Zertifikat des Servers",
            "subitems": [
                {"key": "wizard_secure", "desc": "prüfen (empfohlen)", "defaultValue": True},
                {"key": "wizard_insecure", "desc": "nicht prüfen (selbst signiertes Zertifikat)", "defaultValue": False},
            ]}],
    }], ensure_ascii=False, indent=1).encode()


async def build_spk(server: str, secret: str, site: str = "Standard", insecure: bool = False) -> Dict[str, object]:
    """Baut das .spk. Liefert {"data": bytes, "filename": str, "binaries": [...], "version": str}."""
    version = _spk_version()
    binaries = {}
    for arch in ARCHES:
        data = await _binary(arch)
        if data:
            binaries[arch] = data

    # package.tgz: Programme (falls vorhanden)
    pkg = io.BytesIO()
    with tarfile.open(fileobj=pkg, mode="w:gz") as t:
        for arch, data in binaries.items():
            _add(t, f"bin/rimperio-agent-linux-{arch}", data, 0o755)
        _add(t, "README.txt", ("RIMPERIO Agent für Synology DSM\nKonfiguration und Protokoll: "
                               "/var/packages/RIMPERIOAgent/var\n").encode())
    info = {
        "package": PACKAGE, "version": version, "os_min_ver": "7.0-40000", "arch": "noarch",
        "displayname": "RIMPERIO Agent", "maintainer": "RIMPERIO", "distributor": "RIMPERIO",
        "description": "Verbindet dieses NAS mit RIMPERIO: Live-Werte, Inventur, Befehle, Checks und Logs. "
                       "Das NAS erscheint in RIMPERIO unter Geräte → Rechner.",
        "description_enu": "Connects this NAS to RIMPERIO: live metrics, inventory, commands, checks and logs.",
        "support_url": "https://github.com/Phydran6/RIMPERIO-Server", "thirdparty": "yes",
        "startable": "yes", "ctl_stop": "yes", "silent_upgrade": "yes", "install_dep_services": "",
        "checksum": "",
    }
    info_txt = "".join(f'{k}="{v}"\n' for k, v in info.items() if v != "")

    def fill(text: str) -> bytes:
        return (text.replace("__SERVER__", server).replace("__SECRET__", secret).replace("__SITE__", site or "Standard")
                .replace("__INSECURE__", "true" if insecure else "false").replace("__BASE_URL__", RELEASE_BASE)).encode()

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as spk:
        _add(spk, "INFO", info_txt.encode())
        _add(spk, "package.tgz", pkg.getvalue())
        _add(spk, "conf/privilege", json.dumps({"defaults": {"run-as": "package"}}).encode())
        _add(spk, "conf/resource", b"{}")
        for name in ("preinst", "preuninst", "postuninst", "preupgrade", "postupgrade"):
            _add(spk, f"scripts/{name}", (PREUNINST.encode() if name == "preuninst" else NOOP.encode()), 0o755)
        _add(spk, "scripts/postinst", fill(POSTINST), 0o755)
        _add(spk, "scripts/start-stop-status", fill(START_STOP), 0o755)
        _add(spk, "WIZARD_UIFILES/install_uifile", _wizard(server, secret, site))
        _add(spk, "WIZARD_UIFILES/install_uifile_ger", _wizard(server, secret, site))
        for size, name in ((72, "PACKAGE_ICON.PNG"), (256, "PACKAGE_ICON_256.PNG")):
            icon = ASSETS / f"spk-icon-{size}.png"
            if icon.exists():
                _add(spk, name, icon.read_bytes())
    return {"data": out.getvalue(), "filename": f"RIMPERIO-Agent-{version}-noarch.spk",
            "binaries": sorted(binaries), "version": version}
