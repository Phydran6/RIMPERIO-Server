# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Patchmanagement: Richtlinien, Freigaben, KI-Pruefung, Laeufe
# ==============================================================================
"""
Patchmanagement fuer Windows, Linux und Docker - vollautomatisch, wo es geht,
mit Freigabe, wo es noetig ist.

**Zwei Fragen je Update** (Regeln der Richtlinie, siehe DEFAULT_RULES):

*Stufe* - wie viel Mensch braucht es?

- ``normal``    laeuft immer von selbst. Erkennt die KI ein hohes Risiko oder
                ist dasselbe Update anderswo schiefgegangen, wird es zu
                ``important`` hochgestuft (abschaltbar).
- ``important`` laeuft von selbst - braucht nur dann eine Freigabe, wenn es
                Fehler ausloesen koennte (KI-Risiko, bekannte Probleme,
                Hauptversion, auf einem anderen Rechner fehlgeschlagen).
- ``critical``  nie ohne Freigabe: Datenbanken, Webserver/Reverse-Proxys,
                Anmeldung/Zugang (SSH, LDAP, SSO, VPN), Hypervisor,
                Funktionsupdates, Versionsspruenge. Fuer Datenbank-Container
                laufen Sicherung und Probelauf schon vorher (dbmigrate) - die
                Freigabe kommt mit Ergebnis.

*Dringlichkeit* - wie schnell muss es drauf?

- ``immediate`` sofort, nicht erst im Wartungsfenster (aktiv ausgenutzte
                Luecke, CISA KEV, oder per Regel/KI).
- ``soon``      im naechsten Wartungsfenster (Sicherheitsupdates, wichtige).
- ``low``       irgendwann - erst nach einigen Tagen Rueckstellung, dann im
                Fenster (gewoehnliche Pakete, Container-Neubauten).

**Der Ablauf:**

1. *Jeden Tag* (Standard 12:00) sucht jeder Rechner nach Updates - Windows
   Update bzw. Paketverwaltung, und, falls Docker laeuft, nach neueren Images.
2. Jedes Update bekommt Stufe und Dringlichkeit (erste passende Regel).
3. Die *KI* liest mit, was die Quellen (advisories.py) und auf Wunsch die
   Websuche zu jedem Update sagen; ihr Urteil zaehlt fuer ``important`` und
   ``normal`` (siehe oben) und kann die Dringlichkeit anheben.
4. Was eine Freigabe braucht, erscheint in der App und an der Glocke - samt
   Einschaetzung. Ein Klick gibt frei oder lehnt ab.
5. Installiert wird im Wartungsfenster (Standard taeglich 02:00-05:00),
   ``immediate`` sofort: Snapshot ueber Proxmox → Gesundheitsbild vorher →
   Installation → Neustart, falls noetig → Wartezeit → Gesundheitsbild
   nachher und Checks.
6. Passt alles, wird der Snapshot nach 3 Tagen geloescht. Passt etwas nicht,
   bleibt er, es kommt eine Meldung, und ein Klick rollt zurueck (oder, wenn
   eingestellt, passiert das von selbst). Dasselbe Update wartet dann auf den
   anderen Rechnern auf eine Freigabe.

Alles davon ist je Richtlinie einstellbar; Richtlinien gelten global, je
Standort oder je Rechner (die genaueste gewinnt).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timedelta, time as dtime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import advisories, agentwake, ai, journal, notify, pve
from .database import Base, async_session, engine
from .models import (PATCH_TABLES, Agent, CheckResult, Command, PatchItem, PatchKnowledge,
                     PatchPolicy, PatchReport, PatchRun, PveHost)

logger = logging.getLogger("rimperio.patch")

LOOP_SECONDS = 20
COMMAND_GRACE_MINUTES = 20

# Zustaende eines Updates
OPEN_STATES = ("review", "auto", "needs_approval", "approved")
INSTALLABLE = ("auto", "approved")
FINAL_STATES = ("installed", "superseded")

ACTIVE_RUN = ("pending", "snapshotting", "baseline", "installing", "rebooting",
              "settling", "verifying", "rolling_back")

STATUS_LABEL = {
    "review": "wird geprüft", "auto": "automatisch", "needs_approval": "Freigabe nötig",
    "approved": "freigegeben", "rejected": "abgelehnt", "ignored": "ignoriert",
    "monitor": "nur beobachten", "installing": "wird installiert", "installed": "installiert",
    "failed": "fehlgeschlagen", "superseded": "erledigt",
}

# Stufe: wie viel Mensch braucht ein Update?
TIERS = ("normal", "important", "critical")
TIER_LABEL = {"normal": "Normal", "important": "Wichtig", "critical": "Kritisch"}
TIER_HINT = {
    "normal": "läuft immer von selbst",
    "important": "läuft von selbst – Freigabe nur, wenn es Fehler auslösen könnte",
    "critical": "nie ohne Freigabe",
}
# Dringlichkeit: wie schnell muss es drauf?
URGENCIES = ("immediate", "soon", "low")
URGENCY_LABEL = {"immediate": "sofort", "soon": "zeitnah", "low": "irgendwann"}
URGENCY_HINT = {
    "immediate": "sofort nach Erscheinen, auch außerhalb des Wartungsfensters",
    "soon": "im nächsten Wartungsfenster",
    "low": "nach der Rückstellung im Wartungsfenster",
}
_URGENCY_RANK = {"immediate": 0, "soon": 1, "low": 2}
_TIER_RANK = {"critical": 0, "important": 1, "normal": 2}

# Was bei „Wichtig“ eine Freigabe ausloest
SIGNALS = {
    "ai_risk": "KI hält es für riskant",
    "known_issues": "KI kennt Probleme",
    "major": "Sprung der Hauptversion",
    "failed_elsewhere": "auf einem anderen Rechner fehlgeschlagen",
}
# Bei „Normal“ eskalieren nur diese (Hauptversion und Probleme stehen schon in den Regeln).
NORMAL_ESCALATE = ("ai_risk", "failed_elsewhere")

# Alte Aktionen (bis 2026.11.25) -> Stufe
_LEGACY_ACTION = {"auto": "normal", "approve": "critical", "ignore": "ignore"}
RULE_ACTIONS = TIERS + ("ignore",)

# Felder in PatchItem.data, die RIMPERIO selbst fuehrt (ueberleben den naechsten Scan)
_KEEP_DATA = ("_planned", "tier_rule", "rule_reason", "escalated", "db_migration_id",
              "db_prepared", "urgent_attempt")

# -----------------------------------------------------------------------------
# Voreinstellungen
# -----------------------------------------------------------------------------
_RX_DB_PKG = r"^(postgresql|mariadb-server|mysql-server|mongodb|redis-server|valkey|percona-server)"
_RX_DB_IMAGE = (r"(^|/)(postgres|postgresql|postgis|timescaledb|timescale|mariadb|mysql|mysql-server|"
                r"percona-server|mongo|mongodb|redis|redis-stack-server|valkey|keydb|influxdb|elasticsearch|"
                r"opensearch|couchdb|clickhouse-server|cassandra|neo4j)([:@]|$)")
_RX_WEB_PKG = (r"^(nginx|apache2|httpd|caddy|haproxy|traefik|lighttpd|openresty|envoy|varnish|squid)(-|$)"
               r"|^libapache2-mod-|^mod_(ssl|security)")
_RX_WEB_IMAGE = (r"(^|/)(nginx|nginx-unprivileged|httpd|caddy|traefik|haproxy|nginx-proxy-manager|nginx-proxy|"
                 r"swag|envoy|openresty|lighttpd|zoraxy|cloudflared|frpc|frps|acme-companion)([:@]|$)")
_RX_LOGIN_PKG = (r"^(openssh-server|openssh-sftp-server|sssd|slapd|openldap-servers|krb5-kdc|krb5-server|"
                 r"freeipa-server|ipa-server|samba|winbind|libpam-modules|libpam-runtime|pam|openvpn|"
                 r"wireguard|strongswan|keycloak|xrdp)(-|$)")
_RX_LOGIN_IMAGE = (r"goauthentik/|netbirdio/|bitwarden/|(^|/)(keycloak|authelia|oauth2-proxy|vaultwarden|lldap|"
                   r"openldap|freeipa-server|zitadel|wg-easy|wireguard|openvpn|openvpn-as|headscale|tailscale|"
                   r"pomerium|dex|kanidm|casdoor|teleport|guacamole|guacd)([:@/]|$)")

DEFAULT_RULES: List[dict] = [
    # --- Normal --------------------------------------------------------------
    {"name": "Definitionsupdates (Defender)", "platform": "windows", "field": "category",
     "op": "equals", "value": "Definition Updates", "tier": "normal", "urgency": "soon"},
    # --- Kritisch: nie ohne Freigabe ------------------------------------------
    {"name": "Funktionsupdates / Upgrades", "platform": "windows", "field": "category",
     "op": "in", "value": "Upgrades,Feature Packs", "tier": "critical", "urgency": ""},
    {"name": "Funktionsupdate im Titel", "platform": "windows", "field": "title",
     "op": "regex", "value": r"(?i)feature update|funktionsupdate", "tier": "critical", "urgency": ""},
    {"name": "Datenbankserver", "platform": "linux", "field": "package", "op": "regex",
     "value": _RX_DB_PKG, "tier": "critical", "urgency": ""},
    {"name": "Datenbank-Container", "platform": "docker", "field": "image", "op": "regex",
     "value": _RX_DB_IMAGE, "tier": "critical", "urgency": ""},
    {"name": "Webserver / Reverse-Proxy", "platform": "linux", "field": "package", "op": "regex",
     "value": _RX_WEB_PKG, "tier": "critical", "urgency": ""},
    {"name": "Webserver / Reverse-Proxy (Container)", "platform": "docker", "field": "image", "op": "regex",
     "value": _RX_WEB_IMAGE, "tier": "critical", "urgency": ""},
    {"name": "Anmeldung / Zugang (SSH, LDAP, PAM, VPN)", "platform": "linux", "field": "package", "op": "regex",
     "value": _RX_LOGIN_PKG, "tier": "critical", "urgency": ""},
    {"name": "Anmeldung / Zugang (SSO, VPN, Container)", "platform": "docker", "field": "image", "op": "regex",
     "value": "(?i)" + _RX_LOGIN_IMAGE, "tier": "critical", "urgency": ""},
    {"name": "Proxmox-Pakete", "platform": "linux", "field": "package", "op": "regex",
     "value": r"^(pve-|proxmox-|qemu-server|libpve-)", "tier": "critical", "urgency": ""},
    {"name": "Versionssprung (Hauptversion)", "platform": "any", "field": "major",
     "op": "is_true", "value": "", "tier": "critical", "urgency": ""},
    # --- Wichtig: Freigabe nur, wenn es Fehler ausloesen koennte --------------
    {"name": "Sicherheits- und kritische Updates", "platform": "windows", "field": "category",
     "op": "in", "value": "Security Updates,Critical Updates", "tier": "important", "urgency": "soon"},
    {"name": "Treiber", "platform": "windows", "field": "kind", "op": "equals",
     "value": "driver", "tier": "important", "urgency": "low"},
    {"name": "Vorschau-Updates", "platform": "windows", "field": "title", "op": "regex",
     "value": r"(?i)\bpreview\b|vorschau", "tier": "important", "urgency": "low"},
    {"name": "Docker-Engine / containerd", "platform": "linux", "field": "package", "op": "regex",
     "value": r"^(docker-ce|docker-ce-cli|docker-ce-rootless-extras|containerd(\.io)?|docker\.io|moby-engine|runc)$",
     "tier": "important", "urgency": ""},
    {"name": "Kernel", "platform": "linux", "field": "package", "op": "regex",
     "value": r"^(linux-image|linux-modules|linux-headers|linux-generic|linux-virtual|linux-firmware|kernel)(-|$)",
     "tier": "important", "urgency": ""},
    {"name": "Systemkern (systemd, libc, OpenSSL, Bootloader, sudo)", "platform": "linux", "field": "package",
     "op": "regex", "value": r"^(systemd|libsystemd|udev|libc6|libc-bin|glibc|openssl|libssl|grub|shim|dbus|sudo)",
     "tier": "important", "urgency": ""},
    {"name": "Sicherheitsupdates", "platform": "any", "field": "security", "op": "is_true",
     "value": "", "tier": "important", "urgency": "soon"},
]

# Die mitgelieferten Regeln bis 2026.11.25 - wer sie nie angefasst hat, bekommt
# beim Start die neuen (ensure_schema), statt sie als „eigene“ zu behalten.
LEGACY_DEFAULT_RULES: List[Tuple[str, str]] = [
    ("Definitionsupdates (Defender) sofort", "auto"), ("Funktionsupdates / Upgrades", "approve"),
    ("Funktionsupdate im Titel", "approve"), ("Treiber", "approve"), ("Vorschau-Updates", "approve"),
    ("Sicherheits- und kritische Updates", "auto"), ("Docker-Engine / containerd", "approve"),
    ("Datenbankserver", "approve"), ("Proxmox-Pakete", "approve"), ("Datenbank-Container", "approve"),
    ("Versionssprung (Hauptversion)", "approve"),
]

DEFAULT_POLICY: Dict[str, Any] = {
    "platforms": ["windows", "linux", "docker"],
    "timezone": "Europe/Berlin",
    "scan": {"enabled": True, "time": "12:00"},
    "window": {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6], "start": "02:00",
               "duration_minutes": 180},
    "reboot": "if_needed",            # never | if_needed | always
    "rules": DEFAULT_RULES,
    "fallback": {"windows": "normal", "linux": "normal", "docker": "normal"},
    "tiers": {
        # Normal: bei erkanntem Risiko (KI, anderswo fehlgeschlagen) wie „Wichtig“ behandeln
        "normal": {"escalate": True},
        # Wichtig: was eine Freigabe ausloest (Schluessel aus SIGNALS)
        "important": {"approve_on": ["ai_risk", "known_issues", "major", "failed_elsewhere"]},
        # Kritisch: Datenbank-Container vorher sichern und probeweise aktualisieren
        "critical": {"prepare_db": True},
    },
    "urgency": {
        "immediate": {"outside_window": True, "reboot": "if_needed"},
        "soon": {"defer_days": 0},
        "low": {"defer_days": 3},
    },
    "ai": {"mode": "gate", "web_search": True, "required": False},   # off | advise | gate
    "snapshot": {"enabled": True, "mode": "snapshot", "include_ram": False,
                 "retention_days": 3, "required": False, "storage": ""},
    "verify": {"enabled": True, "settle_minutes": 5, "reboot_timeout_minutes": 45,
               "require_checks": True, "ignore_services": []},
    "rollback": {"auto": False},
    "docker": {"enabled": True, "cleanup_images": True, "health_timeout": 120,
               "rollback_on_fail": True, "prefer_compose": True,
               "include": [], "exclude": []},
    "notify": {"approval_needed": True, "run_failed": True, "run_succeeded": False,
               "urgent_run": True},
}

IGNORED_SERVICES = {
    # Windows: kommen und gehen, gerade rund um Updates
    "trustedinstaller", "wuauserv", "bits", "msiserver", "usosvc", "dosvc", "waasmedicsvc",
    "sppsvc", "gupdate", "gupdatem", "edgeupdate", "edgeupdatem", "mapsbroker", "remoteregistry",
    "clipsvc", "tiledatamodelsvc", "cdpsvc", "wsearch", "sysmain", "staterepository",
    "installservice", "appxsvc", "wbiosrvc", "tokenbroker", "gpsvc", "shellhwdetection",
    "sense", "mpssvc_temp", "googleupdaterservice", "googleupdaterinternalservice",
    # Linux
    "apt-daily.service", "apt-daily-upgrade.service", "unattended-upgrades.service",
    "packagekit.service", "fwupd.service", "man-db.service", "needrestart.service",
    "dnf-makecache.service", "systemd-tmpfiles-clean.service",
}


def _merge(base: Any, incoming: Any) -> Any:
    if isinstance(base, dict) and isinstance(incoming, dict):
        out = deepcopy(base)
        for k, v in incoming.items():
            if k in out:
                out[k] = _merge(out[k], v)
            else:
                out[k] = deepcopy(v)
        return out
    return deepcopy(incoming) if incoming is not None else deepcopy(base)


def normalize_rule(rule: dict) -> dict:
    """Regel in die heutige Form: `tier` statt `action` (auto→normal, approve→critical)."""
    r = dict(rule or {})
    tier = r.get("tier") or _LEGACY_ACTION.get(str(r.get("action") or ""), r.get("action"))
    r["tier"] = tier or "critical"
    r.pop("action", None)
    r["urgency"] = r.get("urgency") or ""
    r["enabled"] = r.get("enabled", True) is not False
    return r


def _normalize(merged: dict, raw: Optional[dict]) -> dict:
    raw = raw or {}
    merged["rules"] = [normalize_rule(r) for r in (merged.get("rules") or [])]
    merged["fallback"] = {k: _LEGACY_ACTION.get(v, v) for k, v in (merged.get("fallback") or {}).items()}
    # Frueher eine Rueckstellung fuer alles: gilt jetzt fuer „zeitnah“ (und
    # mindestens fuer „irgendwann“), solange dort nichts Eigenes steht.
    legacy = merged.pop("defer_days", None)
    if legacy:
        urg_raw = raw.get("urgency") or {}
        if "defer_days" not in (urg_raw.get("soon") or {}):
            merged["urgency"]["soon"]["defer_days"] = int(legacy)
        if "defer_days" not in (urg_raw.get("low") or {}):
            merged["urgency"]["low"]["defer_days"] = max(int(legacy), int(merged["urgency"]["low"]["defer_days"]))
    return merged


def effective_settings(policy: Optional[PatchPolicy]) -> dict:
    raw = (policy.settings if policy else None) or {}
    settings = _merge(DEFAULT_POLICY, raw)
    # Regeln ersetzen statt mischen (eine Liste mischt man nicht sinnvoll).
    if isinstance(raw.get("rules"), list):
        settings["rules"] = deepcopy(raw["rules"])
    return _normalize(settings, raw)


def validate_settings(s: dict) -> dict:
    """Prueft eine Richtlinie und gibt sie bereinigt zurueck. Wirft ValueError."""
    merged = _merge(DEFAULT_POLICY, s or {})
    if isinstance((s or {}).get("rules"), list):
        merged["rules"] = s["rules"]
    merged = _normalize(merged, s)
    for key in ("time",):
        _hhmm(merged["scan"][key])
    _hhmm(merged["window"]["start"])
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(merged["timezone"])
    except Exception as exc:
        raise ValueError(f"Unbekannte Zeitzone '{merged['timezone']}'.") from exc
    days = [int(d) for d in merged["window"]["days"]]
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("Wochentage: 0 (Montag) bis 6 (Sonntag).")
    merged["window"]["days"] = sorted(set(days))
    merged["window"]["duration_minutes"] = max(15, min(int(merged["window"]["duration_minutes"]), 24 * 60))
    if merged["reboot"] not in ("never", "if_needed", "always"):
        raise ValueError("Neustart: never, if_needed oder always.")
    if merged["ai"]["mode"] not in ("off", "advise", "gate"):
        raise ValueError("KI-Modus: off, advise oder gate.")
    if merged["snapshot"]["mode"] not in ("snapshot", "backup", "auto"):
        raise ValueError("Sicherung: snapshot, backup oder auto.")
    merged["snapshot"]["retention_days"] = max(0, min(int(merged["snapshot"]["retention_days"]), 90))
    for rule in merged["rules"]:
        if rule.get("tier") not in RULE_ACTIONS:
            raise ValueError(f"Regel '{rule.get('name')}': Stufe normal, important, critical oder ignore.")
        if rule.get("urgency") not in ("",) + URGENCIES:
            raise ValueError(f"Regel '{rule.get('name')}': Dringlichkeit immediate, soon, low oder leer.")
        if rule.get("op") == "regex":
            try:
                re.compile(rule.get("value") or "")
            except re.error as exc:
                raise ValueError(f"Regel '{rule.get('name')}': ungültiger regulärer Ausdruck ({exc}).") from exc
    for plat, tier in merged["fallback"].items():
        if tier not in RULE_ACTIONS:
            raise ValueError(f"Standard für {plat}: normal, important, critical oder ignore.")
    approve_on = [str(x) for x in (merged["tiers"]["important"].get("approve_on") or [])]
    unknown = [x for x in approve_on if x not in SIGNALS]
    if unknown:
        raise ValueError(f"Wichtig: unbekannte Auslöser {', '.join(unknown)} (erlaubt: {', '.join(SIGNALS)}).")
    merged["tiers"]["important"]["approve_on"] = approve_on
    merged["tiers"]["normal"]["escalate"] = bool(merged["tiers"]["normal"].get("escalate", True))
    merged["tiers"]["critical"]["prepare_db"] = bool(merged["tiers"]["critical"].get("prepare_db", True))
    imm = merged["urgency"]["immediate"]
    imm["outside_window"] = bool(imm.get("outside_window", True))
    if imm.get("reboot") not in ("never", "if_needed", "always"):
        raise ValueError("Sofort-Updates, Neustart: never, if_needed oder always.")
    for key in ("soon", "low"):
        merged["urgency"][key]["defer_days"] = max(0, min(int(merged["urgency"][key].get("defer_days") or 0), 90))
    return merged


def is_legacy_default_rules(rules: Any) -> bool:
    """Sind das unveraenderte mitgelieferte Regeln von frueher?"""
    if not isinstance(rules, list) or len(rules) != len(LEGACY_DEFAULT_RULES):
        return False
    for r, (name, action) in zip(rules, LEGACY_DEFAULT_RULES):
        if not isinstance(r, dict) or r.get("name") != name or r.get("action") != action \
                or r.get("enabled", True) is False:
            return False
    return True


def _hhmm(value: str) -> dtime:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Uhrzeit '{value}' bitte als HH:MM.")
    return dtime(int(m.group(1)), int(m.group(2)))


# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------
async def ensure_schema() -> None:
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=PATCH_TABLES))
            for col in ("tier VARCHAR(16) DEFAULT ''", "urgency VARCHAR(16) DEFAULT ''"):
                await conn.exec_driver_sql(f"ALTER TABLE patch_items ADD COLUMN IF NOT EXISTS {col}")
        async with async_session() as db:
            have = (await db.execute(select(PatchPolicy).where(PatchPolicy.builtin.is_(True)))).scalars().first()
            if have is None:
                db.add(PatchPolicy(name="Standard", builtin=True, scope_type="global", priority=1000,
                                   description="Gilt für alle Rechner ohne eigene Richtlinie.",
                                   settings={}))
                await db.commit()
            await advisories.ensure_builtin(db)
            await _migrate_tiers(db)
        logger.info("Patch-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("Patch-Schema nicht angelegt: %s", exc)


async def _migrate_tiers(db: AsyncSession) -> None:
    """Einmalig beim Wechsel auf Stufen/Dringlichkeit: unveraenderte alte
    Standardregeln durch die neuen ersetzen, offene Updates neu einstufen."""
    changed = []
    for p in await policies(db):
        raw = dict(p.settings or {})
        if is_legacy_default_rules(raw.get("rules")):
            raw.pop("rules", None)
            if raw.get("fallback") == {"windows": "auto", "linux": "auto", "docker": "auto"}:
                raw.pop("fallback", None)
            p.settings = raw
            changed.append(p.name)
    if changed:
        await db.commit()
        logger.info("Patch-Richtlinien auf Stufen umgestellt: %s", ", ".join(changed))
    untiered = (await db.execute(select(PatchItem.id).where(
        PatchItem.status.in_(OPEN_STATES + ("ignored", "monitor")),
        or_(PatchItem.tier.is_(None), PatchItem.tier == "")).limit(1))).first()
    if changed or untiered:
        n = await reclassify(db)
        logger.info("Patch-Einstufung nach Stufen: %s offene Updates neu bewertet", n)


# -----------------------------------------------------------------------------
# Richtlinie finden
# -----------------------------------------------------------------------------
_SCOPE_RANK = {"agent": 0, "site": 1, "global": 2}


def pick_policy(agent: Agent, policies: List[PatchPolicy]) -> Optional[PatchPolicy]:
    best: Optional[Tuple[tuple, PatchPolicy]] = None
    for p in policies:
        if not p.enabled:
            continue
        # Mandanten: eine Richtlinie eines Mandanten gilt nur fuer dessen
        # Geraete; ohne Mandant gilt sie fuer alle (die des Betreibers).
        if p.tenant_id is not None and p.tenant_id != agent.tenant_id:
            continue
        ok = (p.scope_type == "global"
              or (p.scope_type == "site" and p.scope_value == (agent.site or "Standard"))
              or (p.scope_type == "agent" and p.scope_value == agent.agent_uid))
        if not ok:
            continue
        # Bei gleichem Geltungsbereich schlaegt die eigene Richtlinie des
        # Mandanten die gemeinsame des Betreibers.
        rank = (_SCOPE_RANK.get(p.scope_type, 9), 0 if p.tenant_id is not None else 1, p.priority or 100)
        if best is None or rank < best[0]:
            best = (rank, p)
    return best[1] if best else None


async def policies(db: AsyncSession) -> List[PatchPolicy]:
    return list((await db.execute(select(PatchPolicy).order_by(PatchPolicy.priority))).scalars())


async def policy_for(db: AsyncSession, agent: Agent) -> Tuple[Optional[PatchPolicy], dict]:
    p = pick_policy(agent, await policies(db))
    return p, effective_settings(p)


# -----------------------------------------------------------------------------
# Zeit
# -----------------------------------------------------------------------------
def _tz(settings: dict):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(settings.get("timezone") or "Europe/Berlin")
    except Exception:
        return ZoneInfo("UTC")


def local_now(settings: dict, now: Optional[datetime] = None) -> datetime:
    """Jetzt in der Zeitzone der Richtlinie (ohne tzinfo, fuer einfache Vergleiche)."""
    from zoneinfo import ZoneInfo
    now = now or datetime.utcnow()
    return now.replace(tzinfo=ZoneInfo("UTC")).astimezone(_tz(settings)).replace(tzinfo=None)


def to_utc(settings: dict, local: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    return local.replace(tzinfo=_tz(settings)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def scan_due(settings: dict, last_queued: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if not settings["scan"].get("enabled", True):
        return False
    now = now or datetime.utcnow()
    if last_queued is None:
        return True
    loc = local_now(settings, now)
    at = _hhmm(settings["scan"]["time"])
    today = datetime.combine(loc.date(), at)
    slot = today if loc >= today else today - timedelta(days=1)
    return last_queued < to_utc(settings, slot)


def window_state(settings: dict, now: Optional[datetime] = None) -> Tuple[bool, Optional[datetime], Optional[datetime]]:
    """(offen?, Beginn UTC, Ende UTC) des aktuellen Fensters; sonst (False, naechster Beginn, None)."""
    w = settings["window"]
    if not w.get("enabled", True):
        return False, None, None
    now = now or datetime.utcnow()
    loc = local_now(settings, now)
    start = _hhmm(w["start"])
    dur = timedelta(minutes=int(w["duration_minutes"]))
    days = set(int(d) for d in w["days"])
    for back in (1, 0):
        day = loc.date() - timedelta(days=back)
        begin = datetime.combine(day, start)
        if day.weekday() in days and begin <= loc < begin + dur:
            return True, to_utc(settings, begin), to_utc(settings, begin + dur)
    for ahead in range(0, 8):
        day = loc.date() + timedelta(days=ahead)
        begin = datetime.combine(day, start)
        if day.weekday() in days and begin > loc:
            return False, to_utc(settings, begin), None
    return False, None, None


# -----------------------------------------------------------------------------
# Einstufen
# -----------------------------------------------------------------------------
# Eine Version: Zahl am Anfang, dahinter ein Trenner oder Ende - so faellt ein
# abgeschnittener Image-Abdruck wie "3fa9c0..." nicht als Version durch.
_MAJOR = re.compile(r"^(?:\d+:)?v?(\d+)(?=[.\-_+~]|$)")


def major_change(a: str, b: str) -> bool:
    ma, mb = _MAJOR.match((a or "").strip()), _MAJOR.match((b or "").strip())
    if not (ma and mb) or ma.group(1) == mb.group(1):
        return False
    # Datumsversionen (tzdata 2024a → 2025a, 20240101 → 20250101) sind kein Versionssprung.
    if int(ma.group(1)) >= 1990 and int(mb.group(1)) >= 1990:
        return False
    return True


def item_facts(it: PatchItem) -> dict:
    data = it.data or {}
    return {
        "kind": data.get("kind") or ("container" if it.kind == "docker" else "software"),
        "platform": it.platform, "key": it.key, "title": it.title, "category": it.category,
        "severity": it.severity, "kb": it.kb, "security": bool(it.security),
        "package": data.get("package") or (it.key if it.platform == "linux" else ""),
        "image": data.get("image", ""), "container": it.key if it.kind == "docker" else "",
        "major": major_change(it.version_from, it.version_to),
        "reboot": bool(it.reboot),
    }


def rule_matches(rule: dict, facts: dict) -> bool:
    if not rule.get("enabled", True):
        return False
    plat = rule.get("platform") or "any"
    if plat != "any" and plat != facts["platform"]:
        return False
    field = rule.get("field") or "title"
    value = facts.get(field)
    op = rule.get("op") or "contains"
    want = str(rule.get("value") or "")
    if op == "is_true":
        return bool(value)
    if op == "is_false":
        return not value
    text = str(value if value is not None else "")
    if op == "equals":
        return text.lower() == want.lower()
    if op == "contains":
        return want.lower() in text.lower()
    if op == "in":
        return text.lower() in [w.strip().lower() for w in want.split(",") if w.strip()]
    if op == "glob":
        return fnmatch.fnmatch(text.lower(), want.lower())
    if op == "regex":
        try:
            return re.search(want, text) is not None
        except re.error:
            return False
    return False


def classify(it: PatchItem, settings: dict) -> Tuple[str, str, str, str]:
    """(Stufe, Dringlichkeit der Regel, Regelname, Begruendung).
    Stufe: normal | important | critical | ignore; Dringlichkeit leer = Standard."""
    facts = item_facts(it)
    if it.kind == "docker":
        d = settings.get("docker") or {}
        if not d.get("enabled", True):
            return "ignore", "", "Docker aus", "Docker-Updates sind in der Richtlinie abgeschaltet."
        for pat in d.get("exclude") or []:
            if fnmatch.fnmatch(it.key.lower(), str(pat).lower()):
                return "ignore", "", "Ausnahme", f"Container passt auf die Ausnahme '{pat}'."
        inc = d.get("include") or []
        if inc and not any(fnmatch.fnmatch(it.key.lower(), str(p).lower()) for p in inc):
            return "ignore", "", "nicht eingeschlossen", "Container steht nicht in der Einschlussliste."
    for raw in settings.get("rules") or []:
        rule = normalize_rule(raw)
        if rule_matches(rule, facts):
            return rule["tier"], rule["urgency"], rule.get("name", "Regel"), f"Regel „{rule.get('name', '')}“"
    tier = _LEGACY_ACTION.get((settings.get("fallback") or {}).get(it.platform, "normal"), "normal")
    return tier, "", "Standard", f"Standard für {it.platform}"


def base_urgency(it: PatchItem, tier: str, rule_urgency: str) -> Tuple[str, str]:
    """(Dringlichkeit, Begruendung) - aktiv ausgenutzte Luecken gehen immer vor."""
    if it.urgent:
        return "immediate", "behebt eine aktiv ausgenutzte Lücke (CISA KEV)"
    if rule_urgency in URGENCIES:
        return rule_urgency, ""
    if it.security or tier in ("important", "critical"):
        return "soon", ""
    return "low", ""


def _set_not_before(it: PatchItem, settings: dict, now: datetime) -> None:
    if it.urgency == "immediate":
        it.not_before = None
        return
    days = int(((settings.get("urgency") or {}).get(it.urgency) or {}).get("defer_days") or 0)
    it.not_before = (it.first_seen or now) + timedelta(days=days) if days else None


def risk_signals(it: PatchItem, settings: dict, failed_elsewhere: bool = False) -> Dict[str, str]:
    """Was dafuer spricht, dass ein Update Fehler ausloesen koennte - Schluessel aus SIGNALS -> Klartext."""
    out: Dict[str, str] = {}
    mode = (settings.get("ai") or {}).get("mode", "off")
    a = it.ai or {}
    if mode == "gate" and not a.get("error"):
        if a.get("risk") == "high" or a.get("recommendation") in ("hold", "approve"):
            out["ai_risk"] = f"KI: {a.get('summary') or 'Risiko erhöht'}"
        if a.get("known_issues"):
            out["known_issues"] = "KI kennt Probleme: " + "; ".join(str(k) for k in a["known_issues"][:2])
    if major_change(it.version_from, it.version_to):
        out["major"] = f"Hauptversion {it.version_from} → {it.version_to}"
    if failed_elsewhere:
        out["failed_elsewhere"] = "dasselbe Update ist auf einem anderen Rechner fehlgeschlagen"
    return out


def decide_status(it: PatchItem, settings: dict, now: datetime,
                  failed: Optional[Dict[str, Set[int]]] = None) -> None:
    """Stufe, Dringlichkeit und Status neu setzen - fuer alles, was kein Mensch entschieden hat."""
    tier, rule_urg, rule, reason = classify(it, settings)
    it.rule = rule
    data = {k: v for k, v in (it.data or {}).items() if k not in ("_planned", "escalated")}
    if tier == "ignore":
        it.status, it.reason, it.tier, it.urgency = "ignored", reason, "", ""
        it.data = data
        return
    urgency, why = base_urgency(it, tier, rule_urg)
    it.tier, it.urgency = tier, urgency
    data["tier_rule"] = tier
    data["rule_reason"] = reason + (f", {why}" if why else "")
    it.data = data
    _set_not_before(it, settings, now)
    if data.get("monitor_only"):
        it.status, it.reason = "monitor", "Container ist nur zur Beobachtung markiert (monitor-only)."
        return
    mode = (settings.get("ai") or {}).get("mode", "off")
    if mode != "off" and not it.ai:
        it.status, it.reason = "review", data["rule_reason"]
        return
    apply_assessment(it, settings, failed)


def apply_assessment(it: PatchItem, settings: dict, failed: Optional[Dict[str, Set[int]]] = None) -> None:
    """Endgueltiger Status aus Stufe und Risikosignalen (nach der KI-Einschaetzung)."""
    data = dict(it.data or {})
    planned = data.pop("_planned", None)
    data.pop("escalated", None)
    # Stufe der Regel; Updates von vor der Umstellung tragen nur den alten Plan.
    tier = data.get("tier_rule") or (it.tier if it.tier in TIERS else None) \
        or ("normal" if planned == "auto" else "critical")
    urgency = it.urgency if it.urgency in URGENCIES else base_urgency(it, tier, "")[0]
    data["tier_rule"] = tier
    parts = [data.get("rule_reason") or it.reason or "Regel"]
    a = it.ai or {}
    mode = (settings.get("ai") or {}).get("mode", "off")
    # Die KI darf die Dringlichkeit anheben, nie senken.
    ai_urg = str(a.get("urgency") or "")
    if mode != "off" and not a.get("error") and ai_urg in URGENCIES \
            and _URGENCY_RANK[ai_urg] < _URGENCY_RANK[urgency]:
        urgency = ai_urg
        parts.append(f"KI: {URGENCY_LABEL[ai_urg]} ({a.get('urgency_reason') or a.get('summary') or 'dringlicher'})"[:300])
    if urgency != it.urgency:
        it.urgency = urgency
        _set_not_before(it, settings, datetime.utcnow())
    elsewhere = bool(failed and (failed.get(_fingerprint(it), set()) - {it.agent_id}))
    signals = risk_signals(it, settings, elsewhere)
    no_ai = bool(a.get("error")) and mode != "off" and bool((settings.get("ai") or {}).get("required"))
    tiers = settings.get("tiers") or {}
    status = "auto"
    if tier == "critical":
        status = "needs_approval"
        parts.append("Kritisch: nie ohne Freigabe")
    elif tier == "important":
        wanted = (tiers.get("important") or {}).get("approve_on") or []
        hits = [signals[k] for k in wanted if k in signals]
        if no_ai:
            hits.append("KI-Prüfung nicht möglich")
        if hits:
            status = "needs_approval"
            parts.append("Wichtig, aber " + "; ".join(hits))
        else:
            parts.append("Wichtig: läuft automatisch")
    else:
        hits = [signals[k] for k in NORMAL_ESCALATE if k in signals] \
            if (tiers.get("normal") or {}).get("escalate", True) else []
        if no_ai:
            hits.append("KI-Prüfung nicht möglich")
        if hits:
            status, tier = "needs_approval", "important"
            data["escalated"] = True
            parts.append("hochgestuft auf Wichtig: " + "; ".join(hits))
    it.tier, it.status, it.data = tier, status, data
    it.reason = " · ".join(p for p in parts if p)[:1000]


async def failed_fingerprints(db: AsyncSession, days: int = 30) -> Dict[str, Set[int]]:
    """Abdruck -> Rechner, auf denen dieses Update zuletzt fehlschlug oder zurueckgerollt wurde."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(select(PatchItem).where(
        or_(PatchItem.status == "failed", PatchItem.decided_by == "Zurückgerollt"),
        PatchItem.last_seen >= since))).scalars().all()
    out: Dict[str, Set[int]] = {}
    for it in rows:
        out.setdefault(_fingerprint(it), set()).add(it.agent_id)
    return out


def sort_key(it: PatchItem) -> Tuple[int, int, int, int]:
    """Dringendes und Kritisches zuerst."""
    return (_URGENCY_RANK.get(it.urgency or "", 3), _TIER_RANK.get(it.tier or "", 3),
            0 if it.urgent else 1, 0 if it.security else 1)


# -----------------------------------------------------------------------------
# Scan-Ergebnisse uebernehmen
# -----------------------------------------------------------------------------
def _fingerprint(it: PatchItem) -> str:
    if it.platform == "windows":
        base = it.kb or it.title
    elif it.platform == "docker":
        base = f"{(it.data or {}).get('image', '')}|{(it.data or {}).get('remote_digest', '')}"
    else:
        base = f"{(it.data or {}).get('package') or it.key}|{it.version_to}"
    return f"{it.platform}|{base}"[:255]


async def _upsert(db: AsyncSession, agent: Agent, kind: str, key: str, fields: dict,
                  existing: Dict[Tuple[str, str], PatchItem], now: datetime) -> Tuple[PatchItem, bool]:
    it = existing.get((kind, key))
    fresh = it is None
    if fresh:
        it = PatchItem(agent_id=agent.id, kind=kind, key=key, first_seen=now, status="new")
        db.add(it)
        existing[(kind, key)] = it
    elif it.status in ("installed", "superseded", "failed"):
        # Wieder da (neue Version, oder Installation hat nicht gegriffen): neu bewerten.
        fresh = it.version_to != fields.get("version_to") or it.status != "failed"
        if fresh:
            it.status, it.ai, it.decided_by, it.decided_at = "new", None, "", None
            it.first_seen = now
    elif it.version_to and fields.get("version_to") and it.version_to != fields["version_to"] \
            and it.status not in ("installing",):
        # Neue Version eines bekannten Updates: menschliche Entscheidung gilt nicht weiter.
        it.status, it.ai, it.decided_by, it.decided_at = "new", None, "", None
        fresh = True
    # Was RIMPERIO selbst am Update fuehrt (Stufe der Regel, DB-Update, Sofort-
    # Versuch), ersetzt der naechste Scan nicht - bei einer neuen Bewertung
    # bleibt nur der Sofort-Versuch (sonst liefe ein Update, das nach dem Lauf
    # wieder auftaucht, gleich noch einmal sofort).
    if "data" in fields and it.data:
        keep = ("urgent_attempt",) if fresh else _KEEP_DATA
        fields = {**fields, "data": {**(fields["data"] or {}),
                                     **{k: v for k, v in it.data.items() if k in keep}}}
    for k, v in fields.items():
        setattr(it, k, v)
    it.last_seen = now
    return it, fresh


async def ingest_os_scan(db: AsyncSession, agent: Agent, payload: Any) -> dict:
    now = datetime.utcnow()
    if isinstance(payload, list):
        payload = {"updates": payload}
    payload = payload if isinstance(payload, dict) else {}
    updates = [u for u in (payload.get("updates") or []) if isinstance(u, dict) and u.get("id")]
    platform = "windows" if (agent.platform or "").lower().startswith("win") or \
        payload.get("manager") == "windows_update" else "linux"
    _, settings = await policy_for(db, agent)
    existing = {(i.kind, i.key): i for i in (await db.execute(
        select(PatchItem).where(PatchItem.agent_id == agent.id, PatchItem.kind == "os"))).scalars()}
    seen, fresh_items = set(), []
    for u in updates:
        key = str(u["id"])[:255]
        seen.add(key)
        kb = str(u.get("kb") or "")[:128]
        fields = {
            "title": str(u.get("title") or key)[:512], "platform": platform,
            "category": str(u.get("category") or "")[:64], "severity": str(u.get("severity") or "")[:32],
            "kb": kb, "version_from": str(u.get("current") or "")[:128],
            "version_to": str(u.get("available") or kb or "")[:128],
            "security": bool(u.get("security")), "reboot": bool(u.get("reboot")),
            "data": {**u, "manager": payload.get("manager", "")},
        }
        it, fresh = await _upsert(db, agent, "os", key, fields, existing, now)
        if fresh:
            fresh_items.append(it)
    gone = 0
    for (kind, key), it in existing.items():
        if key in seen:
            continue
        if it.status == "installing":
            it.status, it.installed_at = "installed", now
        elif it.status in OPEN_STATES + ("new", "rejected", "ignored", "monitor"):
            it.status = "superseded"
            gone += 1
    # Alter Patch-Stand fuer die bisherigen Ansichten
    report = (await db.execute(select(PatchReport).where(PatchReport.agent_id == agent.id))).scalar_one_or_none()
    if report is None:
        report = PatchReport(agent_id=agent.id, updates=[], count=0)
        db.add(report)
    report.updates, report.count, report.scanned_at = updates, len(updates), now
    data = dict(agent.extra_data or {})
    data["reboot_pending"] = bool(payload.get("reboot_pending"))
    agent.extra_data = data
    await db.flush()
    await _classify_new(db, agent, settings, fresh_items, now)
    await db.commit()
    return {"total": len(updates), "new": len(fresh_items), "gone": gone}


async def ingest_docker_scan(db: AsyncSession, agent: Agent, payload: Any) -> dict:
    now = datetime.utcnow()
    payload = payload if isinstance(payload, dict) else {}
    agent.docker_inventory = payload
    agent.docker_scanned_at = now
    if not payload.get("available"):
        await db.commit()
        return {"total": 0, "new": 0}
    _, settings = await policy_for(db, agent)
    adv_cfg = await advisories.load_config(db)
    existing = {(i.kind, i.key): i for i in (await db.execute(
        select(PatchItem).where(PatchItem.agent_id == agent.id, PatchItem.kind == "docker"))).scalars()}
    seen, fresh_items = set(), []
    for c in payload.get("containers") or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        if not c.get("update_available") or c.get("excluded"):
            continue
        key = str(c["name"])[:255]
        seen.add(key)
        data = dict(c)
        if adv_cfg.get("github_auto"):
            repo = advisories.github_repo_for_image(c.get("image", ""), c.get("labels") or {})
            if repo:
                src = await advisories.ensure_github_source(db, repo)
                if src:
                    data["_github_source_id"] = src.id
        fields = {
            "title": f"{c['name']} ({c.get('image', '')})"[:512], "platform": "docker",
            "category": "container", "version_from": str(c.get("local_version") or (c.get("local_digest") or "")[7:19])[:128],
            "version_to": str(c.get("remote_version") or (c.get("remote_digest") or "")[7:19])[:128],
            "security": False, "reboot": False, "data": data,
        }
        it, fresh = await _upsert(db, agent, "docker", key, fields, existing, now)
        if fresh:
            fresh_items.append(it)
    for (kind, key), it in existing.items():
        if key in seen:
            continue
        if it.status == "installing":
            it.status, it.installed_at = "installed", now
        elif it.status in OPEN_STATES + ("new", "rejected", "ignored", "monitor"):
            it.status = "superseded"
    await db.flush()
    await _classify_new(db, agent, settings, fresh_items, now)
    await db.commit()
    return {"total": len(seen), "new": len(fresh_items)}


async def _classify_new(db: AsyncSession, agent: Agent, settings: dict,
                        items: List[PatchItem], now: datetime) -> None:
    if not items:
        return
    idx = await advisories.build_index(db)
    failed = await failed_fingerprints(db)
    for it in items:
        facts = {"kb": it.kb, "platform": it.platform, "key": it.key, "data": it.data or {}}
        it.advisories = advisories.match(idx, facts)
        it.urgent = advisories.is_urgent(idx, facts)
        decide_status(it, settings, now, failed)
    await _notify_approvals(db, agent, [i for i in items if i.status == "needs_approval"], settings)


async def reclassify(db: AsyncSession) -> int:
    """Nach einer Richtlinien-Aenderung: alles neu einstufen, was kein Mensch
    entschieden hat. Bei menschlich entschiedenen nur Stufe/Dringlichkeit
    nachtragen, falls sie fehlen - die Entscheidung bleibt."""
    now = datetime.utcnow()
    pols = await policies(db)
    agents = {a.id: a for a in (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars()}
    failed = await failed_fingerprints(db)
    rows = (await db.execute(select(PatchItem).where(
        PatchItem.status.in_(("auto", "needs_approval", "ignored", "monitor", "review")),
        PatchItem.decided_by == ""))).scalars().all()
    for it in rows:
        agent = agents.get(it.agent_id)
        if agent is None:
            continue
        settings = effective_settings(pick_policy(agent, pols))
        keep_ai = it.ai
        decide_status(it, settings, now, failed)
        if keep_ai and it.status == "review":
            it.ai = keep_ai
            apply_assessment(it, settings, failed)
    decided = (await db.execute(select(PatchItem).where(
        PatchItem.status.in_(("approved", "rejected", "installing", "failed")),
        or_(PatchItem.tier.is_(None), PatchItem.tier == "")))).scalars().all()
    for it in decided:
        agent = agents.get(it.agent_id)
        if agent is None:
            continue
        tier, rule_urg, _rule, _reason = classify(it, effective_settings(pick_policy(agent, pols)))
        if tier != "ignore":
            it.tier, it.urgency = tier, base_urgency(it, tier, rule_urg)[0]
    await db.commit()
    return len(rows)


# -----------------------------------------------------------------------------
# KI-Einschaetzung
# -----------------------------------------------------------------------------
ASSESS_SYSTEM = (
    "Du bist ein erfahrener Patch-Manager in einem IT-Betrieb. Du bewertest anstehende Updates "
    "für Windows, Linux und Docker-Container. Bewerte für jedes Update: Risiko einer Störung beim "
    "Einspielen (low, medium, high), ob es ohne menschliche Freigabe installiert werden kann "
    "(recommendation: install), eine Freigabe braucht (approve) oder vorerst zurückgehalten "
    "werden sollte (hold), eine kurze Begründung auf Deutsch (summary, höchstens zwei Sätze) und "
    "bekannte Probleme (known_issues). Stütze dich auf die mitgegebenen Meldungen und – falls "
    "verfügbar – eine Websuche nach bekannten Problemen. Sicherheitsupdates ohne bekannte Probleme "
    "sind in der Regel 'install'. Versionssprünge, Datenbanken, Treiber, Funktionsupdates und "
    "Updates mit gemeldeten Problemen eher 'approve' oder 'hold'. Erfinde keine Probleme. "
    "Bewerte außerdem die Dringlichkeit (urgency): 'immediate' nur, wenn das Update eine aktiv "
    "ausgenutzte oder öffentlich ausnutzbare kritische Lücke schließt (z. B. Remote Code Execution "
    "ohne Anmeldung) oder einen Fehler behebt, der das System gerade gefährdet; 'soon' für übliche "
    "Sicherheitsupdates und wichtige Fehlerbehebungen; 'low' für alles, was warten kann. Nenne den "
    "Grund für 'immediate' in urgency_reason (ein Satz). "
    "Antworte ausschließlich mit einem JSON-Objekt der Form "
    '{"items":[{"ref":"1","risk":"low","recommendation":"install","urgency":"soon",'
    '"urgency_reason":"","summary":"…","known_issues":[],"sources":["https://…"]}]}.'
)


def _describe(it: PatchItem, agent: Agent, ref: str, extra: List[dict]) -> str:
    d = it.data or {}
    lines = [f"[{ref}] {it.title}",
             f"  Plattform: {it.platform} ({agent.os or agent.platform or ''})"]
    if it.kb:
        lines.append(f"  KB/Advisory: {it.kb}")
    if it.category:
        lines.append(f"  Kategorie: {it.category}")
    if it.severity:
        lines.append(f"  Schwere: {it.severity}")
    if it.version_from or it.version_to:
        lines.append(f"  Version: {it.version_from or '?'} → {it.version_to or '?'}")
    if it.kind == "docker":
        lines.append(f"  Image: {d.get('image', '')}")
    if d.get("cves"):
        lines.append(f"  CVEs: {', '.join(d['cves'][:15])}")
    if it.urgent:
        lines.append("  Achtung: behebt eine aktiv ausgenutzte Lücke (CISA KEV).")
    for a in (it.advisories or [])[:5] + extra[:4]:
        lines.append(f"  Meldung: {a.get('title', '')[:200]} — {a.get('link') or a.get('url', '')}"
                     + (f" — {a.get('summary') or a.get('snippet', '')}"[:300] if (a.get('summary') or a.get('snippet')) else ""))
    return "\n".join(lines)


def _search_query(it: PatchItem) -> str:
    if it.platform == "windows":
        return f"{it.kb or it.title} known issues problems"
    if it.platform == "docker":
        image = (it.data or {}).get("image", it.key)
        return f"{image} {it.version_to} release breaking changes"
    return f"{(it.data or {}).get('package') or it.key} {it.version_to} update regression"


async def assess_batch(db: AsyncSession, agent: Agent, items: List[PatchItem], settings: dict) -> None:
    ai_cfg = await ai.load_config(db)
    adv_cfg = await advisories.load_config(db)
    failed = await failed_fingerprints(db)
    # Was schon jemand eingeschaetzt hat (gleiches Update auf einem anderen Rechner), nicht erneut fragen.
    todo = []
    cutoff = datetime.utcnow() - timedelta(days=7)
    for it in items:
        known = (await db.execute(select(PatchKnowledge).where(
            PatchKnowledge.fingerprint == _fingerprint(it)))).scalar_one_or_none()
        if known and known.created_at and known.created_at >= cutoff:
            it.ai = {**(known.data or {}), "cached": True}
            apply_assessment(it, settings, failed)
        else:
            todo.append(it)
    if not todo:
        return
    if ai_cfg.get("provider", "off") == "off":
        for it in todo:
            it.ai = {"error": "KI nicht eingerichtet"}
            apply_assessment(it, settings, failed)
        return
    provider = adv_cfg["websearch"].get("provider", "off")
    use_native = bool(settings["ai"].get("web_search")) and provider == "ai" and ai.supports_native_web_search(ai_cfg)
    blocks = []
    for n, it in enumerate(todo, start=1):
        extra: List[dict] = []
        if settings["ai"].get("web_search") and provider in ("searxng", "brave") and n <= 12:
            # Eine Websuche, die scheitert, darf die Einschaetzung nicht
            # verhindern - sonst blieben die Updates fuer immer "in Pruefung"
            # und tauchten nie zur Freigabe auf.
            try:
                extra = await advisories.web_search(adv_cfg, _search_query(it))
            except Exception as exc:                                # defensiv
                logger.info("Websuche für %s fehlgeschlagen: %s", it.title, exc)
                extra = []
        blocks.append(_describe(it, agent, str(n), extra))
    prompt = ("Bewerte diese Updates. Rechner: "
              f"{agent.hostname} ({agent.os or agent.platform}).\n\n" + "\n\n".join(blocks))
    if use_native:
        prompt += ("\n\nSuche bei Bedarf im Web nach bekannten Problemen zu diesen Updates "
                   "(höchstens ein paar Suchen) und nenne die Quellen.")
    try:
        result = await ai.ask_text(ai_cfg, ASSESS_SYSTEM, prompt, web_search=use_native)
        parsed = ai.parse_json_answer(result.get("answer", "")) or {}
        by_ref = {str(x.get("ref")): x for x in (parsed.get("items") or []) if isinstance(x, dict)}
        for n, it in enumerate(todo, start=1):
            x = by_ref.get(str(n))
            if not x:
                it.ai = {"error": "keine Einschätzung erhalten", "model": result.get("model")}
            else:
                urg = str(x.get("urgency", "")).lower()
                it.ai = {"risk": str(x.get("risk", "")).lower(),
                         "recommendation": str(x.get("recommendation", "")).lower(),
                         "urgency": urg if urg in URGENCIES else "",
                         "urgency_reason": str(x.get("urgency_reason", ""))[:300],
                         "summary": str(x.get("summary", ""))[:600],
                         "known_issues": [str(k)[:300] for k in (x.get("known_issues") or [])][:6],
                         "sources": [str(s)[:500] for s in (x.get("sources") or [])][:6]
                         or [s["url"] for s in (result.get("sources") or [])][:4],
                         "model": result.get("model"), "at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
                db.add(PatchKnowledge(fingerprint=_fingerprint(it), data=it.ai))
                await db.flush()
            apply_assessment(it, settings, failed)
    except Exception as exc:
        logger.warning("KI-Einschätzung für %s fehlgeschlagen: %s", agent.hostname, exc)
        for it in todo:
            it.ai = {"error": str(exc)[:300]}
            apply_assessment(it, settings, failed)


REVIEW_MAX_HOURS = 2


async def release_stuck_reviews(db: AsyncSession) -> int:
    """Updates, die laenger als REVIEW_MAX_HOURS in der KI-Pruefung haengen
    (KI nicht erreichbar, Fehler unterwegs), bekommen ihren geplanten Status -
    damit sie zur Freigabe erscheinen bzw. installiert werden koennen."""
    cutoff = datetime.utcnow() - timedelta(hours=REVIEW_MAX_HOURS)
    rows = (await db.execute(select(PatchItem).where(
        PatchItem.status == "review", PatchItem.last_seen < cutoff,
        PatchItem.first_seen < cutoff))).scalars().all()
    if not rows:
        return 0
    pols = await policies(db)
    failed = await failed_fingerprints(db)
    touched: Dict[int, List[PatchItem]] = {}
    for it in rows:
        agent = await db.get(Agent, it.agent_id)
        if agent is None:
            continue
        settings = effective_settings(pick_policy(agent, pols))
        it.ai = {**(it.ai or {}), "error": f"KI-Prüfung nach {REVIEW_MAX_HOURS} h ohne Ergebnis abgebrochen"}
        apply_assessment(it, settings, failed)
        touched.setdefault(agent.id, []).append(it)
    await db.commit()
    for agent_id, items in touched.items():
        agent = await db.get(Agent, agent_id)
        if agent:
            _, settings = await policy_for(db, agent)
            await _notify_approvals(db, agent, items, settings)
    await db.commit()
    return len(rows)


async def assess_pending(db: AsyncSession) -> int:
    rows = (await db.execute(select(PatchItem).where(PatchItem.status == "review")
                             .order_by(PatchItem.agent_id, PatchItem.id).limit(120))).scalars().all()
    if not rows:
        return 0
    pols = await policies(db)
    by_agent: Dict[int, List[PatchItem]] = {}
    for it in rows:
        by_agent.setdefault(it.agent_id, []).append(it)
    for agent_id, items in by_agent.items():
        agent = await db.get(Agent, agent_id)
        if agent is None:
            continue
        settings = effective_settings(pick_policy(agent, pols))
        for i in range(0, len(items), 25):
            await assess_batch(db, agent, items[i:i + 25], settings)
        await db.commit()
        await _notify_approvals(db, agent, [i for i in items if i.status == "needs_approval"], settings)
        await db.commit()
    return len(rows)


async def _notify_approvals(db: AsyncSession, agent: Agent, items: List[PatchItem], settings: dict) -> None:
    items = [i for i in items if i.status == "needs_approval"]
    if not items or not (settings.get("notify") or {}).get("approval_needed", True):
        return
    await db.flush()
    # Was sofort drauf muss, kommt als eigene, kritische Meldung - damit es
    # zwischen den gewoehnlichen Freigaben nicht untergeht.
    urgent = sorted((i for i in items if i.urgency == "immediate"), key=sort_key)
    normal = sorted((i for i in items if i.urgency != "immediate"), key=sort_key)
    for group, level, key, title in (
            (urgent, "critical", f"patch:approve:urgent:{agent.id}",
             f"{agent.hostname}: {len(urgent)} Update(s) sollten SOFORT drauf – Freigabe nötig"),
            (normal, "warning", f"patch:approve:{agent.id}",
             f"{agent.hostname}: {len(normal)} Update(s) brauchen eine Freigabe")):
        if not group:
            continue
        names = ", ".join(f"{i.title} [{TIER_LABEL.get(i.tier, i.tier or '?')}]" for i in group[:4]) \
            + (f" und {len(group) - 4} weitere" if len(group) > 4 else "")
        await notify.push(db, category="patch", level=level, title=title,
                          body=names, link="/rmm/patches?tab=approvals", action="approve",
                          data={"agent_uid": agent.agent_uid, "hostname": agent.hostname,
                                "item_ids": [i.id for i in group],
                                "urgency": "immediate" if group is urgent else "",
                                "tiers": sorted({i.tier for i in group if i.tier})},
                          event="patch_approval", key=key, commit=False)


# -----------------------------------------------------------------------------
# Entscheidungen von Menschen
# -----------------------------------------------------------------------------
async def decide(db: AsyncSession, item_ids: List[int], decision: str, user: str,
                 defer_days: int = 0) -> int:
    if decision not in ("approve", "reject", "reset", "defer"):
        raise ValueError("Entscheidung: approve, reject, defer oder reset.")
    rows = (await db.execute(select(PatchItem).where(PatchItem.id.in_(item_ids)))).scalars().all()
    now = datetime.utcnow()
    pols = await policies(db)
    changed = 0
    for it in rows:
        if it.status in ("installing", "installed", "superseded"):
            continue
        if decision == "approve":
            # Wer freigibt, will es im naechsten Fenster (bzw. sofort) - nicht erst nach der Rueckstellung.
            it.status, it.not_before = "approved", None
        elif decision == "reject":
            it.status = "rejected"
        elif decision == "defer":
            it.status = "approved"
            it.not_before = now + timedelta(days=max(1, defer_days))
        else:
            agent = await db.get(Agent, it.agent_id)
            it.decided_by = ""
            if agent:
                decide_status(it, effective_settings(pick_policy(agent, pols)), now)
            changed += 1
            continue
        it.decided_by, it.decided_at = user, now
        changed += 1
    await db.commit()
    # Offene Freigabe-Meldungen schliessen, wenn nichts mehr wartet.
    agents = {it.agent_id for it in rows}
    for agent_id in agents:
        waiting = (await db.execute(select(PatchItem.id).where(
            PatchItem.agent_id == agent_id, PatchItem.status == "needs_approval"))).first()
        if waiting is None:
            agent = await db.get(Agent, agent_id)
            if agent:
                await notify.resolve(db, action="approve", match={"agent_uid": agent.agent_uid})
    journal.fire(category="rmm", event=f"patch.{decision}", actor=user,
                 message=f"{user}: {changed} Update(s) — {decision}.", detail={"items": item_ids[:200]})
    return changed


# -----------------------------------------------------------------------------
# Laeufe
# -----------------------------------------------------------------------------
def _log(run: PatchRun, text: str, level: str = "info") -> None:
    entries = list(run.log or [])
    entries.append({"ts": datetime.utcnow().isoformat(timespec="seconds") + "Z", "level": level, "text": text})
    run.log = entries[-200:]


def _stage(run: PatchRun, status: str, text: str = "", level: str = "info") -> None:
    run.status, run.stage_since = status, datetime.utcnow()
    if text:
        _log(run, text, level)


async def _queue(db: AsyncSession, agent: Agent, kind: str, payload: Any, timeout: int,
                 source: str) -> Command:
    cmd = Command(agent_id=agent.id, shell="sh" if (agent.platform or "") != "windows" else "powershell",
                  command=json.dumps(payload) if not isinstance(payload, str) else payload,
                  timeout=timeout, kind=kind, source=source, created_by="patch")
    db.add(cmd)
    await db.flush()
    # Der Aufrufer committet erst danach — den wartenden Agent etwas später
    # wecken, damit er den Auftrag beim Nachsehen schon sieht.
    agentwake.notify(agent.id, delay=1.0)
    return cmd


async def queue_scan(db: AsyncSession, agent: Agent, settings: dict, actor: str = "system") -> List[int]:
    """patch_scan (+ docker_scan) einreihen, falls nicht schon einer wartet."""
    ids = []
    kinds = []
    if {"windows", "linux"} & set(settings.get("platforms") or []):
        kinds.append("patch_scan")
    if "docker" in (settings.get("platforms") or []) and (settings.get("docker") or {}).get("enabled", True) \
            and (agent.platform or "").lower() != "windows":
        kinds.append("docker_scan")
    for kind in kinds:
        busy = (await db.execute(select(Command.id).where(
            Command.agent_id == agent.id, Command.kind == kind,
            Command.status.in_(("pending", "running"))))).first()
        if busy:
            continue
        cmd = await _queue(db, agent, kind, kind, 1800, "Patch-Suche" if kind == "patch_scan" else "Docker-Suche")
        ids.append(cmd.id)
    agent.patch_scan_queued_at = datetime.utcnow()
    return ids


async def start_run(db: AsyncSession, agent: Agent, items: List[PatchItem], settings: dict,
                    policy_id: Optional[int], trigger: str, user: str,
                    snapshot: Optional[bool] = None) -> PatchRun:
    frozen = deepcopy(settings)
    if snapshot is not None:
        frozen["snapshot"]["enabled"] = bool(snapshot)
    if trigger == "urgent":
        frozen["reboot"] = ((settings.get("urgency") or {}).get("immediate") or {}).get("reboot", "if_needed")
    run = PatchRun(agent_id=agent.id, policy_id=policy_id, trigger=trigger, created_by=user,
                   status="pending", items=[i.id for i in items], settings=frozen,
                   snapshot={}, commands={}, log=[], started_at=datetime.utcnow())
    db.add(run)
    await db.flush()
    for it in items:
        it.run_id = run.id
    _log(run, f"Lauf angelegt ({trigger}, {user}): {len(items)} Update(s).")
    journal.fire(category="rmm", event="patch.run.created", actor=user, target=agent.hostname,
                 message=f"Patch-Lauf #{run.id} auf {agent.hostname}: {len(items)} Update(s).")
    return run


async def install_now(db: AsyncSession, agent: Agent, item_ids: List[int], user: str,
                      snapshot: Optional[bool] = None, trigger: str = "manual") -> Optional[PatchRun]:
    """Sofort installieren. None = alles waren Datenbank-Container; die laufen
    als DB-Update mit Probelauf weiter (siehe dbmigrate)."""
    active = (await db.execute(select(PatchRun).where(PatchRun.agent_id == agent.id,
                                                      PatchRun.status.in_(ACTIVE_RUN)))).scalars().first()
    if active:
        raise ValueError(f"Auf {agent.hostname} läuft bereits Lauf #{active.id}.")
    q = select(PatchItem).where(PatchItem.agent_id == agent.id)
    q = q.where(PatchItem.id.in_(item_ids)) if item_ids else q.where(PatchItem.status.in_(INSTALLABLE))
    items = [i for i in (await db.execute(q)).scalars().all()
             if i.status not in ("installing", "installed", "superseded", "monitor")]
    if not items:
        raise ValueError("Keine installierbaren Updates ausgewählt.")
    now = datetime.utcnow()
    for it in items:
        if it.status not in ("approved",):
            it.status, it.decided_by, it.decided_at = "approved", user, now
    # Datenbank-Container nie als blankes Container-Update: sie bekommen
    # Sicherung, Probelauf und Pruefung (app/dbmigrate.py).
    from . import dbmigrate
    items = await dbmigrate.from_patch_items(db, agent, items, user)
    if not items:
        await db.commit()
        return None
    policy, settings = await policy_for(db, agent)
    run = await start_run(db, agent, items, settings, policy.id if policy else None, trigger, user, snapshot)
    await db.commit()
    return run


async def _snapshot_step(db: AsyncSession, run: PatchRun, agent: Agent) -> None:
    s = run.settings["snapshot"]
    if not s.get("enabled"):
        _stage(run, "baseline", "Ohne Sicherung (in der Richtlinie abgeschaltet).")
        return
    found = await pve.link_for(db, agent.id)
    if not found:
        if s.get("required"):
            _stage(run, "failed", "Keine Proxmox-VM zugeordnet — Sicherung vorgeschrieben, nichts installiert.", "error")
            run.error = "keine Sicherung möglich"
            return
        _stage(run, "baseline", "Keine Proxmox-VM zugeordnet — weiter ohne Sicherung.", "warning")
        return
    host, link = found
    ref = {"host_id": host.id, "node": link.node, "vmid": link.vmid, "guest_type": link.guest_type,
           "guest_name": link.guest_name}
    mode = s.get("mode", "snapshot")
    try:
        if mode in ("snapshot", "auto"):
            name = pve.snapshot_name()
            upid = await pve.create_snapshot(host, link, name, f"RIMPERIO vor Patch-Lauf #{run.id}",
                                             include_ram=bool(s.get("include_ram")))
            run.snapshot = {**ref, "mode": "snapshot", "name": name, "upid": upid,
                            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
            _stage(run, "snapshotting", f"Snapshot {name} auf {host.name}/{link.node} VM {link.vmid} angestoßen.")
        else:
            storage = s.get("storage") or host.default_storage
            upid = await pve.vzdump(host, link, storage, f"RIMPERIO vor Patch-Lauf #{run.id}")
            run.snapshot = {**ref, "mode": "backup", "storage": storage, "upid": upid,
                            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
            _stage(run, "snapshotting", f"vzdump-Sicherung auf {storage or 'Standardspeicher'} angestoßen.")
    except pve.PveError as exc:
        if mode == "auto" and (s.get("storage") or host.default_storage):
            run.settings = {**run.settings, "snapshot": {**s, "mode": "backup"}}
            _log(run, f"Snapshot nicht möglich ({exc}) — versuche vzdump-Sicherung.", "warning")
            return await _snapshot_step(db, run, agent)
        if s.get("required"):
            _stage(run, "failed", f"Sicherung fehlgeschlagen, nichts installiert: {exc}", "error")
            run.error = str(exc)
        else:
            _stage(run, "baseline", f"Sicherung fehlgeschlagen ({exc}) — weiter ohne.", "warning")


async def _cmd(db: AsyncSession, run: PatchRun, stage: str) -> Optional[Command]:
    cid = (run.commands or {}).get(stage)
    return await db.get(Command, cid) if cid else None


def _parse(cmd: Command) -> Any:
    try:
        return json.loads(cmd.stdout or "")
    except ValueError:
        return None


def compare_health(before: dict, after: dict, settings: dict, windows: bool,
                   updated_containers: Optional[List[str]] = None) -> List[str]:
    before, after = before or {}, after or {}
    ignore = {s.lower() for s in IGNORED_SERVICES} | \
        {str(s).lower() for s in (settings.get("verify") or {}).get("ignore_services") or []}
    problems = []
    missing = set(before.get("running") or []) - set(after.get("running") or [])
    if windows:
        stopped_auto = set(after.get("auto_stopped") or [])
        missing = {m for m in missing if m in stopped_auto}
    missing = sorted(m for m in missing if m.lower() not in ignore)
    if missing:
        problems.append("Dienste laufen nicht mehr: " + ", ".join(missing[:15]))
    failed = sorted(set(after.get("failed") or []) - set(before.get("failed") or []))
    failed = [f for f in failed if f.lower() not in ignore]
    if failed:
        problems.append("Neu fehlgeschlagene Units: " + ", ".join(failed[:15]))
    gone = sorted(set(before.get("containers") or []) - set(after.get("containers") or []))
    if gone:
        problems.append("Container laufen nicht mehr: " + ", ".join(gone[:15]))
    bad = sorted(set(after.get("containers_bad") or []) - set(before.get("containers_bad") or []))
    if bad:
        problems.append("Container ungesund: " + ", ".join(bad[:15]))
    return problems


async def _finish(db: AsyncSession, run: PatchRun, agent: Agent, ok: bool, text: str) -> None:
    now = datetime.utcnow()
    run.finished_at = now
    agent.maintenance_until = None
    settings = run.settings
    items = (await db.execute(select(PatchItem).where(PatchItem.id.in_(run.items or [])))).scalars().all()
    if ok:
        _stage(run, "succeeded", text)
        snap = dict(run.snapshot or {})
        if snap.get("name") or snap.get("volid"):
            days = int(settings["snapshot"].get("retention_days", 3))
            snap["delete_after"] = (now + timedelta(days=days)).isoformat(timespec="seconds") + "Z"
            run.snapshot = snap
            _log(run, f"Sicherung wird am {(now + timedelta(days=days)):%d.%m.%Y %H:%M} UTC gelöscht.")
        if (settings.get("notify") or {}).get("run_succeeded") or \
                (run.trigger == "urgent" and (settings.get("notify") or {}).get("urgent_run", True)):
            await notify.push(db, category="patch", level="info",
                              title=f"{agent.hostname}: Patch-Lauf #{run.id} erfolgreich", body=text,
                              link=f"/rmm/patches?tab=runs&run={run.id}", event="patch_done",
                              data={"run_id": run.id, "agent_uid": agent.agent_uid}, commit=False)
    else:
        _stage(run, "failed", text, "error")
        run.error = text[:2000]
        for it in items:
            if it.status == "installing":
                it.status = "failed"
        await _hold_siblings(db, run, agent, [i for i in items if i.status in ("failed", "installed")])
        has_snapshot = bool((run.snapshot or {}).get("name")) and (run.snapshot or {}).get("mode") == "snapshot"
        if has_snapshot and (settings.get("rollback") or {}).get("auto"):
            await _rollback(db, run, "system (automatisch)")
        elif (settings.get("notify") or {}).get("run_failed", True):
            await notify.push(db, category="patch", level="critical",
                              title=f"{agent.hostname}: Patch-Lauf #{run.id} fehlgeschlagen",
                              body=text + (" Ein Snapshot liegt bereit — ein Klick rollt zurück." if has_snapshot else ""),
                              link=f"/rmm/patches?tab=runs&run={run.id}",
                              action="rollback" if has_snapshot else "",
                              data={"run_id": run.id, "agent_uid": agent.agent_uid},
                              event="patch_failed", key=f"patch:failed:{run.id}", commit=False)
    journal.fire(category="rmm", event="patch.run." + ("succeeded" if ok else "failed"),
                 level="info" if ok else "error", target=agent.hostname, ok=ok,
                 message=f"Patch-Lauf #{run.id} auf {agent.hostname}: {text}")


async def _hold_siblings(db: AsyncSession, run: PatchRun, agent: Agent, items: List[PatchItem]) -> int:
    """Ein Lauf ist fehlgeschlagen: dieselben Updates auf den anderen Rechnern
    nicht mehr automatisch installieren, sondern auf eine Freigabe warten lassen."""
    fps = {_fingerprint(i) for i in items}
    if not fps:
        return 0
    rows = (await db.execute(select(PatchItem).where(
        PatchItem.agent_id != agent.id, PatchItem.status == "auto", PatchItem.decided_by == "",
        PatchItem.platform.in_({i.platform for i in items})))).scalars().all()
    held = [it for it in rows if _fingerprint(it) in fps]
    for it in held:
        it.status = "needs_approval"
        if it.tier == "normal":
            it.tier = "important"
            it.data = {**(it.data or {}), "escalated": True}
        base = (it.data or {}).get("rule_reason") or it.rule or "Regel"
        it.reason = (f"{base} · angehalten: auf {agent.hostname} fehlgeschlagen (Lauf #{run.id}) – "
                     "wartet auf Freigabe")[:1000]
    if held:
        _log(run, f"{len(held)} gleiche(s) Update(s) auf anderen Rechnern angehalten – sie warten jetzt auf eine Freigabe.",
             "warning")
        await notify.push(db, category="patch", level="warning",
                          title=f"{len(held)} Update(s) angehalten: auf {agent.hostname} fehlgeschlagen",
                          body="Dieselben Updates laufen auf den anderen Rechnern erst nach einer Freigabe.",
                          link="/rmm/patches?tab=approvals", action="approve",
                          data={"run_id": run.id, "item_ids": [i.id for i in held][:200]},
                          event="patch_approval", key=f"patch:held:{run.id}", commit=False)
    return len(held)


async def _rollback(db: AsyncSession, run: PatchRun, user: str) -> None:
    snap = run.snapshot or {}
    host = await db.get(PveHost, snap.get("host_id") or 0)
    if host is None or not snap.get("name"):
        raise ValueError("Für diesen Lauf gibt es keinen Snapshot.")
    upid = await pve.rollback_snapshot(host, snap, snap["name"])
    run.snapshot = {**snap, "rollback_upid": upid, "rollback_by": user}
    _stage(run, "rolling_back", f"{user}: Zurückrollen auf {snap['name']} angestoßen.", "warning")


async def rollback(db: AsyncSession, run_id: int, user: str) -> PatchRun:
    run = await db.get(PatchRun, run_id)
    if run is None:
        raise ValueError("Unbekannter Lauf.")
    if run.status in ACTIVE_RUN and run.status != "verifying":
        raise ValueError("Der Lauf ist noch aktiv.")
    await _rollback(db, run, user)
    await notify.resolve(db, action="rollback", match={"run_id": run.id}, commit=False)
    await db.commit()
    return run


async def delete_snapshot_now(db: AsyncSession, run_id: int, user: str) -> PatchRun:
    run = await db.get(PatchRun, run_id)
    if run is None:
        raise ValueError("Unbekannter Lauf.")
    await _delete_snapshot(db, run, user)
    await db.commit()
    return run


async def keep_snapshot(db: AsyncSession, run_id: int, user: str, keep: bool = True) -> PatchRun:
    run = await db.get(PatchRun, run_id)
    if run is None:
        raise ValueError("Unbekannter Lauf.")
    snap = dict(run.snapshot or {})
    snap["keep"] = keep
    run.snapshot = snap
    _log(run, f"{user}: Sicherung {'behalten' if keep else 'wieder zum Löschen freigegeben'}.")
    await db.commit()
    return run


async def _delete_snapshot(db: AsyncSession, run: PatchRun, user: str) -> None:
    snap = dict(run.snapshot or {})
    if snap.get("deleted_at"):
        return
    host = await db.get(PveHost, snap.get("host_id") or 0)
    if host is None:
        raise ValueError("Der Proxmox-Host dieser Sicherung existiert nicht mehr.")
    if snap.get("mode") == "backup":
        if not snap.get("volid"):
            raise ValueError("Die Sicherungsdatei ist unbekannt — bitte in Proxmox löschen.")
        await pve.delete_volume(host, snap["node"], snap.get("storage", ""), snap["volid"])
    else:
        await pve.delete_snapshot(host, snap, snap["name"])
    snap["deleted_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    snap["deleted_by"] = user
    run.snapshot = snap
    _log(run, f"{user}: Sicherung {snap.get('name') or snap.get('volid')} gelöscht.")


async def advance(db: AsyncSession, run: PatchRun) -> None:
    """Ein Schritt im Ablauf eines Laufs."""
    agent = await db.get(Agent, run.agent_id)
    if agent is None:
        _stage(run, "failed", "Rechner existiert nicht mehr.", "error")
        return
    s = run.settings
    now = datetime.utcnow()
    age = (now - (run.stage_since or now)).total_seconds()
    items = (await db.execute(select(PatchItem).where(PatchItem.id.in_(run.items or [])))).scalars().all()

    if run.status == "pending":
        await _snapshot_step(db, run, agent)
        return

    if run.status == "snapshotting":
        snap = run.snapshot or {}
        host = await db.get(PveHost, snap.get("host_id") or 0)
        try:
            st = await pve.task_status(host, pve.node_of_upid(snap.get("upid", ""), snap.get("node", "")),
                                       snap.get("upid", "")) if host else {"done": True, "ok": False, "exitstatus": "Host fehlt"}
        except pve.PveError as exc:
            st = {"done": age > 1800, "ok": False, "exitstatus": str(exc)}
        if not st["done"]:
            if age > 3 * 3600:
                st = {"done": True, "ok": False, "exitstatus": "Zeitüberschreitung"}
            else:
                return
        if st["ok"]:
            if snap.get("mode") == "backup" and host:
                try:
                    volid = await pve.find_backup(host, snap, snap.get("storage", ""), run.started_at or now)
                    run.snapshot = {**snap, "volid": volid}
                except pve.PveError:
                    pass
            _stage(run, "baseline", "Sicherung fertig.")
        elif s["snapshot"].get("mode") == "auto" and snap.get("mode") == "snapshot" and (s["snapshot"].get("storage") or (host and host.default_storage)):
            run.settings = {**s, "snapshot": {**s["snapshot"], "mode": "backup"}}
            _log(run, f"Snapshot fehlgeschlagen ({st['exitstatus']}) — versuche vzdump.", "warning")
            run.snapshot = {}
            await _snapshot_step(db, run, agent)
        elif s["snapshot"].get("required"):
            run.snapshot = {**snap, "failed": st["exitstatus"]}
            await _finish(db, run, agent, False, f"Sicherung fehlgeschlagen ({st['exitstatus']}), nichts installiert.")
        else:
            run.snapshot = {**snap, "failed": st["exitstatus"]}
            _stage(run, "baseline", f"Sicherung fehlgeschlagen ({st['exitstatus']}) — weiter ohne.", "warning")
        return

    if run.status == "baseline":
        cmd = await _cmd(db, run, "baseline")
        if cmd is None:
            cmd = await _queue(db, agent, "health_snapshot", "{}", 300, f"Patch-Lauf #{run.id}: Zustand vorher")
            run.commands = {**(run.commands or {}), "baseline": cmd.id}
            return
        if cmd.status in ("pending", "running"):
            if age > 1800:
                _log(run, "Kein Gesundheitsbild vorher erhalten — weiter ohne Vergleich.", "warning")
            else:
                return
        run.baseline = _parse(cmd) if cmd.status == "done" else None
        # Installation einreihen
        os_items = [i for i in items if i.kind == "os"]
        dk_items = [i for i in items if i.kind == "docker"]
        cmds = dict(run.commands or {})
        if os_items:
            c = await _queue(db, agent, "patch_apply",
                             {"ids": [i.key for i in os_items], "reboot": s.get("reboot", "if_needed"),
                              "reboot_delay": 60}, 14400, f"Patch-Lauf #{run.id}: {len(os_items)} Update(s)")
            cmds["apply"] = c.id
        if dk_items:
            d = s.get("docker") or {}
            c = await _queue(db, agent, "docker_update",
                             {"containers": [i.key for i in dk_items], "cleanup": bool(d.get("cleanup_images", True)),
                              "health_timeout": int(d.get("health_timeout", 120)),
                              "rollback": bool(d.get("rollback_on_fail", True)),
                              "prefer_compose": bool(d.get("prefer_compose", True))},
                             7200, f"Patch-Lauf #{run.id}: {len(dk_items)} Container")
            cmds["docker"] = c.id
        run.commands = cmds
        for it in items:
            it.status = "installing"
        agent.maintenance_until = now + timedelta(minutes=int(s["window"].get("duration_minutes", 180)) + 90)
        _stage(run, "installing", f"Installation eingereiht ({len(os_items)} System, {len(dk_items)} Container).")
        return

    if run.status == "installing":
        apply_cmd, docker_cmd = await _cmd(db, run, "apply"), await _cmd(db, run, "docker")
        waiting = [c for c in (apply_cmd, docker_cmd) if c is not None and c.status in ("pending", "running")]
        if waiting:
            if age > 5 * 3600:
                await _finish(db, run, agent, False, "Installation hat nicht geantwortet (Zeitüberschreitung).")
            return
        reboot, problems = False, []
        by_key = {(i.kind, i.key): i for i in items}
        if apply_cmd is not None:
            res = _parse(apply_cmd) or {}
            for key in res.get("installed") or []:
                it = by_key.get(("os", key))
                if it:
                    it.status, it.installed_at = "installed", now
            if "alle" in (res.get("installed") or []):
                for it in items:
                    if it.kind == "os" and it.status == "installing":
                        it.status, it.installed_at = "installed", now
            for f in res.get("failed") or []:
                it = by_key.get(("os", str(f.get("id"))))
                if it:
                    it.status, it.error = "failed", str(f.get("error", ""))[:2000]
                problems.append(f"{f.get('title') or f.get('id')}: {f.get('error')}")
            if apply_cmd.status != "done" and not res:
                problems.append(f"Installation: {(apply_cmd.stderr or apply_cmd.status)[:500]}")
                for it in items:
                    if it.kind == "os" and it.status == "installing":
                        it.status, it.error = "failed", (apply_cmd.stderr or "")[:2000]
            reboot = bool(res.get("reboot_scheduled"))
            _log(run, f"System: {res.get('message') or apply_cmd.status}"
                 + (" — Neustart geplant." if reboot else "")
                 + (" — Neustart nötig, laut Richtlinie aber nicht automatisch." if res.get("reboot_required") and not reboot else ""))
        updated_containers = []
        if docker_cmd is not None:
            res = _parse(docker_cmd) or {}
            for o in res.get("results") or []:
                it = by_key.get(("docker", str(o.get("name"))))
                if not it:
                    continue
                if o.get("status") in ("updated", "unchanged"):
                    it.status, it.installed_at = "installed", now
                    updated_containers.append(it.key)
                else:
                    it.status, it.error = "failed", str(o.get("message", ""))[:2000]
                    problems.append(f"Container {it.key}: {o.get('status')} — {o.get('message')}")
            if docker_cmd.status != "done" and not res:
                problems.append(f"Docker: {(docker_cmd.stderr or docker_cmd.status)[:500]}")
            _log(run, f"Docker: {res.get('message') or docker_cmd.status}")
        run.verify = {"install_problems": problems, "updated_containers": updated_containers}
        if reboot:
            _stage(run, "rebooting", "Warte auf den Neustart.")
        else:
            _stage(run, "settling", "Warte, bis sich das System gesetzt hat.")
        return

    if run.status == "rebooting":
        rebooted = agent.boot_time is not None and agent.boot_time > (run.stage_since - timedelta(minutes=2))
        from .rmm import is_online
        if rebooted and is_online(agent, 180):
            _stage(run, "settling", f"Neustart erfolgt (gestartet {agent.boot_time:%H:%M} UTC).")
            return
        if age > int(s["verify"].get("reboot_timeout_minutes", 45)) * 60:
            await _finish(db, run, agent, False,
                          "Rechner ist nach dem Neustart nicht zurückgekommen.")
        return

    if run.status == "settling":
        if not s["verify"].get("enabled", True):
            await _finish(db, run, agent, not (run.verify or {}).get("install_problems"),
                          _summary(run, items, []))
            return
        if age < int(s["verify"].get("settle_minutes", 5)) * 60:
            return
        c = await _queue(db, agent, "health_snapshot", "{}", 300, f"Patch-Lauf #{run.id}: Zustand nachher")
        run.commands = {**(run.commands or {}), "after": c.id}
        await queue_scan(db, agent, s)
        _stage(run, "verifying", "Prüfe den Zustand nach dem Update.")
        return

    if run.status == "verifying":
        cmd = await _cmd(db, run, "after")
        if cmd is not None and cmd.status in ("pending", "running"):
            if age > 1800:
                await _finish(db, run, agent, False, "Rechner meldet sich nach dem Update nicht (kein Gesundheitsbild).")
            return
        run.after = _parse(cmd) if cmd is not None and cmd.status == "done" else None
        problems = list((run.verify or {}).get("install_problems") or [])
        windows = (agent.platform or "").lower().startswith("win")
        if run.baseline and run.after:
            problems += compare_health(run.baseline, run.after, s, windows,
                                       (run.verify or {}).get("updated_containers"))
        elif run.baseline and not run.after:
            problems.append("Kein Gesundheitsbild nach dem Update.")
        if s["verify"].get("require_checks", True):
            since = run.stage_since - timedelta(minutes=int(s["verify"].get("settle_minutes", 5)))
            failing = (await db.execute(select(CheckResult).where(
                CheckResult.agent_id == agent.id, CheckResult.status == "fail",
                CheckResult.ts >= since))).scalars().all()
            if failing:
                problems.append(f"{len(failing)} Check(s) schlagen fehl.")
        run.verify = {**(run.verify or {}), "problems": problems,
                      "checked_at": now.isoformat(timespec="seconds") + "Z"}
        await _finish(db, run, agent, not problems, _summary(run, items, problems))
        return

    if run.status == "rolling_back":
        snap = run.snapshot or {}
        host = await db.get(PveHost, snap.get("host_id") or 0)
        try:
            st = await pve.task_status(host, pve.node_of_upid(snap.get("rollback_upid", ""), snap.get("node", "")),
                                       snap.get("rollback_upid", "")) if host else {"done": True, "ok": False, "exitstatus": "Host fehlt"}
        except pve.PveError as exc:
            st = {"done": age > 1800, "ok": False, "exitstatus": str(exc)}
        if not st["done"]:
            return
        if st["ok"]:
            try:
                await pve.start_guest(host, snap)
            except pve.PveError as exc:
                _log(run, f"VM nach dem Zurückrollen nicht gestartet: {exc}", "warning")
            for it in items:
                if it.status in ("installed", "failed", "installing"):
                    it.status, it.decided_by = "rejected", "Zurückgerollt"
            _stage(run, "rolled_back", "Zurückgerollt — die VM läuft wieder mit dem Stand vor dem Update. "
                                       "Die Updates sind als abgelehnt markiert.", "warning")
            await notify.push(db, category="patch", level="warning",
                              title=f"{agent.hostname}: auf den Stand vor Lauf #{run.id} zurückgerollt",
                              body="Die betroffenen Updates sind abgelehnt und werden nicht erneut installiert.",
                              link=f"/rmm/patches?tab=runs&run={run.id}", event="patch_failed",
                              data={"run_id": run.id}, commit=False)
        else:
            _stage(run, "failed", f"Zurückrollen fehlgeschlagen: {st['exitstatus']}", "error")
        run.finished_at = now
        return


def _summary(run: PatchRun, items: List[PatchItem], problems: List[str]) -> str:
    done = sum(1 for i in items if i.status == "installed")
    failed = sum(1 for i in items if i.status == "failed")
    text = f"{done} installiert, {failed} fehlgeschlagen."
    if problems:
        text += " Probleme: " + " | ".join(problems[:6])
    else:
        text += " Prüfung ohne Befund."
    return text


# -----------------------------------------------------------------------------
# Takt
# -----------------------------------------------------------------------------
async def _timeout_commands(db: AsyncSession) -> None:
    now = datetime.utcnow()
    rows = (await db.execute(select(Command).where(Command.status.in_(("pending", "running"))))).scalars().all()
    for c in rows:
        started = c.picked_at or c.created_at or now
        limit = timedelta(seconds=int(c.timeout or 300)) + timedelta(minutes=COMMAND_GRACE_MINUTES)
        if c.status == "pending":
            limit = timedelta(days=2)
        if now - started > limit:
            c.status, c.finished_at = "timeout", now
            c.stderr = (c.stderr or "") + "Kein Ergebnis vom Agent innerhalb der Frist."


async def _installable(db: AsyncSession, agent: Agent, settings: dict, now: datetime,
                       urgency: Optional[str] = None) -> List[PatchItem]:
    q = select(PatchItem).where(
        PatchItem.agent_id == agent.id, PatchItem.status.in_(INSTALLABLE),
        or_(PatchItem.not_before.is_(None), PatchItem.not_before <= now))
    if urgency:
        q = q.where(PatchItem.urgency == urgency)
    items = (await db.execute(q)).scalars().all()
    plats = set(settings.get("platforms") or [])
    return sorted((i for i in items if i.platform in plats), key=sort_key)


async def _window_run(db: AsyncSession, agent: Agent, policy: PatchPolicy, settings: dict,
                      now: datetime) -> bool:
    """Im Wartungsfenster: einmal je Fenster alles Installierbare; kritische
    Datenbank-Container vorbereiten (Sicherung + Probelauf)."""
    is_open, begin, _end = window_state(settings, now)
    if not is_open:
        return False
    await _prepare_db(db, agent, settings)
    done_this_window = (await db.execute(select(PatchRun.id).where(
        PatchRun.agent_id == agent.id, PatchRun.trigger == "schedule",
        PatchRun.created_at >= begin))).first()
    if done_this_window:
        return False
    items = await _installable(db, agent, settings, now)
    if not items:
        return False
    from . import dbmigrate
    items = await dbmigrate.from_patch_items(db, agent, items, "system")
    if not items:
        return False
    await start_run(db, agent, items, settings, policy.id, "schedule", "system")
    return True


URGENT_RETRY_HOURS = 24


async def _urgent_run(db: AsyncSession, agent: Agent, policy: PatchPolicy, settings: dict,
                      now: datetime) -> bool:
    """„Sofort“: nicht aufs Wartungsfenster warten. Gilt nur, wenn die
    Richtlinie ueberhaupt automatisch installiert (Fenster an) und Sofort-
    Updates ausserhalb des Fensters erlaubt. Je Update und Version ein Versuch
    in URGENT_RETRY_HOURS - danach wartet es aufs Fenster."""
    imm = (settings.get("urgency") or {}).get("immediate") or {}
    if not (settings.get("window") or {}).get("enabled", True) or not imm.get("outside_window", True):
        return False
    cutoff = (now - timedelta(hours=URGENT_RETRY_HOURS)).isoformat(timespec="seconds")
    items = []
    for it in await _installable(db, agent, settings, now, urgency="immediate"):
        tried = (it.data or {}).get("urgent_attempt") or {}
        if tried.get("version") == it.version_to and str(tried.get("at") or "") >= cutoff:
            continue
        items.append(it)
    if not items:
        return False
    stamp = {"at": now.isoformat(timespec="seconds")}
    for it in items:
        it.data = {**(it.data or {}), "urgent_attempt": {**stamp, "version": it.version_to}}
    from . import dbmigrate
    items = await dbmigrate.from_patch_items(db, agent, items, "system (sofort)")
    if not items:
        return False
    run = await start_run(db, agent, items, settings, policy.id, "urgent", "system")
    _log(run, "Sofort-Lauf außerhalb des Wartungsfensters: " + ", ".join(i.title for i in items[:5])
         + (f" und {len(items) - 5} weitere" if len(items) > 5 else ""), "warning")
    return True


async def _prepare_db(db: AsyncSession, agent: Agent, settings: dict) -> int:
    """Kritische Datenbank-Container: Analyse, Sicherung und Probelauf schon vor
    der Freigabe (dbmigrate, ohne automatische Ausfuehrung). Die Freigabe kommt
    dann mit dem Ergebnis des Probelaufs; ausgefuehrt wird erst danach."""
    if not ((settings.get("tiers") or {}).get("critical") or {}).get("prepare_db", True):
        return 0
    if "docker" not in (settings.get("platforms") or []) or (agent.platform or "").lower().startswith("win"):
        return 0
    from . import dbmigrate
    rows = (await db.execute(select(PatchItem).where(
        PatchItem.agent_id == agent.id, PatchItem.kind == "docker",
        PatchItem.status == "needs_approval", PatchItem.tier == "critical"))).scalars().all()
    n = 0
    for it in rows:
        d = dict(it.data or {})
        if d.get("db_prepared") or not dbmigrate.is_db_image(d.get("image", "")):
            continue
        d["db_prepared"] = True
        try:
            m = await dbmigrate.create(db, agent, it.key, "system (Vorbereitung)", mode="auto",
                                       patch_item_id=it.id,
                                       snapshot=bool((settings.get("snapshot") or {}).get("enabled", True)),
                                       auto_execute_minor=False)
            d["db_migration_id"] = m.id
            it.reason = (f"{it.reason} · Sicherung und Probelauf laufen vorab (DB-Update #{m.id}) – "
                         "die Freigabe kommt mit Ergebnis")[:1000]
            n += 1
        except ValueError as exc:
            logger.info("DB-Vorbereitung für %s nicht angelegt: %s", it.key, exc)
        it.data = d
    return n


async def tick() -> None:
    async with async_session() as db:
        await _timeout_commands(db)
        await db.commit()

        # 1. Laeufe voranbringen
        runs = (await db.execute(select(PatchRun).where(PatchRun.status.in_(ACTIVE_RUN)))).scalars().all()
        for run in runs:
            try:
                await advance(db, run)
            except Exception as exc:                                # defensiv
                logger.exception("Lauf #%s: %s", run.id, exc)
                _log(run, f"Interner Fehler: {exc}", "error")
            await db.commit()

        # 2. KI-Einschaetzungen
        try:
            await assess_pending(db)
        except Exception as exc:                                    # defensiv
            logger.warning("KI-Einschätzung: %s", exc)
            await db.rollback()
        try:
            await release_stuck_reviews(db)
        except Exception as exc:                                    # defensiv
            logger.warning("Hängende KI-Prüfungen: %s", exc)
            await db.rollback()

        # 3. Suchen und Fenster
        from .rmm import is_online
        now = datetime.utcnow()
        pols = await policies(db)
        agents = (await db.execute(select(Agent).where(Agent.agent_uid.is_not(None)))).scalars().all()
        active_agents = {r.agent_id for r in (await db.execute(
            select(PatchRun).where(PatchRun.status.in_(ACTIVE_RUN)))).scalars()}
        for agent in agents:
            policy = pick_policy(agent, pols)
            if policy is None:
                continue
            settings = effective_settings(policy)
            if not is_online(agent, 300):
                continue
            if scan_due(settings, agent.patch_scan_queued_at, now):
                await queue_scan(db, agent, settings)
            if agent.id in active_agents:
                continue
            try:
                if await _window_run(db, agent, policy, settings, now):
                    active_agents.add(agent.id)
                elif await _urgent_run(db, agent, policy, settings, now):
                    active_agents.add(agent.id)
            except Exception as exc:                                # defensiv
                logger.warning("Patch-Lauf für %s nicht gestartet: %s", agent.hostname, exc)
                await db.rollback()
                continue
            await db.commit()
        await db.commit()

        # 4. Sicherungen nach Frist loeschen
        done = (await db.execute(select(PatchRun).where(PatchRun.status == "succeeded"))).scalars().all()
        for run in done:
            snap = run.snapshot or {}
            if not snap.get("delete_after") or snap.get("deleted_at") or snap.get("keep"):
                continue
            try:
                due = datetime.fromisoformat(snap["delete_after"].rstrip("Z"))
            except ValueError:
                continue
            if due > now:
                continue
            try:
                await _delete_snapshot(db, run, "system")
            except Exception as exc:
                snap = dict(run.snapshot or {})
                snap["delete_error"] = str(exc)[:300]
                snap["delete_after"] = (now + timedelta(hours=6)).isoformat(timespec="seconds") + "Z"
                run.snapshot = snap
                _log(run, f"Sicherung nicht gelöscht ({exc}) — neuer Versuch in 6 h.", "warning")
            await db.commit()


async def loop() -> None:
    await asyncio.sleep(30)
    automatch_at = datetime.min
    while True:
        try:
            await tick()
            if datetime.utcnow() - automatch_at > timedelta(hours=1):
                automatch_at = datetime.utcnow()
                async with async_session() as db:
                    for host in (await db.execute(select(PveHost).where(PveHost.enabled.is_(True)))).scalars().all():
                        try:
                            await pve.refresh_guests(db, host)
                        except pve.PveError as exc:
                            logger.info("Proxmox %s: %s", host.name, exc)
                    await pve.automatch(db)
        except Exception as exc:                                    # defensiv
            logger.warning("Patch-Takt: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)


# -----------------------------------------------------------------------------
# Ausgabe
# -----------------------------------------------------------------------------
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def public_item(it: PatchItem, agent: Optional[Agent] = None) -> dict:
    d = dict(it.data or {})
    d.pop("_planned", None)
    return {"id": it.id, "agent_id": it.agent_id,
            "agent_uid": agent.agent_uid if agent else None,
            "hostname": agent.hostname if agent else None,
            "kind": it.kind, "key": it.key, "title": it.title, "platform": it.platform,
            "category": it.category, "severity": it.severity, "kb": it.kb,
            "version_from": it.version_from, "version_to": it.version_to,
            "security": it.security, "reboot": it.reboot, "urgent": it.urgent,
            "tier": it.tier or "", "tier_label": TIER_LABEL.get(it.tier or "", ""),
            "tier_rule": d.get("tier_rule") or it.tier or "", "escalated": bool(d.get("escalated")),
            "urgency": it.urgency or "", "urgency_label": URGENCY_LABEL.get(it.urgency or "", ""),
            "db_migration_id": d.get("db_migration_id"),
            "status": it.status, "status_label": STATUS_LABEL.get(it.status, it.status),
            "rule": it.rule, "reason": it.reason, "ai": it.ai, "advisories": it.advisories or [],
            "decided_by": it.decided_by, "decided_at": _iso(it.decided_at),
            "not_before": _iso(it.not_before), "first_seen": _iso(it.first_seen),
            "last_seen": _iso(it.last_seen), "installed_at": _iso(it.installed_at),
            "run_id": it.run_id, "error": it.error, "data": d}


def public_run(run: PatchRun, agent: Optional[Agent] = None) -> dict:
    return {"id": run.id, "agent_id": run.agent_id,
            "agent_uid": agent.agent_uid if agent else None,
            "hostname": agent.hostname if agent else None,
            "policy_id": run.policy_id, "trigger": run.trigger, "created_by": run.created_by,
            "status": run.status, "stage_since": _iso(run.stage_since), "items": run.items or [],
            "snapshot": run.snapshot or {}, "verify": run.verify, "log": run.log or [],
            "error": run.error, "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
            "baseline": bool(run.baseline), "after": bool(run.after)}


def public_policy(p: PatchPolicy) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "enabled": p.enabled,
            "priority": p.priority, "scope_type": p.scope_type, "scope_value": p.scope_value,
            "builtin": p.builtin, "settings": effective_settings(p),
            "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at)}
