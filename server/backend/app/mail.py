# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Mailversand ueber Postfix, komplett aus der Oberflaeche
# ==============================================================================
"""
Mailversand - eingestellt im Browser, nicht in `main.cf`.

Drei Betriebsarten, dieselbe Oberflaeche:

* **container** - der mitgelieferte Postfix (`server/deploy/optional.yml`, Profil
  `postfix`) nimmt die Mails an und stellt sie zu. Was in der Oberflaeche
  eingetragen wird, landet in `data/postfix/settings.env`; der Container liest
  das beim Start. Deshalb wird er nach jeder Aenderung neu gestartet.
* **relay** - ohne eigenen Container direkt an einen vorhandenen Mailserver
  (Provider, Firmen-Relay). Dann redet nur das Backend per SMTP. Fuer die
  gaengigen Anbieter (Gmail, GMX, WEB.DE, ...) gibt es Vorlagen mit festem
  Server/Port, siehe ``mail_providers.py``.
* **m365** - Microsoft 365 ueber Microsoft Graph. Die App-Registrierung legt
  RIMPERIO nach einer Admin-Anmeldung selbst an, siehe ``mail_m365.py``.

Das Passwort wird nie zurueckgegeben, nur ob eines hinterlegt ist.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import hostexec
from .models import Setting

logger = logging.getLogger("rimperio.mail")

SETTING_KEY = "mail_config"

# Wo die Datei fuer den Postfix-Container liegt (relativ zur Installation).
POSTFIX_ENV_RELATIVE = "data/postfix/settings.env"

MODES = {
    "off": {
        "label": "Aus",
        "label_en": "Off",
        "hint": "Der Server verschickt keine Mails.",
    },
    "container": {
        "label": "Postfix-Container auf diesem Server",
        "label_en": "Postfix container on this server",
        "hint": ("RIMPERIO bringt seinen eigenen Mailserver mit. Braucht das Profil "
                 "'postfix' unter RIMPERIO-Server → Zusatzdienste."),
    },
    "relay": {
        "label": "Vorhandener Mailserver (SMTP)",
        "label_en": "Existing mail server (SMTP)",
        "hint": "Direkt an einen Mailserver, den es schon gibt. Kein Zusatzcontainer nötig.",
    },
    "m365": {
        "label": "Microsoft 365 (Graph)",
        "label_en": "Microsoft 365 (Graph)",
        "hint": "Versand über Microsoft Graph mit einer automatisch angelegten App-Registrierung.",
    },
}

SECRET_KEYS = ("smtp_password", "relay_password", "m365_client_secret")

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "off",
    "provider": "",                      # Vorlage aus mail_providers (Anzeige/Assistent)
    "from_address": "",
    "from_name": "RIMPERIO",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "encryption": "starttls",           # none | starttls | tls
    "verify_certificate": True,
    "myhostname": "",
    "relay_host": "",                    # nur fuer den Container: Smarthost
    "relay_port": 587,
    "relay_user": "",
    "relay_password": "",
    "m365_tenant_id": "",
    "m365_client_id": "",
    "m365_client_secret": "",            # wird nie ausgeliefert
    "m365_app_object_id": "",            # von RIMPERIO angelegte App (zum Aufraeumen)
    "m365_secret_expires": "",
    "m365_setup_by": "",
    "m365_setup_at": "",
    "default_recipients": [],
    "notify_on": {
        "update_available": True,
        "backup_failed": True,
        "agent_offline": False,
        "disk_pressure": True,
    },
}

ENCRYPTIONS = {
    "none": "Unverschlüsselt (nur im eigenen Netz vertretbar)",
    "starttls": "STARTTLS (üblich, Port 587)",
    "tls": "TLS von Anfang an (Port 465)",
}


# =============================================================================
# Konfiguration
# =============================================================================
async def load_config(db: AsyncSession) -> dict:
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    config = _deep_default()
    if row and isinstance(row.value, dict):
        for key, value in row.value.items():
            if key in config:
                # Verschachteltes kopieren (notify_on, default_recipients):
                # save_config aendert es an Ort und Stelle. Teilt es sich das
                # Objekt mit der geladenen Zeile, sieht SQLAlchemy keinen
                # Unterschied und speichert die Aenderung nicht.
                if isinstance(value, dict):
                    value = dict(value)
                elif isinstance(value, list):
                    value = list(value)
                config[key] = value
    if config["mode"] not in MODES:
        config["mode"] = "off"
    return config


def _deep_default() -> dict:
    config = dict(DEFAULT_CONFIG)
    config["notify_on"] = dict(DEFAULT_CONFIG["notify_on"])
    config["default_recipients"] = list(DEFAULT_CONFIG["default_recipients"])
    return config


def public_config(config: dict) -> dict:
    """Ohne Passwoerter - so geht es an die Oberflaeche."""
    safe = {k: v for k, v in config.items() if k not in SECRET_KEYS}
    for key in SECRET_KEYS:
        safe[f"{key}_set"] = bool(config.get(key))
    return safe


def merge_config(config: dict, incoming: dict) -> dict:
    """Uebernimmt die Eingaben in eine Kopie der Konfiguration und prueft sie.

    Dient dem Speichern *und* der Pruefung vor dem Speichern - damit beide
    dieselben Regeln haben. Leere Passwortfelder lassen das alte stehen.
    """
    from . import mail_providers

    config = dict(config)
    config["notify_on"] = dict(config.get("notify_on") or {})
    if incoming.get("provider") is not None:
        provider = str(incoming["provider"]).strip()
        if provider and not mail_providers.get(provider):
            raise ValueError(f"Unbekannter Anbieter '{provider}'.")
        config["provider"] = provider
        incoming = mail_providers.apply_preset(provider, incoming)

    mode = (incoming.get("mode") or config["mode"]).strip()
    if mode not in MODES:
        raise ValueError(f"Unbekannte Betriebsart '{mode}'. Erlaubt: {', '.join(MODES)}.")

    for key in ("from_address", "from_name", "smtp_host", "smtp_user",
                "myhostname", "relay_host", "relay_user", "encryption",
                "m365_tenant_id", "m365_client_id", "m365_app_object_id",
                "m365_secret_expires", "m365_setup_by", "m365_setup_at"):
        if incoming.get(key) is not None:
            config[key] = str(incoming[key]).strip()
    for key in ("smtp_port", "relay_port"):
        if incoming.get(key) is not None:
            port = int(incoming[key])
            if not 1 <= port <= 65535:
                raise ValueError(f"'{key}' muss zwischen 1 und 65535 liegen.")
            config[key] = port
    if incoming.get("verify_certificate") is not None:
        config["verify_certificate"] = bool(incoming["verify_certificate"])
    if incoming.get("default_recipients") is not None:
        config["default_recipients"] = [
            address.strip() for address in incoming["default_recipients"]
            if str(address).strip()
        ]
    if incoming.get("notify_on"):
        for key in config["notify_on"]:
            if key in incoming["notify_on"]:
                config["notify_on"][key] = bool(incoming["notify_on"][key])

    # Leeres Feld = unveraendert. Sonst waere das Passwort nach jedem Speichern
    # weg, weil die Oberflaeche es nie zurueckbekommt.
    for key in SECRET_KEYS:
        value = (incoming.get(key) or "").strip()
        if value:
            config[key] = value
        if incoming.get(f"clear_{key}"):
            config[key] = ""

    config["mode"] = mode

    if config["encryption"] not in ENCRYPTIONS:
        raise ValueError(f"Unbekannte Verschlüsselung. Erlaubt: {', '.join(ENCRYPTIONS)}.")

    if mode != "off":
        if not config["from_address"] or "@" not in config["from_address"]:
            raise ValueError("Es fehlt eine gültige Absenderadresse.")
        if mode == "relay" and not config["smtp_host"]:
            raise ValueError("Für einen vorhandenen Mailserver fehlt der Servername.")
        if mode == "m365" and not (config["m365_tenant_id"] and config["m365_client_id"]
                                   and config["m365_client_secret"]):
            raise ValueError("Für Microsoft 365 fehlen Mandant, App-ID oder Client-Geheimnis.")

    if mode == "container":
        # Der Container spricht intern immer unverschluesselt auf 587 - er steht
        # im selben Docker-Netz. Nach aussen verschluesselt Postfix selbst.
        config["smtp_host"] = "rimperio-postfix"
        config["smtp_port"] = 587
        config["encryption"] = "none"
        config["smtp_user"] = ""
        config["smtp_password"] = ""
        config["provider"] = "container"
    elif mode == "m365":
        config["provider"] = "m365"
    elif mode == "relay" and config.get("provider") in ("m365", "container", ""):
        config["provider"] = "smtp"
    return config


async def save_config(db: AsyncSession, incoming: dict) -> dict:
    """Prueft und speichert. Leere Passwortfelder lassen das alte stehen."""
    config = merge_config(await load_config(db), incoming)
    mode = config["mode"]

    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = config
    else:
        db.add(Setting(key=SETTING_KEY, value=config, description="Mailversand"))
    await db.commit()

    written = None
    if mode == "container":
        written = await write_postfix_env(config)

    logger.warning("Mail-Einstellung gespeichert (Betriebsart: %s)", mode)
    result = public_config(config)
    result["postfix_file_written"] = written
    return result


# =============================================================================
# Die Datei fuer den Postfix-Container
# =============================================================================
def render_postfix_env(config: dict) -> str:
    """Baut den Inhalt von data/postfix/settings.env.

    Das ist die ganze Postfix-Konfiguration, die der Betreiber je zu sehen
    bekommt: alles Weitere macht das Image daraus.
    """
    hostname = config.get("myhostname") or "rimperio.local"
    domain = (config.get("from_address") or "@local").split("@", 1)[-1]

    lines = [
        "# ==========================================================================",
        "# Von RIMPERIO erzeugt - NICHT von Hand bearbeiten.",
        "# Einzustellen unter Einstellungen -> E-Mail. Diese Datei wird bei jedem Speichern",
        "# neu geschrieben; Aenderungen hier gehen verloren.",
        f"# Erzeugt: {datetime.utcnow().isoformat()}Z",
        "# ==========================================================================",
        f"POSTFIX_myhostname={hostname}",
        f"ALLOWED_SENDER_DOMAINS={domain}",
        # Der Container darf nur aus dem Docker-Netz annehmen. Ein offenes Relay
        # im Internet ist binnen Stunden als Spamschleuder in Benutzung.
        "MYNETWORKS=127.0.0.0/8,172.16.0.0/12,192.168.0.0/16,10.0.0.0/8",
    ]

    # Smarthost: die meisten Anschluesse duerfen nicht selbst zustellen (Port 25
    # gesperrt, kein PTR-Eintrag). Dann uebergibt Postfix an den Provider.
    if config.get("relay_host"):
        lines.append(f"RELAYHOST={config['relay_host']}:{config.get('relay_port', 587)}")
        if config.get("relay_user"):
            lines.append(f"RELAYHOST_USERNAME={config['relay_user']}")
            lines.append(f"RELAYHOST_PASSWORD={config.get('relay_password', '')}")
        lines.append("RELAYHOST_TLS_LEVEL=encrypt")

    lines.append("")
    return "\n".join(lines)


async def write_postfix_env(config: dict) -> dict:
    """Schreibt die Datei auf den Host, damit der Container sie lesen kann."""
    if not hostexec.nsenter_available():
        return {"written": False,
                "reason": ("Kein Zugriff auf den Host — die Postfix-Datei konnte nicht "
                           "geschrieben werden. Auf der Kommandozeile: den Inhalt aus "
                           "der Vorschau nach data/postfix/settings.env kopieren.")}

    path = f"{hostexec.host_paths()['install_dir']}/{POSTFIX_ENV_RELATIVE}"
    # 0600: da steht gegebenenfalls das Relay-Passwort drin.
    result = await hostexec.write_host_file(path, render_postfix_env(config), mode="0600")
    if not result.ok:
        return {"written": False,
                "reason": (result.error or result.stderr or "unbekannter Fehler").strip()}
    return {"written": True, "path": path,
            "note": "Damit es gilt, muss der Postfix-Container neu gestartet werden."}


# =============================================================================
# Senden
# =============================================================================
def _connect(config: dict) -> smtplib.SMTP:
    """Baut die SMTP-Verbindung nach der eingestellten Betriebsart auf."""
    host = config["smtp_host"]
    port = int(config["smtp_port"])
    encryption = config.get("encryption", "starttls")
    timeout = 30

    context = ssl.create_default_context()
    if not config.get("verify_certificate", True):
        # Bewusste Ausnahme fuer interne Server mit eigenem Zertifikat. Die
        # Oberflaeche schreibt dazu, was man damit aufgibt.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    if encryption == "tls":
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        if encryption == "starttls":
            server.starttls(context=context)

    if config.get("smtp_user"):
        server.login(config["smtp_user"], config.get("smtp_password", ""))
    return server


def send_sync(config: dict, recipients: List[str], subject: str, body: str) -> dict:
    """Verschickt eine Mail. Laeuft blockierend - Aufrufer nutzt to_thread."""
    if config.get("mode") == "off":
        raise ValueError("Der Mailversand ist ausgeschaltet.")
    if not recipients:
        raise ValueError("Es wurde kein Empfänger angegeben.")

    message = EmailMessage()
    message["From"] = formataddr((config.get("from_name") or "RIMPERIO",
                                  config["from_address"]))
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=(config.get("myhostname") or "rimperio.local"))
    # Damit Abwesenheitsnotizen und Weiterleitungsschleifen nicht zurueckkommen.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)

    try:
        server = _connect(config)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        raise RuntimeError(
            f"Der Mailserver {config['smtp_host']}:{config['smtp_port']} war nicht "
            f"erreichbar: {exc}") from exc

    try:
        refused = server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(f"Anmeldung am Mailserver abgelehnt: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Der Mailserver hat die Nachricht abgelehnt: {exc}") from exc
    finally:
        try:
            server.quit()
        except Exception:                                           # defensiv
            pass

    return {
        "sent": True,
        "recipients": recipients,
        "refused": {address: str(reason) for address, reason in (refused or {}).items()},
        "via": f"{config['smtp_host']}:{config['smtp_port']}",
        "at": datetime.utcnow().isoformat(),
    }


async def send(config: dict, recipients: List[str], subject: str, body: str) -> dict:
    """Verschickt ueber den eingestellten Weg, ohne die Ereignisschleife zu blockieren."""
    import asyncio
    if config.get("mode") == "m365":
        if not recipients:
            raise ValueError("Es wurde kein Empfänger angegeben.")
        from . import mail_m365
        return await mail_m365.send(config, recipients, subject, body)
    return await asyncio.to_thread(send_sync, config, recipients, subject, body)


def check_login_sync(config: dict) -> dict:
    """Verbindet sich und meldet sich an - ohne etwas zu verschicken."""
    try:
        server = _connect(config)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(f"Anmeldung am Mailserver abgelehnt: {_smtp_text(exc)}") from exc
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        raise RuntimeError(
            f"Der Mailserver {config['smtp_host']}:{config['smtp_port']} war nicht "
            f"erreichbar: {exc}") from exc
    try:
        server.quit()
    except Exception:                                               # defensiv
        pass
    return {"ok": True,
            "detail": (f"Verbindung zu {config['smtp_host']}:{config['smtp_port']} "
                       "und Anmeldung klappen.")}


def _smtp_text(exc: smtplib.SMTPResponseException) -> str:
    text = exc.smtp_error
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return f"{exc.smtp_code} {text}"


async def verify(config: dict) -> dict:
    """Prueft die Zugangsdaten ueber den eingestellten Weg, ohne zu senden."""
    import asyncio
    mode = config.get("mode")
    if mode == "off":
        raise ValueError("Der Mailversand ist ausgeschaltet.")
    if mode == "m365":
        from . import mail_m365
        return await mail_m365.verify(config)
    if mode == "container":
        return {"ok": True, "detail": "Beim Postfix-Container gibt es keine Anmeldung zu prüfen."}
    return await asyncio.to_thread(check_login_sync, config)


async def send_test(config: dict, recipient: str = "") -> dict:
    """Verschickt eine Probemail."""
    targets = [recipient.strip()] if recipient.strip() else list(config.get("default_recipients") or [])
    if not targets:
        raise ValueError("Es fehlt ein Empfänger für die Probemail.")

    body = (
        "Das ist eine Probemail von RIMPERIO.\n\n"
        f"Betriebsart : {MODES.get(config.get('mode', 'off'), {}).get('label', '?')}\n"
        f"Weg         : {_route(config)}\n"
        f"Absender    : {config.get('from_address')}\n"
        f"Zeitpunkt   : {datetime.utcnow().isoformat()}Z\n\n"
        "Kommt diese Mail an, funktioniert der Versand.\n"
    )
    return await send(config, targets, "RIMPERIO — Probemail", body)


def _route(config: dict) -> str:
    if config.get("mode") == "m365":
        return f"Microsoft Graph (Mandant {config.get('m365_tenant_id')})"
    return f"{config.get('smtp_host')}:{config.get('smtp_port')}"


async def notify(db: AsyncSession, event: str, subject: str, body: str) -> Optional[dict]:
    """Verschickt eine Benachrichtigung, falls fuer dieses Ereignis gewuenscht.

    Gibt None zurueck, wenn nicht verschickt wurde - ein abgeschalteter
    Mailversand ist kein Fehler und darf nichts abbrechen.
    """
    config = await load_config(db)
    if config["mode"] == "off":
        return None
    if not config["notify_on"].get(event, False):
        return None
    recipients = config.get("default_recipients") or []
    if not recipients:
        return None

    try:
        return await send(config, recipients, subject, body)
    except Exception as exc:                                        # defensiv
        logger.warning("Benachrichtigung '%s' nicht verschickt: %s", event, exc)
        return None
