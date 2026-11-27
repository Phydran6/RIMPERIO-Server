# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Geheimnisse lokal verschluesselt ablegen (AES-256-GCM)
# ==============================================================================
"""
Ein kleiner Tresor fuer Geheimnisse, die RIMPERIO selbst behalten muss.

Wofuer: Der Zugang zu Vaultwarden (Master-Passwort, API-Schluessel) kann nicht
in Vaultwarden selbst liegen - irgendwo muss der erste Schluessel wohnen. Und
solange kein Vaultwarden angebunden ist, liegen NAS-Anmeldungen, Backup-
Passwoerter und Zugaenge zu Sicherungszielen hier. Nie im Klartext in der
Datenbank.

Schluessel (in dieser Reihenfolge):

1. Umgebungsvariable ``RIMPERIO_SECRET_KEY`` (beliebiger Text, mindestens 32
   Zeichen) - fuer Installationen, die den Schluessel ausserhalb halten wollen.
2. Datei ``<DATA_DIR>/secret.key`` (32 Zufallsbytes, Rechte 0600). Wird beim
   ersten Gebrauch angelegt und liegt im Volume ``app_data``.

Ein Chiffrat sieht so aus: ``sb1:<base64(nonce|ciphertext|tag)>``. Werte ohne
dieses Praefix gelten als Altbestand im Klartext und werden unveraendert
zurueckgegeben - so bleiben vorhandene Eintraege lesbar.

Wichtig: Wer das Volume ``app_data`` verliert, verliert die lokal abgelegten
Geheimnisse. Mit Vaultwarden passiert das nicht - dort liegt alles ausser dem
Zugang selbst.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rimperio.secretbox")

PREFIX = "sb1:"
DATA_DIR = Path(os.getenv("RIMPERIO_DATA_DIR", "/app/data"))
KEY_FILE = DATA_DIR / "secret.key"

_key: Optional[bytes] = None


class SecretBoxError(Exception):
    """Entschluesseln ging nicht (falscher Schluessel, beschaedigt)."""


def _load_key() -> bytes:
    global _key
    if _key is not None:
        return _key
    env = os.getenv("RIMPERIO_SECRET_KEY", "").strip()
    if env:
        if len(env) < 32:
            logger.warning("RIMPERIO_SECRET_KEY ist kürzer als 32 Zeichen - bitte verlängern.")
        _key = hashlib.sha256(b"rimperio-secretbox|" + env.encode("utf-8")).digest()
        return _key
    try:
        raw = KEY_FILE.read_bytes()
        if len(raw) >= 32:
            _key = raw[:32]
            return _key
    except FileNotFoundError:
        pass
    except OSError as exc:                                          # defensiv
        logger.error("Schlüsseldatei %s nicht lesbar: %s", KEY_FILE, exc)
    raw = secrets.token_bytes(32)
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        logger.info("Neuer Schlüssel für lokale Geheimnisse angelegt (%s).", KEY_FILE)
    except FileExistsError:
        # Ein anderer Prozess war schneller - dessen Schluessel gilt.
        raw = KEY_FILE.read_bytes()[:32]
    except OSError as exc:
        # Kein beschreibbares Datenverzeichnis (z. B. Test ohne Volume): dann
        # wenigstens stabil aus dem JWT-Geheimnis ableiten.
        from .config import settings
        logger.warning("Schlüsseldatei nicht anlegbar (%s) - leite aus JWT_SECRET ab.", exc)
        raw = hashlib.sha256(b"rimperio-secretbox|jwt|" + settings.jwt_secret.encode("utf-8")).digest()
    _key = raw
    return _key


def reset_cache() -> None:
    """Nur fuer Tests: Schluessel neu lesen."""
    global _key
    _key = None


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def encrypt(plain: Optional[str]) -> str:
    """Leerer Wert bleibt leer (\"nicht gesetzt\" soll erkennbar bleiben)."""
    if not plain:
        return ""
    if is_encrypted(plain):
        return plain
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_load_key()).encrypt(nonce, plain.encode("utf-8"), b"rimperio")
    return PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(value: Optional[str]) -> str:
    if not value:
        return ""
    if not is_encrypted(value):
        return value                    # Altbestand im Klartext
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        raw = base64.b64decode(value[len(PREFIX):])
        return AESGCM(_load_key()).decrypt(raw[:12], raw[12:], b"rimperio").decode("utf-8")
    except (InvalidTag, ValueError) as exc:
        raise SecretBoxError("Lokales Geheimnis nicht lesbar - wurde secret.key ausgetauscht?") from exc
