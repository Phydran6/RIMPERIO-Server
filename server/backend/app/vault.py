# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Zugangsdaten: Vaultwarden per HTTPS (Bitwarden-API)
#               mit lokalem, verschluesseltem Ersatzspeicher
# ==============================================================================
"""
Alle Zugaenge, die RIMPERIO braucht - NAS-Anmeldungen samt TOTP, Passwoerter
der Sicherungen, Zugaenge zu Sicherungszielen -, liegen an EINER Stelle:

* **Vaultwarden** (empfohlen). RIMPERIO spricht den Tresor ueber dieselbe
  HTTPS-Schnittstelle an wie die offiziellen Bitwarden-Apps: anmelden,
  Eintraege lesen, anlegen, aendern. Ver- und entschluesselt wird hier, mit dem
  Schluessel des Kontos - Vaultwarden selbst sieht nie einen Klartext. Die
  Eintraege landen im Ordner "RIMPERIO" (oder in einer Sammlung einer
  Organisation) und sind dort wie jeder andere Eintrag sichtbar und
  bearbeitbar. Geaendert in Vaultwarden = geaendert fuer RIMPERIO.
* **Lokal** (Ersatz, solange kein Tresor angebunden ist): AES-256-GCM mit dem
  Schluessel aus ``secretbox``. Ein Knopf zieht spaeter alles nach Vaultwarden.

Anmeldung am Tresor
-------------------
Bevorzugt mit dem **persoenlichen API-Schluessel** (client_id ``user.…`` und
client_secret, in Vaultwarden unter Konto -> Sicherheit -> Schluessel). Der
umgeht die Zwei-Faktor-Abfrage, die ein Dienst nicht beantworten kann. Zum
Entschluesseln braucht es trotzdem das Master-Passwort. Ohne API-Schluessel
geht auch E-Mail + Master-Passwort; ist Zwei-Faktor (TOTP) aktiv, wird beim
Einrichten einmal ein Code abgefragt und das "Geraet merken"-Token behalten.

Kryptografie (wie Bitwarden)
----------------------------
* Master-Schluessel: PBKDF2-SHA256(Passwort, E-Mail, n) oder Argon2id.
* Anmelde-Hash: PBKDF2-SHA256(Master-Schluessel, Passwort, 1).
* Gestreckt per HKDF-Expand ("enc", "mac") -> entschluesselt den
  Konto-Schluessel (64 Byte: 32 AES + 32 HMAC).
* Felder sind EncStrings Typ 2: ``2.iv|daten|mac`` (AES-256-CBC + HMAC-SHA256).
* Organisationen: Schluessel Typ 4 (RSA-OAEP-SHA1) mit dem privaten
  Konto-Schluessel.

Der Zugang zum Tresor selbst (Master-Passwort, client_secret) liegt mit
secretbox verschluesselt in der Einstellung ``vault_config``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import secretbox
from .models import Setting, StoredSecret

logger = logging.getLogger("rimperio.vault")

CONFIG_KEY = "vault_config"
FOLDER_DEFAULT = "RIMPERIO"
DEVICE_TYPE = 8            # "Linux Desktop" - Vaultwarden erwartet irgendeinen gueltigen Typ
CACHE_SECONDS = 300

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "url": "",                 # https://vault.example.de
    "email": "",
    "master_password": "",     # secretbox
    "client_id": "",           # user.xxxxxxxx (persoenlicher API-Schluessel)
    "client_secret": "",       # secretbox
    "remember_token": "",      # secretbox - "Geraet merken" nach TOTP
    "device_id": "",
    "organization_id": "",     # leer = persoenlicher Tresor
    "collection_id": "",       # Pflicht bei Organisation
    "folder": FOLDER_DEFAULT,  # nur persoenlicher Tresor
    "verify_tls": True,
    "allow_http": False,       # nur fuer Tests im LAN
    "last_ok_at": None,
    "last_error": "",
}
SECRET_FIELDS = ("master_password", "client_secret", "remember_token")


class VaultError(Exception):
    """Tresor nicht erreichbar, Anmeldung abgelehnt oder Eintrag fehlt."""


class VaultNeedsTotp(VaultError):
    """Das Konto verlangt einen Zwei-Faktor-Code."""


# =============================================================================
# Kryptografie
# =============================================================================
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def make_master_key(password: str, email: str, kdf: int, iterations: int,
                    memory: Optional[int] = None, parallelism: Optional[int] = None) -> bytes:
    email = email.strip().lower()
    if kdf == 0:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), email.encode("utf-8"),
                                   int(iterations), dklen=32)
    if kdf == 1:
        try:
            from argon2.low_level import Type, hash_secret_raw
        except ImportError as exc:                                  # defensiv
            raise VaultError("Das Konto nutzt Argon2id - dafür fehlt das Paket argon2-cffi.") from exc
        salt = hashlib.sha256(email.encode("utf-8")).digest()
        return hash_secret_raw(password.encode("utf-8"), salt, time_cost=int(iterations),
                               memory_cost=int(memory or 64) * 1024, parallelism=int(parallelism or 4),
                               hash_len=32, type=Type.ID)
    raise VaultError(f"Unbekanntes Schlüsselverfahren (KDF {kdf}).")


def master_password_hash(master_key: bytes, password: str) -> str:
    return _b64(hashlib.pbkdf2_hmac("sha256", master_key, password.encode("utf-8"), 1, dklen=32))


def _hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def stretch(master_key: bytes) -> bytes:
    """64 Byte: enc (32) + mac (32)."""
    return _hkdf_expand(master_key, b"enc") + _hkdf_expand(master_key, b"mac")


def enc_string(plain: bytes, key: bytes) -> str:
    """EncString Typ 2 (AES-256-CBC, HMAC-SHA256)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    enc_key, mac_key = key[:32], key[32:64]
    iv = secrets.token_bytes(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain) + padder.finalize()
    enc = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    return f"2.{_b64(iv)}|{_b64(ct)}|{_b64(mac)}"


def dec_string(value: str, key: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if not value:
        return b""
    try:
        etype, rest = value.split(".", 1)
    except ValueError as exc:
        raise VaultError("Unbekanntes Format eines Tresor-Feldes.") from exc
    if etype != "2":
        raise VaultError(f"Tresor-Feld mit Verfahren {etype} wird nicht unterstützt.")
    iv_s, ct_s, mac_s = rest.split("|")
    iv, ct, mac = _unb64(iv_s), _unb64(ct_s), _unb64(mac_s)
    want = hmac.new(key[32:64], iv + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(want, mac):
        raise VaultError("Prüfsumme eines Tresor-Feldes stimmt nicht (falscher Schlüssel?).")
    dec = Cipher(algorithms.AES(key[:32]), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return unpad.update(padded) + unpad.finalize()


def dec_text(value: Optional[str], key: bytes) -> str:
    return dec_string(value, key).decode("utf-8") if value else ""


def enc_text(value: Optional[str], key: bytes) -> Optional[str]:
    return enc_string(value.encode("utf-8"), key) if value else None


def rsa_decrypt(value: str, private_der: bytes) -> bytes:
    """EncString Typ 4: RSA-2048-OAEP-SHA1 (Organisationsschluessel)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as apad
    etype, rest = value.split(".", 1)
    if etype not in ("4", "6"):
        raise VaultError(f"Organisationsschlüssel mit Verfahren {etype} wird nicht unterstützt.")
    priv = serialization.load_der_private_key(private_der, password=None)
    return priv.decrypt(_unb64(rest.split("|")[0]),
                        apad.OAEP(mgf=apad.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None))


def _g(d: Optional[dict], name: str, default: Any = None) -> Any:
    """Feld unabhaengig von der Schreibweise lesen (Vaultwarden: camelCase, alt: PascalCase)."""
    if not isinstance(d, dict):
        return default
    if name in d:
        return d[name]
    alt = name[:1].upper() + name[1:]
    if alt in d:
        return d[alt]
    low = name.lower()
    for k, v in d.items():
        if k.lower() == low:
            return v
    return default


# =============================================================================
# Einstellungen
# =============================================================================
async def load_config(db: AsyncSession) -> dict:
    row = (await db.execute(select(Setting).where(Setting.key == CONFIG_KEY))).scalar_one_or_none()
    cfg = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        cfg.update({k: v for k, v in row.value.items() if k in DEFAULT_CONFIG})
    for k in SECRET_FIELDS:
        try:
            cfg[k] = secretbox.decrypt(cfg.get(k) or "")
        except secretbox.SecretBoxError:
            cfg[k] = ""
    return cfg


async def save_config(db: AsyncSession, cfg: dict) -> dict:
    stored = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}
    for k in SECRET_FIELDS:
        stored[k] = secretbox.encrypt(stored.get(k) or "")
    row = (await db.execute(select(Setting).where(Setting.key == CONFIG_KEY))).scalar_one_or_none()
    if row:
        row.value = stored
    else:
        db.add(Setting(key=CONFIG_KEY, value=stored, description="Vaultwarden (Zugangsdaten)"))
    await db.commit()
    _SESSION.clear()
    return cfg


def public_config(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}
    out["master_password_set"] = bool(cfg.get("master_password"))
    out["client_secret_set"] = bool(cfg.get("client_secret"))
    out["remembered"] = bool(cfg.get("remember_token"))
    return out


def normalize_url(raw: str, allow_http: bool = False) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raise VaultError("Adresse des Tresors fehlt.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise VaultError("Adresse nicht lesbar - bitte z. B. https://vault.firma.de angeben.")
    if parts.scheme == "http" and not allow_http:
        raise VaultError("Vaultwarden nur über https:// anbinden (http nur mit „unverschlüsselt erlauben“).")
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}{path}"


# =============================================================================
# Sitzung
# =============================================================================
@dataclass
class _Session:
    base: str = ""
    token: str = ""
    expires: float = 0.0
    user_key: bytes = b""
    private_key: bytes = b""
    org_keys: Dict[str, bytes] = field(default_factory=dict)
    folder_id: str = ""
    user_id: str = ""
    cache: Dict[str, Tuple[float, dict]] = field(default_factory=dict)

    def clear(self) -> None:
        self.__init__()


_SESSION = _Session()
_lock = asyncio.Lock()


def _client(cfg: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=bool(cfg.get("verify_tls", True)), timeout=20.0,
                             headers={"Accept": "application/json",
                                      "Bitwarden-Client-Name": "rimperio",
                                      "Bitwarden-Client-Version": "2024.6.0",
                                      "Device-Type": str(DEVICE_TYPE)})


def _net_error(base: str, exc: Exception) -> VaultError:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify" in text.lower():
        return VaultError(f"Vaultwarden ({base}): Zertifikat nicht vertrauenswürdig – "
                          "„Zertifikat prüfen“ ausschalten oder ein gültiges Zertifikat einrichten.")
    if isinstance(exc, httpx.TimeoutException):
        return VaultError(f"Vaultwarden ({base}) antwortet nicht (Zeitüberschreitung).")
    return VaultError(f"Vaultwarden ({base}) nicht erreichbar: {text or exc.__class__.__name__}")


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return (r.text or r.reason_phrase or "")[:300]
    for key in ("error_description", "message", "Message", "error"):
        val = body.get(key) if isinstance(body, dict) else None
        if isinstance(val, str) and val:
            return val
    em = _g(body, "errorModel") or _g(body, "ErrorModel")
    if isinstance(em, dict) and _g(em, "message"):
        return _g(em, "message")
    return json.dumps(body)[:300]


async def _login(cfg: dict, totp: str = "") -> dict:
    """Anmelden und den Konto-Schluessel entschluesseln. Gibt die Token-Antwort zurueck."""
    base = normalize_url(cfg.get("url", ""), bool(cfg.get("allow_http")))
    email = (cfg.get("email") or "").strip().lower()
    password = cfg.get("master_password") or ""
    if not email or not password:
        raise VaultError("Für Vaultwarden fehlen E-Mail oder Master-Passwort (zum Entschlüsseln nötig).")
    device_id = cfg.get("device_id") or str(uuid.uuid4())
    cfg["device_id"] = device_id
    async with _client(cfg) as c:
        try:
            r = await c.post(base + "/identity/accounts/prelogin", json={"email": email})
            if r.status_code == 404:
                r = await c.post(base + "/api/accounts/prelogin", json={"email": email})
        except httpx.HTTPError as exc:
            raise _net_error(base, exc) from exc
        if r.status_code >= 400:
            raise VaultError(f"Vaultwarden: Anmeldedaten nicht abrufbar ({r.status_code}): {_error_text(r)}")
        pre = r.json()
        kdf = int(_g(pre, "kdf", 0) or 0)
        master_key = make_master_key(password, email, kdf, int(_g(pre, "kdfIterations", 600000) or 600000),
                                     _g(pre, "kdfMemory"), _g(pre, "kdfParallelism"))
        form = {"scope": "api offline_access", "deviceIdentifier": device_id,
                "deviceName": "RIMPERIO", "deviceType": str(DEVICE_TYPE)}
        if cfg.get("client_id") and cfg.get("client_secret"):
            form.update({"grant_type": "client_credentials", "client_id": cfg["client_id"].strip(),
                         "client_secret": cfg["client_secret"].strip(), "scope": "api"})
        else:
            form.update({"grant_type": "password", "username": email, "client_id": "cli",
                         "password": master_password_hash(master_key, password)})
            if totp:
                form.update({"twoFactorToken": totp.replace(" ", ""), "twoFactorProvider": "0",
                             "twoFactorRemember": "1"})
            elif cfg.get("remember_token"):
                form.update({"twoFactorToken": cfg["remember_token"], "twoFactorProvider": "5"})
        headers = {"Auth-Email": base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")}
        try:
            r = await c.post(base + "/identity/connect/token", data=form, headers=headers)
        except httpx.HTTPError as exc:
            raise _net_error(base, exc) from exc
    body: dict = {}
    try:
        body = r.json()
    except ValueError:
        pass
    if r.status_code >= 400:
        providers = _g(body, "twoFactorProviders") or _g(body, "TwoFactorProviders2")
        if providers:
            if form.get("twoFactorProvider") == "5":
                cfg["remember_token"] = ""
            raise VaultNeedsTotp("Das Vaultwarden-Konto verlangt einen Zwei-Faktor-Code. Einmal eingeben – "
                                 "danach merkt sich RIMPERIO das Gerät. Besser: persönlichen API-Schlüssel hinterlegen.")
        msg = _error_text(r)
        if "invalid_client" in msg or "client" in msg.lower() and form["grant_type"] == "client_credentials":
            raise VaultError(f"Vaultwarden: API-Schlüssel abgelehnt ({msg}).")
        raise VaultError(f"Vaultwarden: Anmeldung abgelehnt – {msg}")
    if _g(body, "TwoFactorToken"):
        cfg["remember_token"] = _g(body, "TwoFactorToken")
    protected = _g(body, "Key") or _g(body, "key")
    if not protected:
        raise VaultError("Vaultwarden hat keinen Konto-Schlüssel geliefert.")
    try:
        user_key = dec_string(protected, stretch(master_key))
    except VaultError as exc:
        raise VaultError("Master-Passwort falsch – der Konto-Schlüssel lässt sich nicht entschlüsseln.") from exc
    if len(user_key) != 64:
        raise VaultError("Unerwartete Länge des Konto-Schlüssels.")
    _SESSION.clear()
    _SESSION.base = base
    _SESSION.token = _g(body, "access_token", "")
    _SESSION.expires = time.time() + max(60, int(_g(body, "expires_in", 3600) or 3600) - 60)
    _SESSION.user_key = user_key
    _SESSION.user_id = _jwt_sub(_SESSION.token)
    priv = _g(body, "PrivateKey") or _g(body, "privateKey")
    if priv:
        try:
            _SESSION.private_key = dec_string(priv, user_key)
        except VaultError:
            _SESSION.private_key = b""
    return body


def _jwt_sub(token: str) -> str:
    """Benutzer-ID aus dem Zugriffstoken (fuer das Feld encryptedFor)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return str(json.loads(base64.urlsafe_b64decode(part)).get("sub") or "")
    except (IndexError, ValueError):
        return ""


async def _ensure(cfg: dict) -> _Session:
    if _SESSION.token and _SESSION.expires > time.time() and _SESSION.user_key:
        return _SESSION
    await _login(cfg)
    return _SESSION


async def _request(cfg: dict, method: str, path: str, **kw) -> Any:
    s = await _ensure(cfg)
    async with _client(cfg) as c:
        try:
            r = await c.request(method, s.base + path, headers={"Authorization": f"Bearer {s.token}"}, **kw)
            if r.status_code == 401:
                s.token = ""
                s = await _ensure(cfg)
                r = await c.request(method, s.base + path, headers={"Authorization": f"Bearer {s.token}"}, **kw)
        except httpx.HTTPError as exc:
            raise _net_error(s.base, exc) from exc
    if r.status_code == 404:
        raise VaultError("Eintrag in Vaultwarden nicht gefunden (gelöscht?).")
    if r.status_code >= 400:
        raise VaultError(f"Vaultwarden ({r.status_code}): {_error_text(r)}")
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


async def _sync(cfg: dict) -> dict:
    return await _request(cfg, "GET", "/api/sync?excludeDomains=true")


async def _org_key(cfg: dict, org_id: str) -> bytes:
    s = await _ensure(cfg)
    if org_id in s.org_keys:
        return s.org_keys[org_id]
    data = await _sync(cfg)
    profile = _g(data, "profile") or {}
    if not s.private_key and _g(profile, "privateKey"):
        s.private_key = dec_string(_g(profile, "privateKey"), s.user_key)
    for org in _g(profile, "organizations") or []:
        if _g(org, "id") == org_id and _g(org, "key"):
            s.org_keys[org_id] = rsa_decrypt(_g(org, "key"), s.private_key)
            return s.org_keys[org_id]
    raise VaultError("Organisation in Vaultwarden nicht gefunden oder ohne Zugriff.")


async def _key_for(cfg: dict, cipher: Optional[dict] = None) -> bytes:
    org = (_g(cipher, "organizationId") if cipher else None) or (None if cipher else cfg.get("organization_id"))
    if org:
        return await _org_key(cfg, org)
    s = await _ensure(cfg)
    return s.user_key


async def _folder_id(cfg: dict) -> Optional[str]:
    if cfg.get("organization_id"):
        return None
    name = (cfg.get("folder") or "").strip()
    if not name:
        return None
    s = await _ensure(cfg)
    if s.folder_id:
        return s.folder_id
    folders = await _request(cfg, "GET", "/api/folders")
    for f in _g(folders, "data") or []:
        try:
            if dec_text(_g(f, "name"), s.user_key) == name:
                s.folder_id = _g(f, "id")
                return s.folder_id
        except VaultError:
            continue
    created = await _request(cfg, "POST", "/api/folders", json={"name": enc_text(name, s.user_key)})
    s.folder_id = _g(created, "id")
    return s.folder_id


# =============================================================================
# Eintraege
# =============================================================================
def _decrypt_cipher(c: dict, key: bytes) -> dict:
    login = _g(c, "login") or {}
    uris = [dec_text(_g(u, "uri"), key) for u in (_g(login, "uris") or []) if _g(u, "uri")]
    fields = {}
    for f in _g(c, "fields") or []:
        try:
            fields[dec_text(_g(f, "name"), key)] = dec_text(_g(f, "value"), key)
        except VaultError:
            continue
    return {"id": _g(c, "id"), "name": dec_text(_g(c, "name"), key),
            "username": dec_text(_g(login, "username"), key), "password": dec_text(_g(login, "password"), key),
            "totp": dec_text(_g(login, "totp"), key), "uri": uris[0] if uris else "",
            "notes": dec_text(_g(c, "notes"), key), "fields": fields,
            "revision": _g(c, "revisionDate")}


async def _cipher_body(cfg: dict, item: dict, key: bytes) -> dict:
    fields = [{"name": enc_text(k, key), "value": enc_text(str(v), key),
               "type": 1 if any(w in k.lower() for w in ("token", "secret", "key", "geheim", "passwort")) else 0}
              for k, v in (item.get("fields") or {}).items() if v not in (None, "")]
    body = {"type": 1, "name": enc_text(item.get("name") or "RIMPERIO", key),
            "notes": enc_text(item.get("notes") or "", key), "favorite": False, "reprompt": 0,
            "folderId": await _folder_id(cfg), "organizationId": cfg.get("organization_id") or None,
            "login": {"username": enc_text(item.get("username") or "", key),
                      "password": enc_text(item.get("password") or "", key),
                      "totp": enc_text(item.get("totp") or "", key),
                      "uris": [{"uri": enc_text(item["uri"], key), "match": None}] if item.get("uri") else None},
            "fields": fields or None}
    s = await _ensure(cfg)
    if s.user_id:
        # Neuere Vaultwarden-Versionen verlangen, fuer wen verschluesselt wurde.
        body["encryptedFor"] = s.user_id
    return body


async def vault_create(cfg: dict, item: dict) -> str:
    key = await _key_for(cfg)
    body = await _cipher_body(cfg, item, key)
    if cfg.get("organization_id"):
        if not cfg.get("collection_id"):
            raise VaultError("Für einen Organisations-Tresor bitte eine Sammlung angeben.")
        res = await _request(cfg, "POST", "/api/ciphers/create",
                             json={"cipher": body, "collectionIds": [cfg["collection_id"]]})
    else:
        res = await _request(cfg, "POST", "/api/ciphers", json=body)
    cid = _g(res, "id")
    if not cid:
        raise VaultError("Vaultwarden hat den Eintrag nicht angelegt.")
    return cid


async def vault_get(cfg: dict, cipher_id: str, fresh: bool = False) -> dict:
    s = await _ensure(cfg)
    hit = s.cache.get(cipher_id)
    if hit and not fresh and hit[0] > time.time():
        return dict(hit[1])
    c = await _request(cfg, "GET", f"/api/ciphers/{cipher_id}")
    key = await _key_for(cfg, c)
    item = _decrypt_cipher(c, key)
    s.cache[cipher_id] = (time.time() + CACHE_SECONDS, item)
    return dict(item)


async def vault_update(cfg: dict, cipher_id: str, item: dict) -> None:
    c = await _request(cfg, "GET", f"/api/ciphers/{cipher_id}")
    key = await _key_for(cfg, c)
    current = _decrypt_cipher(c, key)
    merged = {**current, **{k: v for k, v in item.items() if v is not None}}
    merged["fields"] = {**current.get("fields", {}), **(item.get("fields") or {})}
    body = await _cipher_body(cfg, merged, key)
    body["organizationId"] = _g(c, "organizationId")
    body["folderId"] = _g(c, "folderId")
    await _request(cfg, "PUT", f"/api/ciphers/{cipher_id}", json=body)
    _SESSION.cache.pop(cipher_id, None)


async def vault_delete(cfg: dict, cipher_id: str) -> None:
    """In den Papierkorb von Vaultwarden - nichts endgueltig loeschen."""
    await _request(cfg, "PUT", f"/api/ciphers/{cipher_id}/delete")
    _SESSION.cache.pop(cipher_id, None)


async def vault_list(cfg: dict) -> List[dict]:
    """Eintraege im RIMPERIO-Ordner bzw. der Sammlung (ohne Geheimnisse)."""
    data = await _sync(cfg)
    s = await _ensure(cfg)
    folder = await _folder_id(cfg)
    out = []
    for c in _g(data, "ciphers") or []:
        if cfg.get("organization_id"):
            if cfg.get("collection_id") not in (_g(c, "collectionIds") or []):
                continue
        elif folder and _g(c, "folderId") != folder:
            continue
        if _g(c, "deletedDate"):
            continue
        try:
            key = s.user_key if not _g(c, "organizationId") else await _org_key(cfg, _g(c, "organizationId"))
            item = _decrypt_cipher(c, key)
        except VaultError:
            continue
        out.append({"id": item["id"], "name": item["name"], "username": item["username"],
                    "uri": item["uri"], "has_totp": bool(item["totp"]), "revision": item["revision"]})
    return out


async def test_connection(cfg: dict, totp: str = "") -> dict:
    """Anmelden, Ordner/Sammlung pruefen. Aktualisiert remember_token/device_id in cfg."""
    async with _lock:
        _SESSION.clear()
        await _login(cfg, totp=totp)
        data = await _sync(cfg)
        profile = _g(data, "profile") or {}
        orgs = [{"id": _g(o, "id"), "name": _g(o, "name")} for o in (_g(profile, "organizations") or [])]
        collections = [{"id": _g(c, "id"), "organization_id": _g(c, "organizationId"),
                        "name": _safe_name(_g(c, "name"), _g(c, "organizationId"))}
                       for c in (_g(data, "collections") or [])]
        if cfg.get("organization_id"):
            for c in collections:
                if c["organization_id"] == cfg["organization_id"]:
                    try:
                        key = await _org_key(cfg, cfg["organization_id"])
                        c["name"] = dec_text(c["name"], key) if c["name"].startswith("2.") else c["name"]
                    except VaultError:
                        pass
        folder = await _folder_id(cfg)
        return {"email": _g(profile, "email"), "name": _g(profile, "name"), "organizations": orgs,
                "collections": collections, "folder_id": folder,
                "ciphers": len(_g(data, "ciphers") or [])}


def _safe_name(name: Any, org: Any) -> str:
    return name if isinstance(name, str) else ""


# =============================================================================
# Einheitliche Schnittstelle fuer den Rest von RIMPERIO
# =============================================================================
async def is_active(db: AsyncSession) -> bool:
    cfg = await load_config(db)
    return bool(cfg.get("enabled") and cfg.get("url"))


async def put(db: AsyncSession, *, secret_id: Optional[int], kind: str, name: str,
              username: str = "", password: Optional[str] = None, totp: Optional[str] = None,
              uri: str = "", notes: str = "", fields: Optional[Dict[str, str]] = None) -> StoredSecret:
    """Anlegen oder aendern. ``None`` heisst "unveraendert", "" heisst "leeren".

    Mit aktivem Vaultwarden landet ein neuer Eintrag dort, sonst lokal.
    """
    row = await db.get(StoredSecret, secret_id) if secret_id else None
    item = {"name": f"RIMPERIO · {name}", "username": username, "password": password, "totp": totp,
            "uri": uri, "notes": notes or f"Von RIMPERIO angelegt ({kind}). Ändern hier wirkt sofort in RIMPERIO.",
            "fields": dict(fields or {})}
    cfg = await load_config(db)
    use_vault = bool(cfg.get("enabled") and cfg.get("url"))
    if row is None:
        row = StoredSecret(kind=kind, name=name)
        db.add(row)
    row.name, row.kind, row.username, row.uri = name, kind, username or "", uri or ""
    if row.backend == "vaultwarden" and row.external_id:
        await vault_update(cfg, row.external_id, item)
        await _save_cfg_tokens(db, cfg)
    elif use_vault and not row.data:
        row.external_id = await vault_create(cfg, {**item, "password": password or "", "totp": totp or ""})
        row.backend = "vaultwarden"
        await _save_cfg_tokens(db, cfg)
    else:
        current = _local(row)
        for k in ("username", "uri", "notes"):
            current[k] = item[k]
        if password is not None:
            current["password"] = password
        if totp is not None:
            current["totp"] = totp
        current["fields"] = {**current.get("fields", {}), **(fields or {})}
        row.data = secretbox.encrypt(json.dumps(current))
        row.backend = "local"
    row.last_error = None
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return row


def _local(row: StoredSecret) -> dict:
    if not row.data:
        return {"username": row.username, "password": "", "totp": "", "uri": row.uri, "notes": "", "fields": {}}
    return json.loads(secretbox.decrypt(row.data))


async def get(db: AsyncSession, secret_id: Optional[int]) -> dict:
    """Inhalt eines Zugangs: username, password, totp, uri, notes, fields."""
    if not secret_id:
        raise VaultError("Kein Zugang hinterlegt.")
    row = await db.get(StoredSecret, secret_id)
    if row is None:
        raise VaultError("Zugang nicht mehr vorhanden.")
    if row.backend == "vaultwarden":
        cfg = await load_config(db)
        if not cfg.get("url"):
            raise VaultError("Der Zugang liegt in Vaultwarden, aber Vaultwarden ist nicht mehr eingerichtet.")
        try:
            item = await vault_get(cfg, row.external_id)
        except VaultError as exc:
            row.last_error = str(exc)
            await db.commit()
            raise
        await _save_cfg_tokens(db, cfg)
        return item
    return {"name": row.name, **_local(row)}


async def set_field(db: AsyncSession, secret_id: int, name: str, value: str) -> None:
    """Ein Zusatzfeld setzen (z. B. das Geraete-Token einer Synology)."""
    row = await db.get(StoredSecret, secret_id)
    if row is None:
        return
    if row.backend == "vaultwarden":
        cfg = await load_config(db)
        await vault_update(cfg, row.external_id, {"fields": {name: value}})
        return
    current = _local(row)
    current.setdefault("fields", {})[name] = value
    row.data = secretbox.encrypt(json.dumps(current))
    await db.commit()


async def remove(db: AsyncSession, secret_id: Optional[int]) -> None:
    if not secret_id:
        return
    row = await db.get(StoredSecret, secret_id)
    if row is None:
        return
    if row.backend == "vaultwarden" and row.external_id:
        try:
            await vault_delete(await load_config(db), row.external_id)
        except VaultError as exc:
            logger.warning("Vaultwarden-Eintrag %s nicht gelöscht: %s", row.external_id, exc)
    await db.delete(row)
    await db.commit()


async def migrate_local(db: AsyncSession) -> dict:
    """Alle lokalen Zugaenge nach Vaultwarden schieben."""
    cfg = await load_config(db)
    if not (cfg.get("enabled") and cfg.get("url")):
        raise VaultError("Vaultwarden ist nicht eingerichtet.")
    moved, failed = 0, []
    rows = (await db.execute(select(StoredSecret).where(StoredSecret.backend == "local"))).scalars().all()
    for row in rows:
        try:
            data = _local(row)
            row.external_id = await vault_create(cfg, {
                "name": f"RIMPERIO · {row.name}", "username": data.get("username", ""),
                "password": data.get("password", ""), "totp": data.get("totp", ""), "uri": data.get("uri", ""),
                "notes": data.get("notes", ""), "fields": data.get("fields", {})})
            row.backend, row.data, row.last_error = "vaultwarden", "", None
            moved += 1
        except (VaultError, secretbox.SecretBoxError, ValueError) as exc:
            row.last_error = str(exc)
            failed.append({"id": row.id, "name": row.name, "error": str(exc)})
    await db.commit()
    await _save_cfg_tokens(db, cfg)
    return {"moved": moved, "failed": failed}


async def _save_cfg_tokens(db: AsyncSession, cfg: dict) -> None:
    """device_id / remember_token nach einer Anmeldung behalten (ohne Cache zu leeren)."""
    row = (await db.execute(select(Setting).where(Setting.key == CONFIG_KEY))).scalar_one_or_none()
    if not row or not isinstance(row.value, dict):
        return
    val = dict(row.value)
    changed = False
    if cfg.get("device_id") and val.get("device_id") != cfg["device_id"]:
        val["device_id"] = cfg["device_id"]
        changed = True
    enc = secretbox.decrypt(val.get("remember_token") or "") if val.get("remember_token") else ""
    if (cfg.get("remember_token") or "") != enc:
        val["remember_token"] = secretbox.encrypt(cfg.get("remember_token") or "")
        changed = True
    if changed:
        row.value = val
        await db.commit()


def public_secret(row: Optional[StoredSecret]) -> Optional[dict]:
    if row is None:
        return None
    return {"id": row.id, "kind": row.kind, "name": row.name, "backend": row.backend,
            "username": row.username, "uri": row.uri, "external_id": row.external_id,
            "last_error": row.last_error,
            "updated_at": row.updated_at.isoformat(timespec="seconds") + "Z" if row.updated_at else None}


def totp_now(seed: str) -> str:
    """Aktuellen Code aus einem TOTP-Geheimnis (Base32 oder otpauth://-URI)."""
    import pyotp
    seed = (seed or "").strip()
    if seed.lower().startswith("otpauth://"):
        return pyotp.parse_uri(seed).now()
    return pyotp.TOTP(seed.replace(" ", "").upper()).now()
