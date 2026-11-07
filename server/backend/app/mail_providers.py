# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Mail-Anbieter fuer die Einrichtungsassistenten
# ==============================================================================
"""
Die Anbieter, fuer die es unter System -> Mail einen eigenen Assistenten gibt.

Jeder Eintrag sagt der Oberflaeche, was sie abfragen muss und was sie selbst
vorbelegt. Fuer die meisten Anbieter ist das ein SMTP-Zugang mit festem Server,
Port und Verschluesselung - der Benutzer gibt nur Adresse und Passwort ein.
Was beim Anbieter vorher eingeschaltet werden muss (IMAP/SMTP-Zugriff,
App-Passwort), steht als Schrittfolge mit direkten Links dabei.

Arten (``kind``):

* ``m365``      - Anmeldung bei Microsoft, die App-Registrierung entsteht
                  automatisch (siehe ``mail_m365.py``).
* ``preset``    - SMTP mit festen Werten, nur Adresse + Passwort.
* ``smtp``      - beliebiger Mailserver, alles von Hand.
* ``container`` - der mitgelieferte Postfix.

Warum Google kein "anmelden und fertig" bekommt wie Microsoft: Google laesst
OAuth-Clients fuer Gmail nicht per API anlegen, und der Geraetecode-Weg ist
fuer Gmail-Berechtigungen gesperrt. Ein gemeinsamer RIMPERIO-Client muesste
von Google teuer geprueft werden (eingeschraenkter Scope). Das App-Passwort
ist der Weg, den Google fuer genau diesen Fall vorsieht.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "m365",
        "name": "Microsoft 365",
        "kind": "m365",
        "badge": "Anmeldung bei Microsoft",
        "hint": ("Mit einem Administrator anmelden — RIMPERIO legt die "
                 "App-Registrierung selbst an. Versand über Microsoft Graph."),
        "domains": [],
        "notes": [
            "Nur Firmen- und Schulkonten (Exchange Online). Private Outlook.com-/"
            "Hotmail-Adressen werden nicht unterstützt.",
        ],
    },
    {
        "id": "google",
        "name": "Google / Gmail",
        "kind": "preset",
        "badge": "App-Passwort",
        "hint": "Gmail und Google Workspace über ein App-Passwort.",
        "host": "smtp.gmail.com", "port": 587, "encryption": "starttls",
        "password_label": "App-Passwort (16 Zeichen)",
        "strip_spaces": True,
        "domains": ["gmail.com", "googlemail.com"],
        "prep": [
            {"text": "Die Bestätigung in zwei Schritten muss eingeschaltet sein — ohne "
                     "sie bietet Google keine App-Passwörter an.",
             "link": "https://myaccount.google.com/signinoptions/twosv",
             "link_label": "Bestätigung in zwei Schritten"},
            {"text": "Ein App-Passwort anlegen, Name zum Beispiel „RIMPERIO“. Google zeigt "
                     "es nur einmal an — gleich im nächsten Schritt einfügen.",
             "link": "https://myaccount.google.com/apppasswords",
             "link_label": "App-Passwörter öffnen"},
        ],
        "notes": [
            "Google Workspace: Steht unter App-Passwörter nichts, hat der Administrator "
            "sie abgeschaltet (Admin-Konsole → Sicherheit → Bestätigung in zwei Schritten).",
            "Als Absender geht nur die eigene Adresse oder ein in Gmail hinterlegter Alias.",
        ],
    },
    {
        "id": "gmx",
        "name": "GMX",
        "kind": "preset",
        "badge": "Passwort",
        "hint": "GMX FreeMail und ProMail.",
        "host": "mail.gmx.net", "port": 587, "encryption": "starttls",
        "password_label": "GMX-Passwort (bzw. App-Passwort bei Zwei-Faktor)",
        "domains": ["gmx.de", "gmx.net", "gmx.at", "gmx.ch"],
        "prep": [
            {"text": "Im GMX-Postfach: E-Mail → Einstellungen → POP3 & IMAP → "
                     "„POP3 und IMAP Zugriff erlauben“ einschalten und speichern. Ohne "
                     "diesen Haken lehnt GMX jede SMTP-Anmeldung ab.",
             "link": "https://www.gmx.net/",
             "link_label": "GMX öffnen"},
            {"text": "Ist die Zwei-Faktor-Authentifizierung aktiv, in den "
                     "Sicherheitseinstellungen ein anwendungsspezifisches Passwort anlegen "
                     "und das statt des normalen Passworts verwenden."},
        ],
        "notes": ["Als Absender geht nur die eigene GMX-Adresse."],
    },
    {
        "id": "webde",
        "name": "WEB.DE",
        "kind": "preset",
        "badge": "Passwort",
        "hint": "WEB.DE FreeMail und Club.",
        "host": "smtp.web.de", "port": 587, "encryption": "starttls",
        "password_label": "WEB.DE-Passwort (bzw. App-Passwort bei Zwei-Faktor)",
        "domains": ["web.de"],
        "prep": [
            {"text": "Im WEB.DE-Postfach: Einstellungen → POP3/IMAP Abruf → "
                     "„POP3 und IMAP Zugriff erlauben“ einschalten und speichern. Ohne "
                     "diesen Haken lehnt WEB.DE jede SMTP-Anmeldung ab.",
             "link": "https://web.de/",
             "link_label": "WEB.DE öffnen"},
            {"text": "Ist die Zwei-Faktor-Authentifizierung aktiv, ein "
                     "anwendungsspezifisches Passwort anlegen und das verwenden."},
        ],
        "notes": ["Als Absender geht nur die eigene WEB.DE-Adresse."],
    },
    {
        "id": "tonline",
        "name": "T-Online",
        "kind": "preset",
        "badge": "E-Mail-Passwort",
        "hint": "Telekom-Mail (@t-online.de, @magenta.de).",
        "host": "securesmtp.t-online.de", "port": 465, "encryption": "tls",
        "password_label": "E-Mail-Passwort (nicht das Telekom-Login-Passwort)",
        "domains": ["t-online.de", "magenta.de"],
        "prep": [
            {"text": "Die Telekom verlangt für Mailprogramme ein eigenes E-Mail-Passwort. "
                     "Im E-Mail Center unter Einstellungen → Passwörter → E-Mail-Passwort "
                     "anlegen.",
             "link": "https://email.t-online.de/",
             "link_label": "E-Mail Center öffnen"},
        ],
        "notes": [],
    },
    {
        "id": "ionos",
        "name": "IONOS",
        "kind": "preset",
        "badge": "Passwort",
        "hint": "Postfächer auf einer eigenen Domain bei IONOS.",
        "host": "smtp.ionos.de", "port": 587, "encryption": "starttls",
        "password_label": "Passwort des Postfachs",
        "domains": [],
        "prep": [],
        "notes": ["Benutzer ist die vollständige E-Mail-Adresse des Postfachs."],
    },
    {
        "id": "strato",
        "name": "STRATO",
        "kind": "preset",
        "badge": "Passwort",
        "hint": "Postfächer auf einer eigenen Domain bei STRATO.",
        "host": "smtp.strato.de", "port": 465, "encryption": "tls",
        "password_label": "Passwort des Postfachs",
        "domains": [],
        "prep": [],
        "notes": ["Benutzer ist die vollständige E-Mail-Adresse des Postfachs."],
    },
    {
        "id": "icloud",
        "name": "iCloud Mail",
        "kind": "preset",
        "badge": "App-Passwort",
        "hint": "@icloud.com, @me.com, @mac.com.",
        "host": "smtp.mail.me.com", "port": 587, "encryption": "starttls",
        "password_label": "App-spezifisches Passwort",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "prep": [
            {"text": "Unter Apple-Account → Anmeldung und Sicherheit → App-spezifische "
                     "Passwörter ein neues Passwort anlegen.",
             "link": "https://account.apple.com/",
             "link_label": "Apple-Account öffnen"},
        ],
        "notes": ["Benutzer ist die iCloud-Adresse (nicht eine Fremd-Apple-ID)."],
    },
    {
        "id": "yahoo",
        "name": "Yahoo Mail",
        "kind": "preset",
        "badge": "App-Passwort",
        "hint": "@yahoo.com, @yahoo.de.",
        "host": "smtp.mail.yahoo.com", "port": 465, "encryption": "tls",
        "password_label": "App-Passwort",
        "strip_spaces": True,
        "domains": ["yahoo.com", "yahoo.de"],
        "prep": [
            {"text": "In den Kontosicherheits-Einstellungen ein App-Passwort erzeugen.",
             "link": "https://login.yahoo.com/account/security",
             "link_label": "Kontosicherheit öffnen"},
        ],
        "notes": [],
    },
    {
        "id": "posteo",
        "name": "Posteo",
        "kind": "preset",
        "badge": "Passwort",
        "hint": "Posteo-Postfach.",
        "host": "posteo.de", "port": 587, "encryption": "starttls",
        "password_label": "Posteo-Passwort",
        "domains": ["posteo.de", "posteo.net"],
        "prep": [],
        "notes": [],
    },
    {
        "id": "mailboxorg",
        "name": "mailbox.org",
        "kind": "preset",
        "badge": "Passwort",
        "hint": "mailbox.org-Postfach.",
        "host": "smtp.mailbox.org", "port": 465, "encryption": "tls",
        "password_label": "mailbox.org-Passwort",
        "domains": ["mailbox.org"],
        "prep": [],
        "notes": [],
    },
    {
        "id": "smtp",
        "name": "Anderer Mailserver",
        "kind": "smtp",
        "badge": "SMTP",
        "hint": "Eigener Exchange, Firmen-Relay, Hoster — alles von Hand eintragen.",
        "domains": [],
        "notes": [],
    },
    {
        "id": "container",
        "name": "Postfix auf diesem Server",
        "kind": "container",
        "badge": "Zusatzdienst",
        "hint": ("RIMPERIO bringt seinen eigenen Mailserver mit, optional mit "
                 "Smarthost. Braucht das Profil „postfix“ unter Zusatzdienste."),
        "domains": [],
        "notes": [],
    },
]

_BY_ID = {provider["id"]: provider for provider in PROVIDERS}


def get(provider_id: str) -> Optional[Dict[str, Any]]:
    return _BY_ID.get(provider_id or "")


def public_list() -> List[Dict[str, Any]]:
    return [dict(provider) for provider in PROVIDERS]


def apply_preset(provider_id: str, incoming: dict) -> dict:
    """Ueberschreibt Server/Port/Verschluesselung mit den festen Werten.

    Damit kann die Oberflaeche bei einer Vorlage nichts Falsches schicken -
    und eine spaetere Korrektur der Vorlage (neuer Hostname beim Anbieter)
    greift beim naechsten Speichern.
    """
    provider = get(provider_id)
    if not provider or provider["kind"] != "preset":
        return incoming
    result = dict(incoming)
    result["mode"] = "relay"
    result["smtp_host"] = provider["host"]
    result["smtp_port"] = provider["port"]
    result["encryption"] = provider["encryption"]
    result["verify_certificate"] = True
    if not result.get("smtp_user") and result.get("from_address"):
        result["smtp_user"] = result["from_address"]
    if provider.get("strip_spaces") and result.get("smtp_password"):
        result["smtp_password"] = "".join(str(result["smtp_password"]).split())
    return result
