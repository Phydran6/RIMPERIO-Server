# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.31.23.30.00
# Beschreibung: RIMPERIO - Einstufung von FRITZ!Box-Ereignissen (Level + Source)
# ==============================================================================
"""Die FRITZ!Box liefert im Ereignisprotokoll keinen Schweregrad, sondern nur
eine Ereignis-ID (`id`) und eine Gruppe (`sys`/`net`/`wlan`/`fon`/`usb`).

Hier wird daraus ein Syslog-Level und ein Dienstname (`source`) abgeleitet:

1. Ist die Ereignis-ID bekannt, gilt der Eintrag aus EVENT_LEVELS.
2. Sonst entscheiden Stichwoerter im Meldungstext (Fehler/Warnung).
3. Sonst "info".

Neue IDs muessen also nicht gepflegt werden - sie landen ueber die Stichwoerter
in einer plausiblen Stufe. Wer eine ID sauber einstufen will, traegt sie in
EVENT_LEVELS nach (Hilfetext der Box: /help/help.lua?helppage=hilfe_syslog_<id>.html).
"""

from typing import Optional, Tuple

# Bekannte Ereignis-IDs -> Level. Stand: FRITZ!OS 8.22, aus dem realen
# Ereignisprotokoll der 6690 Cable erhoben (2026-07-31).
EVENT_LEVELS = {
    # --- Internet / Verbindung -------------------------------------------
    9:     "info",     # Kabel-Internet ist verfuegbar (Synchronisierung besteht)
    22:    "notice",   # Internetverbindung wurde erfolgreich hergestellt
    25:    "notice",   # Internetverbindung IPv6 wurde erfolgreich hergestellt
    26:    "info",     # IPv6-Praefix wurde aktualisiert
    27:    "info",     # IPv6-Praefix wurde bezogen
    4012:  "notice",   # Kabel-Internet Synchronisierung beginnt (Training)
    # --- VPN --------------------------------------------------------------
    120:   "notice",   # VPN-Verbindung aufgebaut
    121:   "warning",  # VPN-Verbindung wurde getrennt
    122:   "error",    # VPN-Fehler (IKE-Error)
    # --- Anmeldung / Konfiguration ---------------------------------------
    502:   "notice",   # Einstellungen ueber die Benutzeroberflaeche geaendert
    503:   "warning",  # Anmeldung an der Benutzeroberflaeche gescheitert
    504:   "info",     # Anmeldung eines Benutzers erfolgreich
    505:   "info",     # Anmeldung eines Benutzers erfolgreich
    2018:  "notice",   # Portfreigabe hinzugefuegt
    2358:  "notice",   # Einstellungen wurden gesichert
    # --- System ------------------------------------------------------------
    2104:  "info",     # Systemzeit wurde aktualisiert
    2343:  "info",     # Netzwerkgeraet hat sich verbunden
    # --- WLAN ---------------------------------------------------------------
    659:   "info",     # WLAN-Geraet angemeldet, volle Leistung
    660:   "info",     # Kein WLAN-Geraet mehr angemeldet, Stromsparen
    752:   "info",     # WLAN-Geraet hat sich abgemeldet
    754:   "info",     # WLAN-Geraet wurde abgemeldet
    766:   "info",     # WLAN-Geraet umgemeldet (Band-Steering)
    774:   "info",     # WLAN-Autokanal: Umgebung wird erfasst
    784:   "info",     # WLAN-Autokanal: Kanal unveraendert
    785:   "info",     # WLAN-Autokanal: Kanal geaendert
    787:   "info",     # Temporaerer Kanalwechsel (Radarpruefung)
    30005: "info",     # WLAN-Geraet angemeldet
}

# Ereignisse, die inhaltlich zur Anmeldung bzw. zur Konfigurationsaenderung
# gehoeren. Sie bekommen einen eigenen Dienstnamen, damit die Log-Kategorien
# "Anmeldung & Rechte" und "Audit" in der Log-Ansicht greifen.
AUTH_EVENTS = {503, 504, 505}
AUDIT_EVENTS = {502, 2018, 2358}

# Gruppe der FRITZ!Box -> Dienstname in RIMPERIO
GROUP_SOURCES = {
    "sys":  "fritzbox-sys",
    "net":  "fritzbox-net",
    "wlan": "fritzbox-wlan",
    "fon":  "fritzbox-fon",
    "usb":  "fritzbox-usb",
}

# Stichwoerter fuer unbekannte Ereignis-IDs (Meldungstext, klein geschrieben)
ERROR_HINTS = ("fehler", "fehlgeschlagen", "konnte nicht", "nicht moeglich",
               "nicht möglich", "abgelehnt", "verweigert")
WARNING_HINTS = ("gescheitert", "getrennt", "unterbrochen", "ungueltig",
                 "ungültig", "keine verbindung", "zeitueberschreitung",
                 "zeitüberschreitung", "warnung", "gesperrt")


def classify(event_id: Optional[int], group: Optional[str], message: str) -> Tuple[str, str]:
    """Gibt (level, source) fuer einen Eintrag aus dem FRITZ!Box-Ereignisprotokoll."""
    level = EVENT_LEVELS.get(event_id) if event_id is not None else None

    if level is None:
        text = (message or "").lower()
        if any(hint in text for hint in ERROR_HINTS):
            level = "error"
        elif any(hint in text for hint in WARNING_HINTS):
            level = "warning"
        else:
            level = "info"

    if event_id in AUTH_EVENTS:
        source = "fritzbox-auth"
    elif event_id in AUDIT_EVENTS:
        source = "fritzbox-audit"
    else:
        source = GROUP_SOURCES.get((group or "").strip().lower(), "fritzbox")

    return level, source
