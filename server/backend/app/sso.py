# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Anmeldung ueber Microsoft 365 / Entra ID (OpenID Connect)
# ==============================================================================
"""
Anmelden mit dem Firmenkonto - ohne Zusatzkosten.

**Warum OpenID Connect und nicht SAML.** Beides erledigt dieselbe Aufgabe. Der
Unterschied liegt in der Rechnung: SAML-Anmeldung fuer eine eigene, nicht im
Katalog gelistete Anwendung verlangt bei Microsoft einen kostenpflichtigen
Entra-ID-Plan. Eine App-Registrierung mit OpenID Connect (also OAuth 2.0 mit
Identitaetsschicht obendrauf) ist in *jedem* Microsoft-365-Tarif enthalten,
auch im kostenlosen. Beide Wege gelten als gleichwertig sicher; OIDC ist der
neuere und der, den Microsoft selbst empfiehlt.

Praktisch heisst das: das hier funktioniert mit dem Tarif, den man ohnehin hat.

**Wie es ablaeuft** (Authorization Code Flow mit PKCE, der empfohlene Weg fuer
Anwendungen mit eigenem Server):

1. Der Browser holt sich bei ``/api/auth/sso/start`` eine Adresse bei Microsoft.
   RIMPERIO legt dafuer einen Merkzettel an: ``state`` (gegen untergeschobene
   Anmeldungen), ``nonce`` (gegen wiederverwendete Token) und den
   PKCE-Pruefwert.
2. Der Benutzer meldet sich bei Microsoft an - Passwort, MFA, alles bleibt
   dort. RIMPERIO sieht das Passwort nie.
3. Microsoft schickt den Browser mit einem Code zurueck auf
   ``/api/auth/sso/callback``.
4. RIMPERIO tauscht den Code gegen ein ID-Token, **prueft dessen Unterschrift**
   gegen die oeffentlichen Schluessel des Mandanten und kontrolliert
   Aussteller, Empfaenger, Laufzeit und ``nonce``.
5. Erst dann entsteht ein RIMPERIO-Konto (oder es wird wiedergefunden) und der
   Browser bekommt eine ganz normale RIMPERIO-Sitzung.

**Was nicht passiert:** RIMPERIO fragt nie ein Passwort ab, speichert keines und
kann keines sehen. Das Client-Geheimnis der App-Registrierung wird nie wieder
herausgegeben - weder in der Konfiguration noch in einer Fehlermeldung.

**Wer wird Administrator?** Nicht automatisch jeder. Entweder ueber die
Gruppenzugehoerigkeit (``admin_groups`` - die Objekt-IDs der Gruppen aus
Entra ID) oder ueber eine Rolle aus der App-Registrierung (``roles``-Anspruch).
Steht beides leer, bekommt jeder die Rolle aus ``default_role`` - ab Werk
``user``. Das ist Absicht: ein Verzeichnis, in dem jeder mitmachen darf, soll
nicht versehentlich lauter Administratoren erzeugen.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting, User

# Zugelassene Verfahren fuer ID-Tokens: nur asymmetrisch (Anbieter signiert,
# wir pruefen mit dem oeffentlichen Schluessel).
ALLOWED_ID_TOKEN_ALGS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")

logger = logging.getLogger("rimperio.sso")

SETTING_KEY = "sso_config"

# Wie lange ein begonnener Anmeldevorgang gueltig bleibt. Kurz: laenger braucht
# niemand, und jede Minute mehr ist eine Minute, in der ein abgefangener
# Merkzettel noch etwas wert waere.
FLOW_TTL_SECONDS = 600

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "provider": "entra",              # 'entra' (Microsoft 365) oder 'generic'
    "label": "Mit Microsoft 365 anmelden",
    "tenant_id": "",                  # bei Entra: die Verzeichnis-ID
    "client_id": "",
    "client_secret": "",              # wird nie ausgeliefert
    "discovery_url": "",              # bei 'generic': das .well-known-Dokument
    "scopes": "openid profile email",
    "redirect_uri": "",               # leer = aus SITE_URL bzw. der Anfrage
    "auto_create_users": True,
    "default_role": "user",
    "admin_groups": "",               # Objekt-IDs, Komma-getrennt
    "admin_role_claim": "RIMPERIO.Admin",
    "allowed_domains": "",            # z.B. "firma.de,firma.com"; leer = alle
    "username_claim": "preferred_username",
}

PROVIDERS = {
    "entra": {
        "label": "Microsoft 365 / Entra ID",
        "hint": ("Der übliche Fall. Es braucht die Verzeichnis-ID (Mandant), die "
                 "Anwendungs-ID und ein Client-Geheimnis aus der App-Registrierung."),
        "needs_tenant": True,
    },
    "generic": {
        "label": "Anderer OpenID-Connect-Anbieter",
        "hint": ("Keycloak, Authentik, Google Workspace, Okta … Statt der "
                 "Verzeichnis-ID wird die Adresse des Discovery-Dokuments "
                 "(.well-known/openid-configuration) gebraucht."),
        "needs_tenant": False,
    },
}


# =============================================================================
# Konfiguration
# =============================================================================
async def load_config(db: AsyncSession) -> dict:
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    config = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        config.update({k: v for k, v in row.value.items() if k in DEFAULT_CONFIG})
    if config["provider"] not in PROVIDERS:
        config["provider"] = "entra"
    return config


async def save_config(db: AsyncSession, incoming: dict) -> dict:
    """Speichert die Einstellung. Ein leeres Geheimnis laesst das alte stehen."""
    config = await load_config(db)

    for key in ("provider", "label", "tenant_id", "client_id", "discovery_url",
                "scopes", "redirect_uri", "default_role", "admin_groups",
                "admin_role_claim", "allowed_domains", "username_claim"):
        if key in incoming and incoming[key] is not None:
            config[key] = str(incoming[key]).strip()
    for key in ("enabled", "auto_create_users"):
        if key in incoming and incoming[key] is not None:
            config[key] = bool(incoming[key])

    secret = (incoming.get("client_secret") or "").strip()
    if secret:
        config["client_secret"] = secret
    if incoming.get("clear_client_secret"):
        config["client_secret"] = ""

    if config["provider"] not in PROVIDERS:
        raise ValueError(f"Unbekannter Anbieter '{config['provider']}'.")
    if config["default_role"] not in ("user", "admin"):
        raise ValueError("Die Standardrolle muss 'user' oder 'admin' sein.")

    if config["enabled"]:
        if not config["client_id"]:
            raise ValueError("Ohne Anwendungs-ID (Client-ID) geht es nicht.")
        if not config["client_secret"]:
            raise ValueError("Ohne Client-Geheimnis geht es nicht.")
        if config["provider"] == "entra" and not config["tenant_id"]:
            raise ValueError("Für Microsoft 365 fehlt die Verzeichnis-ID (Mandant).")
        if config["provider"] == "generic" and not config["discovery_url"]:
            raise ValueError("Für einen anderen Anbieter fehlt die Adresse des "
                             "Discovery-Dokuments.")

    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = config
    else:
        db.add(Setting(key=SETTING_KEY, value=config,
                       description="Anmeldung über einen externen Identitätsanbieter"))
    await db.commit()
    logger.warning("Single Sign-on gespeichert: %s, aktiv=%s",
                   config["provider"], config["enabled"])
    return public_config(config)


def public_config(config: dict) -> dict:
    """Ohne Geheimnis - so geht es an die Oberflaeche."""
    safe = {k: v for k, v in config.items() if k != "client_secret"}
    safe["client_secret_set"] = bool(config.get("client_secret"))
    safe["discovery_effective"] = discovery_url(config)
    return safe


def discovery_url(config: dict) -> str:
    if config.get("provider") == "entra":
        tenant = (config.get("tenant_id") or "").strip()
        if not tenant:
            return ""
        return f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    return (config.get("discovery_url") or "").strip()


# =============================================================================
# Anbieter-Dokument und Schluessel
# =============================================================================
_metadata_cache: Dict[str, Tuple[float, dict]] = {}
_jwks_cache: Dict[str, Tuple[float, dict]] = {}
_CACHE_TTL = 3600.0


async def metadata(config: dict, force: bool = False) -> dict:
    """Das Discovery-Dokument des Anbieters (gecacht)."""
    url = discovery_url(config)
    if not url:
        raise ValueError("Es ist kein Anbieter eingerichtet.")

    cached = _metadata_cache.get(url)
    if cached and not force and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        if response.status_code >= 400:
            raise RuntimeError(f"Der Anbieter antwortet auf {url} mit HTTP "
                               f"{response.status_code}.")
        data = response.json()

    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not data.get(required):
            raise RuntimeError(f"Im Discovery-Dokument fehlt '{required}'.")

    _metadata_cache[url] = (time.time(), data)
    return data


async def jwks(config: dict, force: bool = False) -> dict:
    """Die oeffentlichen Schluessel des Anbieters (gecacht)."""
    meta = await metadata(config)
    url = meta["jwks_uri"]
    cached = _jwks_cache.get(url)
    if cached and not force and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        keys = response.json()

    _jwks_cache[url] = (time.time(), keys)
    return keys


# =============================================================================
# Merkzettel fuer laufende Anmeldungen
# =============================================================================
class _Flows:
    """Was zu einer begonnenen Anmeldung gehoert - im Arbeitsspeicher.

    Bewusst nicht in der Datenbank: es lebt zehn Minuten und ueberlebt einen
    Neustart nicht - dann faengt man die Anmeldung eben neu an. Dafuer liegt
    nichts davon irgendwo dauerhaft herum.
    """

    def __init__(self) -> None:
        self._items: Dict[str, dict] = {}

    def start(self, verifier: str, nonce: str, next_url: str = "") -> str:
        self._expire()
        state = secrets.token_urlsafe(32)
        self._items[state] = {
            "verifier": verifier, "nonce": nonce,
            "next": next_url, "at": time.time(),
        }
        return state

    def take(self, state: str) -> Optional[dict]:
        """Holt den Merkzettel - genau einmal."""
        self._expire()
        return self._items.pop(state or "", None)

    def _expire(self) -> None:
        deadline = time.time() - FLOW_TTL_SECONDS
        for key in [k for k, v in self._items.items() if v["at"] < deadline]:
            self._items.pop(key, None)
        # Notbremse gegen Fluten: wer tausend Anmeldungen beginnt und keine
        # beendet, soll den Speicher nicht vollschreiben.
        if len(self._items) > 500:
            for key in sorted(self._items, key=lambda k: self._items[k]["at"])[:250]:
                self._items.pop(key, None)


flows = _Flows()


def _pkce() -> Tuple[str, str]:
    """(Pruefwert, Herausforderung) nach RFC 7636, Verfahren S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def effective_redirect(config: dict, request_base: str) -> str:
    """Wohin Microsoft den Browser zurueckschickt."""
    explicit = (config.get("redirect_uri") or "").strip()
    if explicit:
        return explicit
    base = (request_base or "").rstrip("/")
    return f"{base}/api/auth/sso/callback" if base else ""


# =============================================================================
# Schritt 1: Adresse beim Anbieter besorgen
# =============================================================================
async def begin(config: dict, request_base: str, next_url: str = "") -> dict:
    if not config.get("enabled"):
        raise ValueError("Single Sign-on ist nicht eingeschaltet.")

    meta = await metadata(config)
    redirect = effective_redirect(config, request_base)
    if not redirect:
        raise ValueError("Die Rückadresse ließ sich nicht bestimmen. SITE_URL setzen "
                         "oder die Rückadresse in den Einstellungen eintragen.")

    verifier, challenge = _pkce()
    nonce = secrets.token_urlsafe(24)
    state = flows.start(verifier, nonce, next_url)

    params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": redirect,
        "response_mode": "query",
        "scope": config.get("scopes") or "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{meta['authorization_endpoint']}?{httpx.QueryParams(params)}"
    return {"url": url, "state": state, "redirect_uri": redirect}


# =============================================================================
# Schritt 2: Code einloesen und ID-Token pruefen
# =============================================================================
async def exchange(config: dict, code: str, state: str, request_base: str) -> dict:
    """Tauscht den Code gegen ein geprueftes ID-Token. Gibt die Ansprueche zurueck."""
    flow = flows.take(state)
    if not flow:
        raise ValueError("Der Anmeldevorgang ist abgelaufen oder gehört nicht hierher. "
                         "Bitte noch einmal von vorn anfangen.")

    meta = await metadata(config)
    redirect = effective_redirect(config, request_base)

    data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "code_verifier": flow["verifier"],
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(meta["token_endpoint"], data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            detail = body.get("error_description") or body.get("error") or ""
        except ValueError:
            detail = response.text[:300]
        raise RuntimeError(f"Der Anbieter hat den Code nicht angenommen: "
                           f"{detail or f'HTTP {response.status_code}'}")

    payload = response.json()
    id_token = payload.get("id_token")
    if not id_token:
        raise RuntimeError("Der Anbieter hat kein ID-Token geschickt. Fehlt der Bereich "
                           "'openid' in den Scopes?")

    claims = await verify_id_token(config, id_token, flow["nonce"])
    claims["_next"] = flow.get("next") or ""
    return claims


async def verify_id_token(config: dict, id_token: str, nonce: str) -> dict:
    """Prueft Unterschrift, Aussteller, Empfaenger, Laufzeit und nonce.

    Ohne diese Pruefung waere das ID-Token ein Zettel, auf den sich jeder
    schreiben koennte, er sei der Administrator.
    """
    meta = await metadata(config)
    keys = await jwks(config)

    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise RuntimeError(f"Das ID-Token ist unlesbar: {exc}") from exc

    kid = header.get("kid")
    key = next((k for k in keys.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        # Schluesselwechsel beim Anbieter: einmal frisch holen, dann aufgeben.
        keys = await jwks(config, force=True)
        key = next((k for k in keys.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise RuntimeError("Der Unterschrift-Schlüssel des Anbieters ist unbekannt.")

    # Den Algorithmus bestimmt NICHT das Token. Frueher wurde der Wert aus dem
    # (unsignierten) Kopf uebernommen - das ist die klassische Einladung zur
    # Algorithmus-Verwechslung ("none", HS256 mit dem oeffentlichen Schluessel).
    # Erlaubt sind nur asymmetrische Verfahren, und wenn der Schluessel selbst
    # ein Verfahren nennt, nur dieses.
    alg = header.get("alg", "")
    allowed = [key["alg"]] if key.get("alg") in ALLOWED_ID_TOKEN_ALGS else list(ALLOWED_ID_TOKEN_ALGS)
    if alg not in allowed:
        raise RuntimeError(f"Das ID-Token nutzt ein nicht zugelassenes Verfahren ({alg or 'keins'}).")

    issuer = meta["issuer"]
    try:
        claims = jwt.decode(
            id_token, key,
            algorithms=allowed,
            audience=config["client_id"],
            issuer=issuer,
            options={"verify_at_hash": False},
        )
    except JWTError as exc:
        # Entra schreibt bei mandantenuebergreifenden Anwendungen ein '{tenantid}'
        # in den Aussteller - dann passt der Vergleich mit dem Platzhalter nicht.
        if "{tenantid}" in issuer:
            try:
                claims = jwt.decode(
                    id_token, key, algorithms=allowed,
                    audience=config["client_id"],
                    options={"verify_iss": False, "verify_at_hash": False},
                )
            except JWTError as inner:
                raise RuntimeError(f"Das ID-Token wurde abgelehnt: {inner}") from inner
            # Der Aussteller wird trotzdem geprueft - nur eben mit dem Mandanten
            # aus dem Token eingesetzt. Ohne diese Pruefung haette jedes Token
            # irgendeines Entra-Mandanten gepasst.
            tid = str(claims.get("tid") or "")
            if not tid or claims.get("iss") != issuer.replace("{tenantid}", tid):
                raise RuntimeError("Das ID-Token stammt von einem fremden Aussteller.")
        else:
            raise RuntimeError(f"Das ID-Token wurde abgelehnt: {exc}") from exc

    # Die nonce MUSS passen. Vorher wurde die Pruefung uebersprungen, wenn das
    # Token gar keine nonce trug - genau der Fall, den sie abfangen soll.
    if not nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise RuntimeError("Das ID-Token gehört nicht zu dieser Anmeldung (nonce).")

    return claims


# =============================================================================
# Schritt 3: daraus ein RIMPERIO-Konto machen
# =============================================================================
def _claim_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def pick_username(config: dict, claims: dict) -> str:
    """Welcher Anspruch wird zum Benutzernamen?"""
    order = [config.get("username_claim") or "preferred_username",
             "preferred_username", "upn", "email", "unique_name", "sub"]
    for key in order:
        value = (claims.get(key) or "").strip() if isinstance(claims.get(key), str) else ""
        if value:
            return value.lower()
    raise RuntimeError("Das ID-Token enthält keinen brauchbaren Benutzernamen.")


def decide_role(config: dict, claims: dict) -> str:
    """Administrator oder normaler Benutzer?"""
    wanted_groups = {g.lower() for g in _claim_list(config.get("admin_groups"))}
    if wanted_groups:
        groups = {g.lower() for g in _claim_list(claims.get("groups"))}
        if groups & wanted_groups:
            return "admin"

    admin_role = (config.get("admin_role_claim") or "").strip().lower()
    if admin_role:
        roles = {r.lower() for r in _claim_list(claims.get("roles"))}
        if admin_role in roles:
            return "admin"

    if wanted_groups or admin_role:
        # Es gibt eine Regel, und sie hat nicht getroffen: dann eben Benutzer.
        return "user" if config.get("default_role") != "admin" else "admin"

    return config.get("default_role") or "user"


def domain_allowed(config: dict, username: str, claims: dict) -> Tuple[bool, str]:
    allowed = [d.lower().lstrip("@") for d in _claim_list(config.get("allowed_domains"))]
    if not allowed:
        return True, ""
    candidates = [username] + [str(claims.get(k) or "") for k in ("email", "upn",
                                                                  "preferred_username")]
    for candidate in candidates:
        if "@" in candidate and candidate.rsplit("@", 1)[-1].lower() in allowed:
            return True, ""
    return False, (f"Dieses Konto gehört nicht zu einer zugelassenen Domäne "
                   f"({', '.join(allowed)}).")


async def upsert_user(db: AsyncSession, config: dict, claims: dict) -> User:
    """Findet oder legt das Konto zu diesen Anspruechen an."""
    from .auth import get_password_hash

    username = pick_username(config, claims)
    ok, reason = domain_allowed(config, username, claims)
    if not ok:
        raise PermissionError(reason)

    role = decide_role(config, claims)
    email = None
    for key in ("email", "upn", "preferred_username"):
        value = claims.get(key)
        if isinstance(value, str) and "@" in value:
            email = value
            break

    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()

    if user:
        source = (user.auth_source or "local")
        if source not in ("sso", "oidc"):
            # Gleicher Name, anderes Zuhause. Ein Verzeichniskonto darf ein
            # lokales Admin-Konto nicht uebernehmen - dieselbe Regel wie bei LDAP.
            raise PermissionError(
                f"Es gibt bereits ein Konto '{username}', das nicht über Single Sign-on "
                f"angelegt wurde. Das muss ein Administrator zuerst auflösen.")
        if not user.is_active:
            raise PermissionError("Dieses Konto ist deaktiviert.")
        user.role = role
        if email:
            user.email = email
        user.updated_at = datetime.utcnow()
    else:
        if not config.get("auto_create_users", True):
            raise PermissionError(
                f"Für '{username}' gibt es hier kein Konto, und das automatische Anlegen "
                f"ist ausgeschaltet.")
        user = User(
            username=username,
            email=email,
            # Unbrauchbarer Hash: an diesem Konto gibt es kein lokales Passwort.
            password_hash=get_password_hash(secrets.token_urlsafe(32)),
            role=role,
            is_active=True,
            auth_source="sso",
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)
    return user


# =============================================================================
# Einrichtungshilfe
# =============================================================================
def setup_guide(config: dict, request_base: str) -> dict:
    """Was im Entra-Portal einzutragen ist - damit man nicht suchen muss."""
    redirect = effective_redirect(config, request_base) or "https://<RIMPERIO-Adresse>/api/auth/sso/callback"
    return {
        "redirect_uri": redirect,
        "steps": [
            "Im Entra-Portal: Identität → Anwendungen → App-Registrierungen → Neue Registrierung.",
            "Name frei wählen, Kontotyp „Nur Konten in diesem Organisationsverzeichnis“.",
            f"Umleitungs-URI vom Typ „Web“ eintragen: {redirect}",
            "Nach dem Anlegen: Anwendungs-ID (Client) und Verzeichnis-ID (Mandant) hier eintragen.",
            "Unter „Zertifikate & Geheimnisse“ ein neues Client-Geheimnis anlegen und hier eintragen. "
            "Der Wert ist nur einmal sichtbar.",
            "Optional, für Administratorrechte: unter „Tokenkonfiguration“ den Gruppenanspruch "
            "aktivieren und die Objekt-ID der Admin-Gruppe hier eintragen — oder unter "
            "„App-Rollen“ eine Rolle „RIMPERIO.Admin“ anlegen und zuweisen.",
        ],
        "note": ("Das reicht mit jedem Microsoft-365-Tarif, auch dem kostenlosen Entra-ID-Plan. "
                 "Der SAML-Weg wäre gleichwertig, verlangt für eigene Anwendungen aber einen "
                 "kostenpflichtigen Plan — deshalb nimmt RIMPERIO OpenID Connect."),
    }
