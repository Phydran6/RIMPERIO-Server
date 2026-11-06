# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Zugangsschluessel fuer Agents (pro Geraet, gehasht)
# ==============================================================================
"""
Schluesselverwaltung fuer alles, was ohne Benutzeranmeldung an die API darf.

**Was sich geaendert hat und warum.** Frueher gab es genau einen Schluessel
(`global-agent`), er stand im Klartext in der Datenbank, jeder angemeldete
Benutzer konnte ihn abrufen, und er lag danach auf jedem Rechner im Netz. Wer
einen davon aufmachte, hatte den Schluessel fuer alle.

Jetzt gilt:

* **Jedes Geraet bekommt seinen eigenen Schluessel.** Faellt ein Rechner in
  falsche Haende, entwertet man genau diesen einen - die anderen laufen weiter.
* **Kein Schluessel liegt im Klartext.** Gespeichert wird nur ein SHA-256-
  Abdruck. Angezeigt wird der Schluessel genau einmal, direkt nach dem
  Erzeugen. Danach kann ihn niemand mehr auslesen - auch kein Admin, auch
  nicht ueber einen Datenbankabzug.
* **Der Generalschluessel bleibt**, aber streng bewacht: er wird nur beim
  Erzeugen gezeigt, kann auf Absenderbereiche eingegrenzt und mit einem
  Ablaufdatum versehen werden, jede Benutzung wird vermerkt, und jeder
  Fehlversuch landet im Systemtagebuch.
* **Einladungen** (`enroll`) schliessen die Luecke beim Installieren: ein
  kurzlebiger, zaehlbarer Schluessel, der *nur* einen Geraete-Schluessel
  anfordern darf. Damit muss der Generalschluessel nicht mehr auf jeden
  Rechner kopiert werden.

Warum SHA-256 und nicht bcrypt: Diese Schluessel sind 256 Bit Zufall, kein
Passwort. Es gibt nichts zu raten, also braucht es keine kuenstliche Bremse -
und die Bremse waere hier teuer, weil jede einzelne gelieferte Logzeile den
Schluessel pruefen muss.

Abwaertskompatibilitaet: Schluessel aus der Zeit vor dieser Fassung liegen noch
im Klartext in der Spalte `token`. Sie funktionieren unveraendert weiter (beim
Start wird ihr Abdruck nachgetragen), sind in der Oberflaeche aber als
"ungeschuetzt" gekennzeichnet, mit einem Knopf zum Ueberfuehren.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentToken

logger = logging.getLogger("rimperio.tokens")

# Erkennungszeichen am Anfang. Damit sieht man einem gefundenen Schluessel an,
# wohin er gehoert - und Suchen nach versehentlich veroeffentlichten
# Schluesseln (GitHub, Pastebin) funktionieren ueberhaupt erst.
PREFIXES = {
    "global": "lbg",     # RIMPERIO Global
    "agent": "lba",      # RIMPERIO Agent
    "enroll": "lbe",     # RIMPERIO Enrollment
}

KINDS = tuple(PREFIXES)

# Der Generalschluessel heisst so. Der Name stammt aus der Vorgaengerfassung
# und bleibt, damit bestehende Installationen ihn wiedererkennen.
GLOBAL_TOKEN_NAME = "global-agent"

# Wie lange eine Einladung gilt und wie oft sie eingeloest werden darf.
DEFAULT_ENROLL_HOURS = 24
DEFAULT_ENROLL_USES = 1


# =============================================================================
# Erzeugen und Pruefen
# =============================================================================
def digest(value: str) -> str:
    """Der Abdruck eines Schluessels - das Einzige, was gespeichert wird."""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


# Kurzname fuer den internen Gebrauch.
_digest = digest


def mint(kind: str) -> Tuple[str, str, str]:
    """Wuerfelt einen neuen Schluessel. Gibt (Klartext, Abdruck, Anzeigeteil)."""
    if kind not in PREFIXES:
        raise ValueError(f"Unbekannte Schluesselart '{kind}'.")
    secret = secrets.token_hex(32)                       # 256 Bit
    plain = f"{PREFIXES[kind]}_{secret}"
    return plain, _digest(plain), f"{PREFIXES[kind]}_{secret[:6]}"


def ip_allowed(token: AgentToken, source_ip: str) -> bool:
    """Darf dieser Absender diesen Schluessel benutzen?

    Ohne Eintrag: ja. Mit Eintrag: nur aus den genannten Netzen. Eine
    unlesbare Absender-IP gilt als nicht erlaubt, sobald eine Einschraenkung
    gesetzt ist - im Zweifel zu.
    """
    raw = (token.allowed_cidrs or "").strip()
    if not raw:
        return True
    try:
        address = ipaddress.ip_address((source_ip or "").strip())
    except ValueError:
        return False
    for entry in raw.replace(";", ",").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            logger.warning("Unlesbarer Netzbereich in Schluessel %s: %r", token.id, entry)
    return False


def usable(token: AgentToken) -> Tuple[bool, str]:
    """Ist der Schluessel gerade gueltig? Gibt (ja/nein, Grund)."""
    if not token.is_active:
        return False, "Der Schlüssel ist abgeschaltet."
    if token.revoked_at:
        return False, "Der Schlüssel wurde zurückgezogen."
    if token.expires_at and token.expires_at <= datetime.utcnow():
        return False, "Der Schlüssel ist abgelaufen."
    if token.max_uses is not None and (token.use_count or 0) >= token.max_uses:
        return False, "Die Einladung ist aufgebraucht."
    return True, ""


async def resolve(db: AsyncSession, presented: str,
                  source_ip: str = "") -> Tuple[Optional[AgentToken], str]:
    """Findet den Schluessel zu einem vorgelegten Wert.

    Gibt (Schluessel, "") zurueck - oder (None, Grund). Der Grund geht bewusst
    *nicht* an den Aufrufer der API zurueck: von aussen soll nicht
    unterscheidbar sein, ob ein Schluessel unbekannt, abgelaufen oder nur aus
    dem falschen Netz vorgelegt wurde. Er steht im Systemtagebuch.
    """
    presented = (presented or "").strip()
    if not presented:
        return None, "Kein Schlüssel vorgelegt."

    digest = _digest(presented)
    row = (await db.execute(
        select(AgentToken).where(AgentToken.token_hash == digest)
    )).scalar_one_or_none()

    # Altbestand ohne nachgetragenen Abdruck: ueber den Klartext finden und den
    # Abdruck bei dieser Gelegenheit ergaenzen.
    if row is None:
        row = (await db.execute(
            select(AgentToken).where(AgentToken.token == presented)
        )).scalar_one_or_none()
        if row is not None and not row.token_hash:
            row.token_hash = digest
            row.prefix = row.prefix or presented[:10]

    if row is None:
        return None, "Unbekannter Schlüssel."

    ok, reason = usable(row)
    if not ok:
        return None, reason
    if not ip_allowed(row, source_ip):
        return None, f"Absender {source_ip or 'unbekannt'} ist für diesen Schlüssel nicht zugelassen."
    return row, ""


def note_use(token: AgentToken, source_ip: str = "") -> None:
    """Haelt fest, dass der Schluessel benutzt wurde. Der Aufrufer committet.

    Fuer Vorgaenge, die selten sind und gezaehlt werden sollen: Anmeldung,
    Abmeldung. Fuer das Liefern von Logzeilen gibt es `touch` - dort waere ein
    Zaehler pro Anfrage eine Schreiboperation zu viel.
    """
    token.last_used_at = datetime.utcnow()
    if source_ip:
        token.last_used_ip = source_ip[:45]
    token.use_count = (token.use_count or 0) + 1


# Wie oft der Zeitstempel beim Liefern hoechstens nachgezogen wird.
TOUCH_INTERVAL = timedelta(seconds=60)


def touch(token: AgentToken, source_ip: str = "") -> bool:
    """Haelt 'zuletzt benutzt' nach - aber sparsam.

    Ein Log-Server nimmt im Zweifel hunderte Lieferungen pro Minute entgegen.
    Bei jeder davon eine Zeile in `agent_tokens` zu schreiben, waere eine
    Schreiboperation, die niemandem nuetzt: fuer die Frage "wird dieser
    Schluessel noch benutzt?" reicht Minutengenauigkeit.

    Gibt zurueck, ob sich etwas geaendert hat.
    """
    now = datetime.utcnow()
    if token.last_used_at and now - token.last_used_at < TOUCH_INTERVAL:
        return False
    token.last_used_at = now
    if source_ip:
        token.last_used_ip = source_ip[:45]
    return True


# =============================================================================
# Was darf welcher Schluessel?
# =============================================================================
def may_ingest(token: AgentToken, hostname: str = "",
               agent_id: Optional[int] = None) -> Tuple[bool, str]:
    """Darf dieser Schluessel Logzeilen liefern - und fuer wen?

    Der Generalschluessel darf fuer jedes Geraet liefern (dafuer ist er da:
    Sammler wie n8n liefern fuer die FRITZ!Box). Ein Geraete-Schluessel darf
    nur fuer sein eigenes Geraet liefern - sonst koennte ein uebernommener
    Rechner Logzeilen im Namen des Domaenencontrollers erfinden.
    """
    kind = (token.kind or "agent").lower()
    if kind == "enroll":
        return False, ("Dieser Schlüssel ist eine Einladung. Er darf ausschließlich "
                       "einen Geräteschlüssel anfordern, keine Logs liefern.")
    if kind == "global":
        return True, ""
    # Noch nicht gebunden: die erste Lieferung bindet ihn (siehe bind_to_agent).
    if token.agent_id is None:
        return True, ""
    if agent_id is not None and token.agent_id == agent_id:
        return True, ""
    return False, ("Dieser Geräteschlüssel gehört zu einem anderen Gerät. Für ein "
                   "weiteres Gerät gehört ein eigener Schlüssel dazu.")


def may_enroll(token: AgentToken) -> bool:
    """Darf dieser Schluessel einen Geraeteschluessel anfordern?"""
    return (token.kind or "agent").lower() in ("global", "enroll")


def may_decommission(token: AgentToken, agent_id: Optional[int]) -> Tuple[bool, str]:
    """Darf dieser Schluessel dieses Geraet abmelden?"""
    kind = (token.kind or "agent").lower()
    if kind == "global":
        return True, ""
    if kind == "enroll":
        return False, "Eine Einladung darf keine Geräte abmelden."
    if token.agent_id is None or (agent_id is not None and token.agent_id == agent_id):
        return True, ""
    return False, "Dieser Geräteschlüssel gehört zu einem anderen Gerät."


def bind_to_agent(token: AgentToken, agent_id: int) -> bool:
    """Bindet einen noch freien Geraeteschluessel an sein Geraet.

    Einmalig: danach kann derselbe Schluessel nicht mehr fuer ein zweites
    Geraet benutzt werden. Gibt zurueck, ob sich etwas geaendert hat.
    """
    if (token.kind or "agent").lower() != "agent":
        return False
    if token.agent_id is not None:
        return False
    token.agent_id = agent_id
    return True


# =============================================================================
# Anlegen
# =============================================================================
async def create(db: AsyncSession, *, name: str, kind: str = "agent",
                 device_type: Optional[str] = None, agent_id: Optional[int] = None,
                 allowed_cidrs: str = "", expires_in_hours: Optional[int] = None,
                 max_uses: Optional[int] = None, created_by: str = "",
                 note: str = "") -> Tuple[AgentToken, str]:
    """Legt einen Schluessel an. Gibt (Zeile, Klartext) - der Klartext einmalig."""
    if kind not in KINDS:
        raise ValueError(f"Unbekannte Schluesselart '{kind}'. Erlaubt: {', '.join(KINDS)}.")
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Schlüssel braucht einen Namen.")

    plain, digest, prefix = mint(kind)
    row = AgentToken(
        name=name[:100],
        token=None,                       # Klartext wird bewusst nicht gespeichert
        token_hash=digest,
        prefix=prefix,
        kind=kind,
        device_type=device_type,
        agent_id=agent_id,
        allowed_cidrs=(allowed_cidrs or "").strip() or None,
        max_uses=max_uses,
        use_count=0,
        expires_at=(datetime.utcnow() + timedelta(hours=expires_in_hours)
                    if expires_in_hours else None),
        created_by=(created_by or "")[:50] or None,
        note=(note or "").strip() or None,
        is_active=True,
    )
    db.add(row)
    await db.flush()
    return row, plain


async def ensure_global(db: AsyncSession, created_by: str = "system") -> Tuple[AgentToken, str]:
    """Sorgt dafuer, dass es einen Generalschluessel gibt.

    Gibt (Zeile, Klartext). Der Klartext ist leer, wenn der Schluessel schon
    vorher da war - er laesst sich dann nicht mehr auslesen, nur erneuern.
    """
    row = (await db.execute(
        select(AgentToken).where(AgentToken.kind == "global")
    )).scalar_one_or_none()
    if row is None:
        # Altbestand: der Schluessel hiess frueher 'global-agent' und hatte noch
        # keine Art. Den uebernehmen statt einen zweiten anzulegen.
        row = (await db.execute(
            select(AgentToken).where(AgentToken.name == GLOBAL_TOKEN_NAME)
        )).scalar_one_or_none()
        if row is not None:
            row.kind = "global"
            return row, ""

    if row is not None:
        return row, ""

    row, plain = await create(db, name=GLOBAL_TOKEN_NAME, kind="global",
                              created_by=created_by,
                              note="Generalschlüssel — darf anmelden, liefern und abmelden.")
    return row, plain


async def rotate(db: AsyncSession, row: AgentToken, created_by: str = "") -> str:
    """Wuerfelt den Schluessel neu. Der alte gilt ab sofort nicht mehr."""
    plain, digest, prefix = mint((row.kind or "agent").lower() if row.kind in KINDS else "agent")
    row.token = None                       # ein evtl. alter Klartext verschwindet hier
    row.token_hash = digest
    row.prefix = prefix
    row.revoked_at = None
    row.is_active = True
    row.use_count = 0
    row.updated_at = datetime.utcnow()
    if created_by:
        row.created_by = created_by[:50]
    return plain


def revoke(row: AgentToken, reason: str = "") -> None:
    """Zieht einen Schluessel zurueck. Die Zeile bleibt - fuer die Nachvollziehbarkeit."""
    row.revoked_at = datetime.utcnow()
    row.is_active = False
    if reason:
        row.note = ((row.note or "") + f"\nZurückgezogen: {reason}").strip()[:2000]


# =============================================================================
# Darstellung
# =============================================================================
def public(row: AgentToken, agent_hostname: str = "") -> dict:
    """Die Sicht fuer die Oberflaeche - ohne den Schluessel selbst."""
    ok, reason = usable(row)
    legacy = bool(row.token)
    return {
        "id": row.id,
        "name": row.name,
        "kind": (row.kind or "agent").lower(),
        "prefix": row.prefix or (row.token[:10] + "…" if row.token else ""),
        "device_type": row.device_type,
        "agent_id": row.agent_id,
        "agent_hostname": agent_hostname or None,
        "is_active": bool(row.is_active),
        "usable": ok,
        "state_reason": reason,
        "revoked_at": row.revoked_at,
        "expires_at": row.expires_at,
        "max_uses": row.max_uses,
        "use_count": row.use_count or 0,
        "allowed_cidrs": row.allowed_cidrs or "",
        "last_used_at": row.last_used_at,
        "last_used_ip": row.last_used_ip,
        "created_at": row.created_at,
        "created_by": row.created_by,
        "note": row.note or "",
        # Der wichtigste Hinweis in dieser Liste: liegt der Schluessel noch im
        # Klartext in der Datenbank?
        "legacy_plaintext": legacy,
        "warning": ("Dieser Schlüssel stammt aus einer früheren Fassung und liegt noch "
                    "im Klartext in der Datenbank. Erneuern oder überführen."
                    if legacy else ""),
    }


async def list_all(db: AsyncSession) -> List[dict]:
    """Alle Schluessel, Generalschluessel zuerst."""
    from .models import Agent

    rows = (await db.execute(
        select(AgentToken).order_by(AgentToken.kind.asc(), AgentToken.created_at.desc())
    )).scalars().all()

    hostnames = {}
    agent_ids = [r.agent_id for r in rows if r.agent_id]
    if agent_ids:
        pairs = (await db.execute(
            select(Agent.id, Agent.hostname).where(Agent.id.in_(agent_ids))
        )).all()
        hostnames = {row_id: host for row_id, host in pairs}

    order = {"global": 0, "enroll": 1, "agent": 2}
    items = [public(row, hostnames.get(row.agent_id, "")) for row in rows]
    items.sort(key=lambda item: (order.get(item["kind"], 9), item["name"].lower()))
    return items
