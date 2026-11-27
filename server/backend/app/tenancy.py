# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.26.22.00.00
# Beschreibung: RIMPERIO - Mandantenfaehigkeit (Kunden in einer Instanz trennen)
# ==============================================================================
"""Mandanten: eine RIMPERIO-Instanz, mehrere Kunden - sauber getrennt.

Grundidee
---------
Ein Mandant (``tenants``) ist ein Kunde. Was ihm gehoert, traegt seine
``tenant_id``: Benutzer, Geraete, Zugangsschluessel, Proxmox-Hosts,
SNMP-Geraete und -Zugangsdaten, Meldungen. Alles, was an einem Geraet haengt
(Logs, Messwerte, Befehle, Alarme, Updates, Patch-Laeufe ...), gehoert ueber
das Geraet automatisch mit dazu.

Skripte, Checks, Patch-Richtlinien und SNMP-Vorlagen koennen einem Mandanten
gehoeren - oder ohne Mandant fuer alle gelten (die Bibliothek des Betreibers).

Wer sieht was?
--------------
* **Betreiber** (Benutzer ohne Mandant) sehen alles. In der Oberflaeche koennen
  sie auf einen Mandanten umschalten (Kopf ``X-Rimperio-Tenant``) - dann sehen
  sie genau das, was dieser Kunde sieht, und was sie neu anlegen, gehoert ihm.
* **Mandanten-Benutzer** sehen nur ihren Mandanten. Das ist nicht abschaltbar
  und gilt auch, wenn die Mandantenfaehigkeit spaeter ausgeschaltet wird.
  Systemfunktionen (Konsole auf dem Server, Updates, Sicherung, Container,
  Netzwerk, Datenbank ...) erreichen sie nie - es gilt eine Positivliste
  (``TENANT_PATHS``), alles andere ist zu.

Wie wird getrennt?
------------------
Nicht in jeder Abfrage von Hand, sondern einmal zentral in der
Datenbankschicht (``do_orm_execute``): Jede ORM-Abfrage bekommt fuer die
betroffenen Tabellen eine Zusatzbedingung. Beim Speichern (``before_flush``)
bekommen neue Zeilen den Mandanten, und eine Zeile eines anderen Mandanten
(oder eine gemeinsame des Betreibers) laesst sich nicht aendern oder loeschen.
Welcher Mandant gilt, steht in einer ContextVar - gesetzt bei der Anmeldung
eines Benutzers (``auth.get_current_user``) bzw. eines Agents (Schluessel).

Hintergrundaufgaben (Checks, Patch-Laeufe, Alarme) laufen ohne Mandant und
sehen damit alles - sie arbeiten ohnehin je Geraet.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable, Optional

from sqlalchemy import event, inspect as sa_inspect, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

logger = logging.getLogger("rimperio.tenancy")

# Kopf, mit dem ein Betreiber auf einen Mandanten umschaltet. "0" = nur, was
# keinem Mandanten gehoert. Leer/"all" = alles.
HEADER = "X-Rimperio-Tenant"
SETTING_KEY = "tenancy"
UNASSIGNED = 0
# execution_options(**ALL_TENANTS): diese eine Abfrage ohne Mandanten-Filter
# (z. B. "ist der Benutzername schon vergeben?" - das gilt instanzweit).
ALL_TENANTS = {"rimperio_all_tenants": True}

# Mandant fuer diese Anfrage: None = keine Einschraenkung, 0 = "ohne Mandant",
# >0 = genau dieser Mandant.
_scope: ContextVar[Optional[int]] = ContextVar("rimperio_tenant_scope", default=None)
# True, wenn der Mandant erzwungen ist (Mandanten-Benutzer, Agent eines
# Mandanten) - dann darf auch nichts "fuer alle" angelegt werden.
_forced: ContextVar[bool] = ContextVar("rimperio_tenant_forced", default=False)

_enabled: Optional[bool] = None


class TenantViolation(PermissionError):
    """Zugriff auf etwas, das einem anderen Mandanten gehoert."""


# ------------------------------------------------------------------------------
# Ein / aus
# ------------------------------------------------------------------------------

def env_default() -> bool:
    return (os.environ.get("RIMPERIO_MULTITENANT", "") or "").strip().lower() in ("1", "true", "yes", "ja", "on")


def enabled() -> bool:
    """Ist die Mandantenfaehigkeit eingeschaltet? (Zwischengespeichert.)"""
    return bool(_enabled) if _enabled is not None else env_default()


def _set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


async def load_state(db) -> bool:
    """Liest den Schalter aus der Datenbank (beim Start und nach Aenderung).

    RIMPERIO_MULTITENANT=true in der .env (Installer: --multi-tenant) schaltet
    die Mandantenfaehigkeit ein - beim ersten Start, und auch spaeter, wenn der
    Wert erst bei einem Update dazukommt. Danach gilt der Schalter in der
    Oberflaeche: wer dort ausschaltet, wird beim naechsten Neustart nicht
    wieder eingeschaltet (``env_seen`` merkt sich, dass der Wert schon galt).
    """
    from .models import Setting
    env = env_default()
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row is None:
        value = env
        db.add(Setting(key=SETTING_KEY, value={"enabled": value, "env_seen": env},
                       description="Mandantenfähigkeit (Benutzer → Mandanten)"))
        await db.commit()
    else:
        data = row.value if isinstance(row.value, dict) else {"enabled": bool(row.value)}
        value = bool(data.get("enabled"))
        if env and not data.get("env_seen"):
            value = True
            logger.info("Mandantenfähigkeit per RIMPERIO_MULTITENANT eingeschaltet")
        if bool(data.get("env_seen")) != env or value != bool(data.get("enabled")):
            row.value = {**data, "enabled": value, "env_seen": env}
            await db.commit()
    _set_enabled(value)
    return value


async def store_state(db, value: bool) -> None:
    from .models import Setting
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row is None:
        db.add(Setting(key=SETTING_KEY, value={"enabled": bool(value)},
                       description="Mandantenfähigkeit (Benutzer → Mandanten)"))
    else:
        row.value = {**(row.value if isinstance(row.value, dict) else {}), "enabled": bool(value)}
    await db.commit()
    _set_enabled(value)


# ------------------------------------------------------------------------------
# Wer gilt gerade?
# ------------------------------------------------------------------------------

def current() -> Optional[int]:
    return _scope.get()


def forced() -> bool:
    return _forced.get()


def set_scope(tenant_id: Optional[int], *, force: bool = False) -> None:
    """Setzt den Mandanten fuer den Rest dieser Anfrage/Aufgabe."""
    _scope.set(tenant_id)
    _forced.set(bool(force) and tenant_id is not None)


@contextmanager
def scoped(tenant_id: Optional[int], *, force: bool = False):
    t1 = _scope.set(tenant_id)
    t2 = _forced.set(bool(force) and tenant_id is not None)
    try:
        yield
    finally:
        _scope.reset(t1)
        _forced.reset(t2)


@contextmanager
def unscoped():
    """Ohne Einschraenkung (nur fuer interne Nachschlagewege wie Schluessel)."""
    with scoped(None):
        yield


def scope_for_user(user, header_value: Optional[str] = None) -> Optional[int]:
    """Welcher Mandant gilt fuer diesen Benutzer in dieser Anfrage?"""
    tid = getattr(user, "tenant_id", None)
    if tid is not None:
        return int(tid)
    if not enabled():
        return None
    raw = (header_value or "").strip().lower()
    if not raw or raw in ("all", "*", "alle"):
        return None
    if raw in ("0", "none", "ohne", "provider", "betreiber"):
        return UNASSIGNED
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# ------------------------------------------------------------------------------
# Was darf ein Mandanten-Benutzer ueberhaupt aufrufen?
# ------------------------------------------------------------------------------
# Positivliste: alles, was hier nicht steht, ist fuer Mandanten-Benutzer zu.
# Bewusst eng - was einem Kunden fehlt, laesst sich nachtragen; was ihm
# versehentlich offensteht (eine Root-Konsole auf dem Server), nicht.
TENANT_PATHS = [
    r"/api/auth(/.*)?",
    r"/api/users(/.*)?",
    r"/api/tenants(/.*)?",
    r"/api/agents(/.*)?",
    r"/api/agent-tokens(/.*)?",
    r"/api/logs(/.*)?",
    r"/api/rmm(/.*)?",
    r"/api/patching(/.*)?",
    r"/api/notifications(/.*)?",
    r"/api/snmp(/.*)?",
    r"/api/pve(/.*)?",
    r"/api/db-updates(/.*)?",
    r"/api/app(/.*)?",
    r"/api/branding(/.*)?",
    r"/api/about",                          # Herkunft, Lizenz (statisch)
    r"/api/log-handling/explain/\d+",       # Klartext zu einer (eigenen) Logzeile
    r"/api/nas(/.*)?",                       # eigene NAS (anlegen: Betreiber)
    r"/api/backups(/.*)?",                   # eigene Sicherungen (Ziele/Auftraege: Betreiber)
]
# Ausnahmen innerhalb der erlaubten Bereiche: Einstellungen, die fuer die
# ganze Instanz gelten, und Wege, die den Server selbst betreffen.
TENANT_DENY = [
    r"/api/rmm/settings/.*",                 # Schwellen gelten fuer alle
    r"/api/patching/sources/.+",             # Update-Quellen der Instanz (Lesen: /advisories)
    r"/api/app/server(/.*)?",                # Server-Update aus der App
    r"/api/agent-tokens/harden",
    r"/api/agents/installer/.*",
    r"/api/snmp/discovered",                 # Netzwerksuche des Betreibers
    r"/api/snmp/probe",                      # fragt vom Server aus ab
    r"/api/nas/(discovered|probe|connect)",  # Netzwerksuche / Verbindung vom Server aus
    r"/api/nas/\d+/snmp",
    r"/api/backups/runs/\d+/(download|to-restore)",
]
# Nur lesen: Verbindungen, die der Server selbst aufbaut (Proxmox), legt der
# Betreiber an und ordnet sie dem Mandanten zu. Der Mandant nutzt sie.
_READ_ONLY_PATHS = [
    r"/api/patching/sources",
    r"/api/patching/pve/hosts",
    r"/api/patching/pve/hosts/connect",
    r"/api/patching/pve/hosts/\d+",
    r"/api/nas/\d+",
    r"/api/backups/targets",
    r"/api/backups/targets/\d+",
    r"/api/backups/jobs",
    r"/api/backups/jobs/\d+",
    r"/api/backups/runs/\d+",
]
_ALLOW = [re.compile(p + r"$") for p in TENANT_PATHS]
_DENY = [re.compile(p + r"$") for p in TENANT_DENY]
# Nur lesen: die Liste der Quellen darf ein Mandant sehen, aber nicht aendern.
_READ_ONLY = [re.compile(p + r"$") for p in _READ_ONLY_PATHS]


def tenant_path_allowed(path: str, method: str = "GET") -> bool:
    path = path.rstrip("/") or "/"
    if any(p.match(path) for p in _READ_ONLY):
        return method.upper() in ("GET", "HEAD")
    if any(p.match(path) for p in _DENY):
        return False
    return any(p.match(path) for p in _ALLOW)


# Ticket-Zwecke (auth.create_ticket), die ein Mandanten-Benutzer bekommen darf.
TENANT_TICKETS = ("console",)


# ------------------------------------------------------------------------------
# Die Trennung in der Datenbankschicht
# ------------------------------------------------------------------------------
_strict: list = []          # gehoert genau einem Mandanten (oder dem Betreiber)
_shared: list = []          # ohne Mandant = gilt fuer alle
_via_agent: list = []       # haengt an agents.id
_via_snmp: list = []        # haengt an snmp_devices.id
_via_user: list = []        # haengt an users.id
_via_parent: list = []      # (Modell, Spalte, Eltern-Modell mit tenant_id)
_owned: tuple = ()
_installed = False


def _criteria(tid: int, write: bool) -> list:
    """Zusatzbedingungen je Tabelle - als fertige Ausdruecke, nicht als Lambda:
    so werden die Werte ganz normal als Parameter gebunden."""
    from .models import Agent, SnmpDevice, User

    def own(col):
        return col.is_(None) if tid == UNASSIGNED else col == tid

    agents = select(Agent.id).where(own(Agent.tenant_id))
    devices = select(SnmpDevice.id).where(own(SnmpDevice.tenant_id))
    users = select(User.id).where(own(User.tenant_id))

    opts = []
    for m in _strict:
        opts.append(with_loader_criteria(m, own(m.tenant_id), include_aliases=True))
    for m in _shared:
        if write or tid == UNASSIGNED:
            # Aendern/Loeschen in Massen: nie die gemeinsamen Zeilen mitnehmen.
            crit = own(m.tenant_id)
        else:
            crit = or_(m.tenant_id == tid, m.tenant_id.is_(None))
        opts.append(with_loader_criteria(m, crit, include_aliases=True))
    for m in _via_agent:
        crit = m.agent_id.in_(agents)
        if tid == UNASSIGNED:
            crit = or_(m.agent_id.is_(None), crit)
        opts.append(with_loader_criteria(m, crit, include_aliases=True))
    for m in _via_snmp:
        opts.append(with_loader_criteria(m, m.device_id.in_(devices), include_aliases=True))
    for m in _via_user:
        opts.append(with_loader_criteria(m, m.user_id.in_(users), include_aliases=True))
    for m, col, parent in _via_parent:
        opts.append(with_loader_criteria(
            m, getattr(m, col).in_(select(parent.id).where(own(parent.tenant_id))), include_aliases=True))
    return opts


def _on_execute(state) -> None:
    tid = _scope.get()
    if tid is None:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("rimperio_all_tenants"):
        return
    write = bool(state.is_update or state.is_delete)
    state.statement = state.statement.options(*_criteria(tid, write))


def _original(obj, attr: str = "tenant_id"):
    hist = sa_inspect(obj).attrs[attr].history
    if hist.deleted:
        return hist.deleted[0]
    if hist.unchanged:
        return hist.unchanged[0]
    return getattr(obj, attr, None)


def _matches(value, tid: int) -> bool:
    if tid == UNASSIGNED:
        return value is None
    return value == tid


def _before_flush(session, flush_context, instances) -> None:
    tid = _scope.get()
    if tid is None:
        return
    shared = tuple(_shared)
    for obj in list(session.new):
        if not isinstance(obj, _owned):
            continue
        value = getattr(obj, "tenant_id", None)
        if value is None:
            if tid != UNASSIGNED:
                obj.tenant_id = tid
            continue
        # Ausdruecklich gesetzt: nur der eigene Mandant - es sei denn, ein
        # Betreiber ohne erzwungenen Mandanten legt fuer einen Kunden an.
        if _forced.get() and not _matches(value, tid):
            raise TenantViolation("Dieser Eintrag gehört zu einem anderen Mandanten.")
    if not _forced.get():
        # Betreiber mit umgeschalteter Ansicht: darf alles, was er sieht.
        return
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, _owned):
            continue
        if obj in session.dirty and not session.is_modified(obj, include_collections=False):
            continue
        orig = _original(obj)
        if not _matches(orig, tid):
            if orig is None and isinstance(obj, shared):
                raise TenantViolation("Das gilt für alle Mandanten und kann nur vom Betreiber geändert werden.")
            raise TenantViolation("Dieser Eintrag gehört zu einem anderen Mandanten.")
        if obj not in session.deleted and not _matches(getattr(obj, "tenant_id", None), tid):
            raise TenantViolation("Einträge lassen sich nicht in einen anderen Mandanten verschieben.")


def install() -> None:
    """Haengt die Trennung in die Datenbankschicht ein (einmal beim Import)."""
    global _installed, _owned
    if _installed:
        return
    from . import models as m

    _strict[:] = [m.User, m.Agent, m.AgentToken, m.PveHost, m.Notification,
                  m.SnmpDevice, m.SnmpCredential]
    _shared[:] = [m.Script, m.Check, m.PatchPolicy, m.SnmpTemplate]
    _via_agent[:] = [m.Log, m.Telemetry, m.Command, m.RmmAlert, m.CheckResult, m.PatchReport,
                     m.PatchItem, m.PatchRun, m.PveLink, m.DbMigration]
    _via_snmp[:] = [m.SnmpMetric, m.SnmpSample]
    _via_user[:] = [m.AppDevice, m.AppLoginToken, m.WebAuthnCredential, m.MFABackupCode]
    # NAS und Sicherungen (seit 0.0.1): Geraet, Ziel und Auftrag gehoeren einem
    # Mandanten; Vorgaenge und Laeufe haengen daran.
    _strict.extend([m.NasDevice, m.BackupTarget, m.BackupJob])
    _via_parent[:] = [(m.NasJob, "nas_id", m.NasDevice), (m.BackupRun, "job_id", m.BackupJob)]
    _owned = tuple(_strict + _shared)
    event.listen(Session, "do_orm_execute", _on_execute)
    event.listen(Session, "before_flush", _before_flush)
    _installed = True


# ------------------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------------------
OWNED_TABLES = ("users", "agents", "agent_tokens", "scripts", "checks", "patch_policies",
                "pve_hosts", "notifications", "snmp_credentials", "snmp_templates", "snmp_devices",
                "nas_devices", "backup_targets", "backup_jobs")


async def ensure_schema(engine) -> None:
    """Legt `tenants` an und gibt den Tabellen ihre Spalte `tenant_id`. Wirft nie.

    Laeuft als ERSTER Start-Schritt: die Modelle kennen die Spalte schon, jede
    Abfrage vorher wuerde an der fehlenden Spalte scheitern. Tabellen, die es
    noch nicht gibt, legen ihre eigenen Start-Schritte spaeter samt Spalte an.
    """
    from .database import Base
    from .models import TENANT_TABLES
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=TENANT_TABLES))
            for table in OWNED_TABLES:
                await conn.exec_driver_sql(
                    f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS tenant_id INTEGER "
                    f"REFERENCES tenants(id) ON DELETE SET NULL")
                await conn.exec_driver_sql(
                    f"DO $$ BEGIN IF to_regclass('{table}') IS NOT NULL THEN "
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id); "
                    f"END IF; END $$")
            # Hostname + IP war instanzweit eindeutig. Zwei Kunden mit
            # "server01" auf 192.168.1.10 sind aber zwei Geraete - der Mandant
            # gehoert mit in den Schluessel.
            await conn.exec_driver_sql(
                "DO $$ BEGIN IF to_regclass('agents') IS NOT NULL THEN "
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_hostname_ip_tenant "
                "ON agents (hostname, ip_address, COALESCE(tenant_id, 0)); "
                "DROP INDEX IF EXISTS idx_agents_hostname_ip; "
                "END IF; END $$")
        logger.info("Mandanten-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("Mandanten-Schema nicht angelegt: %s", exc)


# ------------------------------------------------------------------------------
# Hilfen fuer Routen
# ------------------------------------------------------------------------------

def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        value = value.replace(a, b)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:64] or "mandant"


def public(t, counts: Optional[dict] = None) -> dict:
    out = {
        "id": t.id, "slug": t.slug, "name": t.name, "is_active": bool(t.is_active),
        "contact_name": t.contact_name or "", "contact_email": t.contact_email or "",
        "note": t.note or "", "alert_emails": t.alert_emails or "",
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if counts is not None:
        out["counts"] = counts
    return out


def alert_recipients(tenant) -> list:
    if tenant is None:
        return []
    return [a.strip() for a in (tenant.alert_emails or "").replace(";", ",").split(",") if a.strip()]


def ids(values: Iterable) -> list:
    out = []
    for v in values or []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


install()


async def log_total(db) -> int:
    """Anzahl Logs fuer Uebersichten.

    Ohne Mandant: die Schaetzung aus pg_class (sofort, auch bei Millionen
    Zeilen). Mit Mandant waere diese Zahl die der ganzen Instanz - also
    zaehlen, was dieser Mandant wirklich sieht.
    """
    from sqlalchemy import func, text
    from .models import Log
    if current() is None:
        return (await db.execute(text(
            "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
        ))).scalar() or 0
    return (await db.execute(select(func.count(Log.id)))).scalar() or 0
