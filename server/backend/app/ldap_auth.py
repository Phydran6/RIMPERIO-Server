# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.16.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Anmeldung gegen LDAP / Active Directory (optional)
# ==============================================================================
"""
Optionale Anmeldung gegen ein Verzeichnis (Active Directory oder OpenLDAP).

Ablauf einer Anmeldung:
  1. Mit dem Dienstkonto am Verzeichnis anmelden (oder anonym, falls erlaubt).
  2. Den Benutzer über den Suchfilter finden -> liefert dessen DN.
  3. Mit **dessen** DN und dem eingegebenen Passwort erneut anmelden. Erst das
     beweist, dass das Passwort stimmt.
  4. Gruppen auslesen und auf eine RIMPERIO-Rolle abbilden.

Wichtig: Ein leeres Passwort wird immer abgelehnt. LDAP-Server behandeln einen
Bind ohne Passwort als "anonyme Anmeldung" und melden Erfolg — ohne diese
Prüfung käme man mit leerem Passwort in jedes Konto.

Die Einstellungen liegen in der `settings`-Tabelle unter dem Schlüssel `ldap`
und werden über die Oberfläche gepflegt.
"""

import asyncio
import logging
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

logger = logging.getLogger("rimperio.ldap")

SETTING_KEY = "ldap"

DEFAULT_CONFIG = {
    "enabled": False,
    # ldap://host:389 (mit StartTLS) oder ldaps://host:636
    "server_uri": "",
    "start_tls": False,
    "verify_cert": True,
    # Dienstkonto für die Suche. Leer = anonyme Suche (viele AD-Server verbieten das).
    "bind_dn": "",
    "bind_password": "",
    "base_dn": "",
    # {username} wird durch die Eingabe ersetzt. AD: sAMAccountName, OpenLDAP: uid
    "user_filter": "(sAMAccountName={username})",
    "attr_email": "mail",
    "attr_display_name": "displayName",
    "attr_groups": "memberOf",
    # Nur Mitglieder dieser Gruppe dürfen sich anmelden (leer = alle gefundenen).
    "required_group": "",
    # Mitglieder dieser Gruppe bekommen die Rolle "admin".
    "admin_group": "",
    "default_role": "user",
    # Unbekannte Benutzer beim ersten erfolgreichen Login anlegen.
    "auto_create_users": True,
}

# Felder, die nie an die Oberfläche zurückgehen.
SECRET_FIELDS = ("bind_password",)


async def load_config(db: AsyncSession) -> dict:
    """Liest die LDAP-Einstellungen (mit Standardwerten aufgefüllt)."""
    result = await db.execute(select(Setting).where(Setting.key == SETTING_KEY))
    row = result.scalar_one_or_none()
    config = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        config.update(row.value)
    return config


def public_config(config: dict) -> dict:
    """Konfiguration ohne Geheimnisse; statt des Passworts nur, ob eines gesetzt ist."""
    safe = {k: v for k, v in config.items() if k not in SECRET_FIELDS}
    safe["bind_password_set"] = bool(config.get("bind_password"))
    return safe


def _escape_filter_value(value: str) -> str:
    """Entschärft Sonderzeichen im Suchfilter (RFC 4515).

    Ohne das könnte eine Eingabe wie `*` oder `admin)(|(uid=*` den Filter
    umschreiben und einen fremden Treffer erzwingen.
    """
    replacements = {
        "\\": r"\5c",
        "*": r"\2a",
        "(": r"\28",
        ")": r"\29",
        "\0": r"\00",
        "/": r"\2f",
    }
    return "".join(replacements.get(char, char) for char in value)


def _connect(config: dict, user: str, password: str):
    """Baut eine Verbindung auf und meldet sich an. Wirft bei Misserfolg."""
    # Import erst hier: ohne aktiviertes LDAP soll das Paket nicht nötig sein.
    from ldap3 import Server, Connection, Tls, ALL
    import ssl

    uri = (config.get("server_uri") or "").strip()
    if not uri:
        raise ValueError("Kein LDAP-Server konfiguriert")

    tls = Tls(
        validate=ssl.CERT_REQUIRED if config.get("verify_cert", True) else ssl.CERT_NONE
    )
    server = Server(uri, use_ssl=uri.lower().startswith("ldaps://"), get_info=ALL, tls=tls)

    conn = Connection(
        server,
        user=user or None,
        password=password or None,
        auto_bind=False,
        raise_exceptions=False,
        receive_timeout=10,
    )
    if config.get("start_tls") and not uri.lower().startswith("ldaps://"):
        if not conn.start_tls():
            raise ValueError(f"StartTLS fehlgeschlagen: {conn.result}")
    if not conn.bind():
        raise ValueError(f"Anmeldung am Verzeichnis fehlgeschlagen: {conn.result.get('description', conn.result)}")
    return conn


def _authenticate_sync(config: dict, username: str, password: str) -> dict:
    """Blockierender Teil der Anmeldung (läuft im Thread-Pool)."""
    from ldap3 import SUBTREE

    base_dn = (config.get("base_dn") or "").strip()
    if not base_dn:
        raise ValueError("Kein Basis-DN konfiguriert")

    search_filter = (config.get("user_filter") or "").replace(
        "{username}", _escape_filter_value(username)
    )

    attributes = [
        a for a in (
            config.get("attr_email"),
            config.get("attr_display_name"),
            config.get("attr_groups"),
        ) if a
    ]

    # Schritt 1+2: mit Dienstkonto suchen
    search_conn = _connect(config, config.get("bind_dn", ""), config.get("bind_password", ""))
    try:
        search_conn.search(
            search_base=base_dn,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=attributes or ["cn"],
            size_limit=2,
        )
        entries = search_conn.entries
        if not entries:
            return {"ok": False, "reason": "Benutzer im Verzeichnis nicht gefunden"}
        if len(entries) > 1:
            return {"ok": False, "reason": "Suchfilter trifft mehrere Benutzer"}

        entry = entries[0]
        user_dn = str(entry.entry_dn)

        def attr(name: str) -> Optional[str]:
            if not name or name not in entry:
                return None
            value = entry[name].value
            if isinstance(value, list):
                return value[0] if value else None
            return value

        groups_attr = config.get("attr_groups")
        groups = []
        if groups_attr and groups_attr in entry:
            raw = entry[groups_attr].value
            groups = raw if isinstance(raw, list) else ([raw] if raw else [])

        email = attr(config.get("attr_email"))
        display_name = attr(config.get("attr_display_name"))
    finally:
        search_conn.unbind()

    # Schritt 3: Passwort prüfen (Bind als der Benutzer selbst)
    try:
        user_conn = _connect(config, user_dn, password)
        user_conn.unbind()
    except ValueError:
        return {"ok": False, "reason": "Passwort falsch"}

    return {
        "ok": True,
        "dn": user_dn,
        "email": email,
        "display_name": display_name,
        "groups": [str(g) for g in groups],
    }


def _group_matches(needle: str, groups: list) -> bool:
    """Gruppenvergleich: ganzer DN oder nur der Name (CN) zählt, Groß/klein egal."""
    needle = (needle or "").strip().lower()
    if not needle:
        return False
    for group in groups:
        value = str(group).strip().lower()
        if value == needle:
            return True
        # "CN=RIMPERIO-Admins,OU=…" -> "rimperio-admins"
        if value.startswith("cn=") and value.split(",")[0][3:] == needle:
            return True
    return False


def role_for(config: dict, groups: list) -> str:
    """RIMPERIO-Rolle aus den Verzeichnisgruppen."""
    if _group_matches(config.get("admin_group", ""), groups):
        return "admin"
    role = (config.get("default_role") or "user").strip().lower()
    return role if role in ("admin", "user") else "user"


async def authenticate(config: dict, username: str, password: str) -> Tuple[bool, dict]:
    """Prüft Benutzername + Passwort gegen das Verzeichnis.

    Rückgabe: (erfolgreich, Details). Details enthalten bei Erfolg dn, email,
    display_name, groups und die abgeleitete Rolle, sonst einen Grund.
    """
    if not config.get("enabled"):
        return False, {"reason": "LDAP ist nicht aktiviert"}
    if not password:
        # Siehe Modulkommentar: leeres Passwort = anonymer Bind = Scheunentor.
        return False, {"reason": "Leeres Passwort"}

    try:
        result = await asyncio.to_thread(_authenticate_sync, config, username, password)
    except ImportError:
        logger.error("LDAP aktiviert, aber das Paket ldap3 fehlt im Image")
        return False, {"reason": "LDAP-Unterstützung fehlt im Backend (ldap3 nicht installiert)"}
    except Exception as exc:
        logger.warning("LDAP-Anmeldung fehlgeschlagen: %s", exc)
        return False, {"reason": str(exc)}

    if not result.get("ok"):
        return False, result

    groups = result.get("groups", [])
    required = config.get("required_group", "")
    if required and not _group_matches(required, groups):
        return False, {"reason": "Benutzer ist nicht Mitglied der erforderlichen Gruppe"}

    result["role"] = role_for(config, groups)
    return True, result
