# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Verbindung zur Admin-API von Caddy (Unix-Socket)
# ==============================================================================
"""
Die Admin-API von Caddy kann die komplette Proxy-Konfiguration austauschen:
Zertifikate, Weiterleitungen, Ziele. Sie hat keine Anmeldung.

Frueher lauschte sie auf ``0.0.0.0:2019`` im Docker-Netz. Dort haengen aber
auch die Zusatzdienste (n8n, Open WebUI, Portainer, Postfix). Ein einziger
HTTP-Knoten in einem n8n-Workflow haette genuegt, um den Proxy umzubauen und
saemtlichen Verkehr samt Passwoertern abzugreifen.

Jetzt lauscht sie auf einem Unix-Socket in einem eigenen Volume
(``caddy_admin``), das nur Caddy und das Backend eingehaengt haben. Uebers
Netz ist sie nicht mehr erreichbar.

Rueckfall: Gibt es den Socket nicht (aeltere Compose-Datei, eigener Aufbau),
geht der Helfer wie frueher ueber ``CADDY_ADMIN_URL``.

Caddy prueft auch am Socket den Host-Kopf und laesst dort nur ``127.0.0.1``
zu - deshalb diese Basisadresse.
"""

from __future__ import annotations

import os
import re

import httpx

from .config import settings

SOCKET_PATH = os.getenv("CADDY_ADMIN_SOCKET", "/caddy-admin/admin.sock")

# So steht die Admin-Adresse in jeder Caddyfile, die das Backend laedt.
# Caddy-Schreibweise: "unix/" + absoluter Pfad.
SOCKET_LISTEN = f"unix/{SOCKET_PATH}"
TCP_LISTEN = "0.0.0.0:2019"

_SOCKET_BASE = "http://127.0.0.1"


def socket_available() -> bool:
    return bool(SOCKET_PATH) and os.path.exists(SOCKET_PATH)


def socket_volume_mounted() -> bool:
    """Ist das Volume fuer den Socket da? (Dann gehoert die API dorthin.)"""
    return bool(SOCKET_PATH) and os.path.isdir(os.path.dirname(SOCKET_PATH))


def listen_address() -> str:
    """Wohin die Admin-API in der naechsten geladenen Config gehoert.

    Haengt das Socket-Volume im Backend, gehoert die API immer auf den Socket -
    auch wenn die Datei gerade fehlt (Caddy startet noch). Nur ganz ohne
    Volume (alter Aufbau) bleibt es beim TCP-Port.
    """
    return SOCKET_LISTEN if (socket_available() or socket_volume_mounted()) else TCP_LISTEN


def describe() -> str:
    """Fuer Meldungen und die Diagnose: wo wird Caddy angesprochen?"""
    return f"unix:{SOCKET_PATH}" if socket_available() else settings.caddy_admin_url.rstrip("/")


def client(timeout: float = 10.0) -> httpx.AsyncClient:
    """HTTP-Client fuer die Admin-API. Pfade relativ angeben ("/config/")."""
    if socket_available():
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=SOCKET_PATH),
            base_url=_SOCKET_BASE, timeout=timeout)
    return httpx.AsyncClient(base_url=settings.caddy_admin_url.rstrip("/"), timeout=timeout)


# "admin <adresse>" in den globalen Optionen, optional mit eigenem Block danach.
_ADMIN_LINE = re.compile(r"^(?P<indent>[ \t]*)admin(?:[ \t]+(?P<addr>[^\s{]+))?(?P<rest>[ \t]*\{?)[ \t]*$",
                         re.MULTILINE)


def enforce_admin(caddyfile: str) -> str:
    """Setzt die Admin-Adresse in einer Caddyfile auf den richtigen Wert.

    Wozu: Gespeicherte Konfigurationen und Eingaben im Editor tragen oft noch
    ``admin 0.0.0.0:2019``. Ginge die so an Caddy, laege die Admin-API wieder
    offen im Netz - und das Backend wuerde den Socket nicht mehr finden. Darum
    bekommt jede Caddyfile vor dem Laden die richtige Adresse, ob sie eine
    globale Option hatte oder nicht. ``admin off`` wird ebenfalls ersetzt: ohne
    Admin-API koennte RIMPERIO den Proxy nie wieder einstellen.
    """
    text = (caddyfile or "").lstrip("﻿")
    stripped = text.lstrip()
    wanted = listen_address()
    # Ein globaler Block ist "{" gefolgt von Leerraum - "{$DOMAIN} {" waere
    # dagegen eine Seitenadresse mit Platzhalter.
    if stripped.startswith("{") and (len(stripped) == 1 or stripped[1] in " \t\r\n}"):
        start = text.index("{")
        depth, end = 0, None
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is not None:
            block = text[start:end + 1]
            if _ADMIN_LINE.search(block):
                block = _ADMIN_LINE.sub(
                    lambda m: f"{m.group('indent')}admin {wanted}{m.group('rest')}", block, count=1)
            else:
                block = "{\n    admin " + wanted + "\n" + block[1:].lstrip("\n")
            return text[:start] + block + text[end + 1:]
    return "{\n    admin " + wanted + "\n}\n\n" + text
