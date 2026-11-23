# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Logzeilen in Klartext uebersetzen (Was ist passiert?)
# ==============================================================================
"""
`logparse` zerlegt eine Zeile in ihre Teile. Das macht sie *ordentlich*, aber
noch nicht *verstaendlich*: "pam_unix(sshd:auth): authentication failure;
logname= uid=0 euid=0 tty=ssh ruser= rhost=203.0.113.7 user=root" bleibt auch
zerlegt ein Raetsel, wenn man nicht taeglich Linux-Logs liest.

Dieses Modul beantwortet deshalb die Frage, die man eigentlich hat:
**Was ist passiert - und muss ich etwas tun?**

Fuer jede Zeile gibt es (wenn ein Muster passt):

* `title`    - ein deutscher Satz: "Anmeldung als root von 203.0.113.7 fehlgeschlagen (SSH)"
* `category` - Themenbereich fuer Gruppen in App und Oberflaeche
               (Anmeldung, Netzwerk, Dienste, System, Hardware, Sicherheit,
               Firewall, Updates, Container, Zeitplan, Mail, Web)
* `hint`     - wenn sinnvoll: was man tun sollte / was es bedeutet
* `noise`    - typisches Grundrauschen (Cron-Sitzungen, DHCP-Verlaengerung,
               "Started session") - wird bei der Rausch-Erkennung bevorzugt
               vorgeschlagen

Bewusst regelbasiert statt KI: es muss fuer Millionen Zeilen schnell sein, darf
nichts kosten und muss reproduzierbar sein. Fuer den Einzelfall gibt es
zusaetzlich die KI-Erklaerung einer einzelnen Zeile (routes/loghandling.py).

Nichts hier aendert gespeicherte Daten - es ist nur Anzeige.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# Kategorien: Schluessel -> Anzeigename. Reihenfolge = Reihenfolge in der App.
CATEGORIES: Dict[str, str] = {
    "auth": "Anmeldung",
    "security": "Sicherheit",
    "firewall": "Firewall",
    "network": "Netzwerk",
    "service": "Dienste",
    "system": "System",
    "hardware": "Hardware",
    "storage": "Speicher",
    "update": "Updates",
    "container": "Container",
    "schedule": "Zeitplan",
    "mail": "Mail",
    "web": "Web",
    "other": "Sonstiges",
}

Explanation = Dict[str, Any]
Rule = Tuple[str, "re.Pattern[str]", Callable[[re.Match], Explanation]]


def _e(title: str, category: str, hint: str = "", noise: bool = False) -> Explanation:
    return {"title": title, "category": category, "hint": hint, "noise": noise}


def _g(m: re.Match, name: str, default: str = "") -> str:
    value = m.groupdict().get(name)
    return (value or default).strip()

_IP = r"(?P<ip>[0-9a-fA-F:.]+)"

# =============================================================================
# Musterliste: (Name, Ausdruck, Erklaerung). Die erste passende gewinnt.
# Ausdruecke laufen gegen die *bereinigte* Meldung (ohne Syslog-Kopf).
# =============================================================================
RULES: List[Rule] = []


def rule(name: str, pattern: str, flags: int = re.IGNORECASE):
    def wrap(fn: Callable[[re.Match], Explanation]):
        RULES.append((name, re.compile(pattern, flags), fn))
        return fn
    return wrap


# --- SSH / PAM / sudo ---------------------------------------------------------
@rule("ssh.failed", r"Failed (?P<method>password|publickey|keyboard-interactive\S*) for (?P<inv>invalid user )?(?P<user>\S+) from " + _IP)
def _(m):
    who = f"unbekannter Benutzer {_g(m, 'user')}" if _g(m, "inv") else _g(m, "user")
    how = {"password": "Passwort", "publickey": "Schlüssel"}.get(_g(m, "method"), _g(m, "method"))
    return _e(f"SSH-Anmeldung als {who} von {_g(m, 'ip')} fehlgeschlagen ({how})", "auth",
              "Einzelne Fehlversuche sind normal (Tippfehler). Viele in kurzer Zeit von einer "
              "Adresse deuten auf einen Angriff – SSH nur per Schlüssel und Fail2ban helfen.")


@rule("ssh.accepted", r"Accepted (?P<method>password|publickey|keyboard-interactive\S*) for (?P<user>\S+) from " + _IP)
def _(m):
    how = {"password": "Passwort", "publickey": "Schlüssel"}.get(_g(m, "method"), _g(m, "method"))
    return _e(f"SSH-Anmeldung als {_g(m, 'user')} von {_g(m, 'ip')} erfolgreich ({how})", "auth")


@rule("ssh.invalid", r"Invalid user (?P<user>\S*) from " + _IP)
def _(m):
    return _e(f"SSH: Anmeldeversuch mit unbekanntem Benutzer „{_g(m, 'user')}“ von {_g(m, 'ip')}", "security",
              "Typisch für automatisierte Scans aus dem Internet.")


@rule("ssh.disconnect", r"(?:Disconnected from|Connection closed by|Received disconnect from) (?:(?:invalid |authenticating )?user (?P<user>\S+) )?" + _IP)
def _(m):
    user = _g(m, "user")
    return _e(f"SSH-Verbindung von {_g(m, 'ip')} beendet" + (f" ({user})" if user else ""), "auth", noise=True)


@rule("ssh.maxauth", r"(?:maximum authentication attempts exceeded|Too many authentication failures) for (?:invalid user )?(?P<user>\S+) from " + _IP)
def _(m):
    return _e(f"SSH: zu viele Fehlversuche als {_g(m, 'user')} von {_g(m, 'ip')} – Verbindung getrennt", "security",
              "Deutet auf einen Passwort-Rateangriff hin.")


@rule("pam.authfail", r"pam_unix\((?P<svc>[^:)]+)(?::auth)?\): authentication failure")
def _(m):
    rest = m.string[m.end():]
    ip = (re.search(r"rhost=(\S+)", rest) or [None, ""])[1]
    user = (re.search(r"\buser=(\S+)", rest) or [None, ""])[1]
    title = f"Anmeldung bei {_g(m, 'svc')} fehlgeschlagen" + (f" als {user}" if user else "") + (f" von {ip}" if ip else "")
    return _e(title, "auth")


@rule("pam.session", r"pam_unix\((?P<svc>[^:)]+):session\): session (?P<what>opened|closed) for user (?P<user>[^\s(]+)")
def _(m):
    svc, user = _g(m, "svc"), _g(m, "user")
    verb = "geöffnet" if _g(m, "what") == "opened" else "geschlossen"
    noisy = svc in ("cron", "CRON", "systemd-user", "sudo", "su")
    return _e(f"Sitzung für {user} {verb} ({svc})", "schedule" if svc.lower() == "cron" else "auth", noise=noisy)


@rule("sudo.cmd", r"^\s*(?P<user>\S+)\s*:\s*(?:TTY=\S+\s*;\s*)?PWD=(?P<pwd>\S+)\s*;\s*USER=(?P<as>\S+)\s*;\s*COMMAND=(?P<cmd>.+)$")
def _(m):
    return _e(f"{_g(m, 'user')} hat als {_g(m, 'as')} ausgeführt: {_g(m, 'cmd')[:160]}", "auth")


@rule("sudo.fail", r"(?P<user>\S+)\s*:\s*(?P<n>\d+) incorrect password attempts?")
def _(m):
    return _e(f"sudo: {_g(m, 'n')} falsche Passworteingabe(n) von {_g(m, 'user')}", "security")


@rule("su", r"\(to (?P<to>\S+)\) (?P<user>\S+) on")
def _(m):
    return _e(f"{_g(m, 'user')} hat zu {_g(m, 'to')} gewechselt (su)", "auth")


@rule("login.new_user", r"new user: name=(?P<user>[^,\s]+)")
def _(m):
    return _e(f"Neues Benutzerkonto angelegt: {_g(m, 'user')}", "security",
              "Prüfen, ob das gewollt war.")


@rule("passwd.changed", r"password changed for (?P<user>\S+)")
def _(m):
    return _e(f"Passwort von {_g(m, 'user')} geändert", "security")


# --- systemd ------------------------------------------------------------------
@rule("systemd.session", r"(?:New session|Removed session|Started Session|Session) (?P<id>\S+) (?:of [Uu]ser (?P<user>[^\s.]+)|logged out)")
def _(m):
    user = _g(m, "user")
    return _e("Benutzersitzung " + (f"von {user} " if user else "") + "geöffnet/geschlossen", "auth", noise=True)


@rule("systemd.started", r"^(?:Started|Finished) (?P<unit>.+?)\.?$")
def _(m):
    unit = _g(m, "unit")
    noisy = bool(re.search(r"session|Cleanup|Daily|apt|man-db|logrotate|Message of the Day|timer|fstrim|tmpfiles|sysstat|phpsessionclean", unit, re.I))
    return _e(f"Dienst/Aufgabe gestartet: {unit}", "service", noise=noisy)


@rule("systemd.stopped", r"^(?:Stopped|Stopping) (?P<unit>.+?)\.?$")
def _(m):
    unit = _g(m, "unit")
    noisy = bool(re.search(r"session|user@|User Manager|Daily|apt", unit, re.I))
    return _e(f"Dienst beendet: {unit}", "service", noise=noisy)


@rule("systemd.failed", r"(?P<unit>[\w@.\-]+\.(?:service|socket|mount|timer)): (?:Failed with result '(?P<res>[^']+)'|Main process exited, code=(?P<code>\w+), status=(?P<status>\S+))")
def _(m):
    unit = _g(m, "unit")
    if _g(m, "res"):
        why = {"exit-code": "mit Fehlercode beendet", "signal": "durch ein Signal beendet",
               "timeout": "Zeitüberschreitung", "core-dump": "abgestürzt (Core-Dump)",
               "oom-kill": "wegen Speichermangel beendet", "start-limit-hit": "zu oft neu gestartet"}.get(_g(m, "res"), _g(m, "res"))
        return _e(f"Dienst {unit} fehlgeschlagen: {why}", "service",
                  f"Details: journalctl -u {unit} -n 50")
    return _e(f"Dienst {unit} hat sich beendet (Status {_g(m, 'status')})", "service")


@rule("systemd.restart", r"(?P<unit>[\w@.\-]+\.service): Scheduled restart job, restart counter is at (?P<n>\d+)")
def _(m):
    return _e(f"Dienst {_g(m, 'unit')} wird neu gestartet ({_g(m, 'n')}. Versuch)", "service",
              "Startet ein Dienst immer wieder neu, ist er vermutlich falsch eingerichtet.")


@rule("systemd.reached", r"^Reached target (?P<t>.+?)\.?$")
def _(m):
    return _e(f"Systemzustand erreicht: {_g(m, 't')}", "system", noise=True)


# --- Kernel / Hardware / Speicher ---------------------------------------------
@rule("kernel.oom", r"Out of memory: Kill(?:ed)? process (?P<pid>\d+) \((?P<proc>[^)]+)\)")
def _(m):
    return _e(f"Arbeitsspeicher voll – Prozess {_g(m, 'proc')} wurde beendet", "system",
              "Der Rechner hat zu wenig RAM für seine Last. Speicher erhöhen oder Verbraucher finden.")


@rule("kernel.oom2", r"oom-kill:|invoked oom-killer")
def _(m):
    return _e("Arbeitsspeicher voll – der Kernel musste einen Prozess beenden", "system",
              "Der Rechner hat zu wenig RAM für seine Last.")


@rule("kernel.segfault", r"(?P<proc>[\w.\-]+)\[\d+\]: segfault at")
def _(m):
    return _e(f"Programm {_g(m, 'proc')} ist abgestürzt (Speicherzugriffsfehler)", "system")


@rule("disk.io", r"(?:I/O error|Buffer I/O error|blk_update_request: I/O error|critical medium error).*?dev (?P<dev>[\w\-]+)")
def _(m):
    return _e(f"Lese-/Schreibfehler auf Datenträger {_g(m, 'dev')}", "hardware",
              "Möglicher Plattendefekt – SMART-Werte prüfen und Sicherung sicherstellen.")


@rule("disk.smart", r"(?:SMART|smartd).*?(?:Device: (?P<dev>\S+)).*?(?:FAILED|failure|Currently unreadable|Offline uncorrectable|Reallocated)")
def _(m):
    return _e(f"SMART meldet ein Problem an {_g(m, 'dev')}", "hardware",
              "Die Platte zeigt Verschleiß oder Defekte – austauschen planen.")


@rule("fs.full", r"No space left on device")
def _(m):
    return _e("Kein Platz mehr auf dem Datenträger", "storage",
              "Sofort Platz schaffen – Dienste können sonst nichts mehr schreiben.")


@rule("fs.ext4", r"EXT4-fs (?:error|warning) \(device (?P<dev>[^)]+)\)")
def _(m):
    return _e(f"Dateisystemfehler auf {_g(m, 'dev')}", "storage", "Dateisystem prüfen (fsck).")


@rule("zfs", r"zed?:.*?(?:class=)?(?P<cls>(?:statechange|checksum|io|data|scrub_finish|resilver_finish))")
def _(m):
    cls = _g(m, "cls")
    text = {"checksum": "Prüfsummenfehler", "io": "E/A-Fehler", "data": "Datenfehler",
            "statechange": "Zustandsänderung", "scrub_finish": "Scrub abgeschlossen",
            "resilver_finish": "Resilver abgeschlossen"}.get(cls, cls)
    return _e(f"ZFS: {text}", "storage", noise=cls in ("scrub_finish", "resilver_finish"))


@rule("kernel.link", r"(?P<if>(?:eth|en|wl|br|vmbr|bond|tap|veth|fwpr|fwln)[\w.\-]*):? .*?[Ll]ink (?:is )?(?P<state>up|down)")
def _(m):
    state = "verbunden" if _g(m, "state").lower() == "up" else "getrennt"
    noisy = _g(m, "if").startswith(("tap", "veth", "fwpr", "fwln"))
    return _e(f"Netzwerkanschluss {_g(m, 'if')} {state}", "network", noise=noisy)


@rule("kernel.port_state", r"(?P<br>vmbr\d+|br[\w\-]*): port \d+\((?P<port>[^)]+)\) entered (?P<state>\w+) state")
def _(m):
    return _e(f"Bridge {_g(m, 'br')}: Anschluss {_g(m, 'port')} → {_g(m, 'state')}", "network", noise=True)


@rule("kernel.temp", r"(?:temperature above threshold|cpu clock throttled|Package temperature)")
def _(m):
    return _e("CPU zu heiß – Takt wird gedrosselt", "hardware", "Lüfter und Kühlung prüfen.")


@rule("kernel.mce", r"(?:Machine check events logged|Hardware Error|mce: \[Hardware Error\])")
def _(m):
    return _e("Hardwarefehler (Machine Check) gemeldet", "hardware",
              "Kann auf defekten RAM oder CPU hinweisen – Hersteller-Diagnose laufen lassen.")


# --- Firewall / Netfilter ------------------------------------------------------
@rule("ufw", r"\[UFW (?P<act>BLOCK|ALLOW|AUDIT|LIMIT)\].*?SRC=(?P<src>\S+).*?DST=(?P<dst>\S+).*?PROTO=(?P<proto>\S+)(?:.*?DPT=(?P<dpt>\d+))?")
def _(m):
    act = {"BLOCK": "blockiert", "ALLOW": "erlaubt", "LIMIT": "begrenzt", "AUDIT": "protokolliert"}[_g(m, "act").upper()]
    port = f":{_g(m, 'dpt')}" if _g(m, "dpt") else ""
    return _e(f"Firewall hat {_g(m, 'proto')} von {_g(m, 'src')} nach {_g(m, 'dst')}{port} {act}", "firewall",
              noise=act in ("blockiert", "protokolliert"))


@rule("netfilter", r"IN=(?P<in>\S*) OUT=(?P<out>\S*).*?SRC=(?P<src>\S+) DST=(?P<dst>\S+).*?PROTO=(?P<proto>\S+)(?:.*?DPT=(?P<dpt>\d+))?")
def _(m):
    port = f":{_g(m, 'dpt')}" if _g(m, "dpt") else ""
    return _e(f"Paketfilter: {_g(m, 'proto')} von {_g(m, 'src')} nach {_g(m, 'dst')}{port}", "firewall", noise=True)


@rule("fail2ban", r"\[(?P<jail>[\w\-]+)\] (?P<act>Ban|Unban|Found) " + _IP)
def _(m):
    act = {"Ban": "gesperrt", "Unban": "wieder freigegeben", "Found": "erkannt"}.get(_g(m, "act"), _g(m, "act"))
    return _e(f"Fail2ban ({_g(m, 'jail')}): {_g(m, 'ip')} {act}", "security", noise=_g(m, "act") == "Found")


# --- Netzwerkdienste -------------------------------------------------------------
@rule("dhcp.ack", r"DHCP(?P<kind>ACK|REQUEST|DISCOVER|OFFER|INFORM|RELEASE)\b")
def _(m):
    kind = _g(m, "kind").upper()
    rest = m.string[m.end():]
    ip = (re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", rest) or [None, ""])[1]
    mac = (re.search(r"\b((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\b", rest) or [None, ""])[1]
    text = {"ACK": "Adresse vergeben", "REQUEST": "Adresse angefragt", "DISCOVER": "Gerät sucht DHCP-Server",
            "OFFER": "Adresse angeboten", "INFORM": "Informationsanfrage", "RELEASE": "Adresse zurückgegeben"}[kind]
    who = " ".join(x for x in (ip, mac) if x)
    return _e(f"DHCP: {text}" + (f" ({who})" if who else ""), "network", noise=True)


@rule("wifi.assoc", r"(?P<if>\S+): STA (?P<mac>(?:[0-9a-f]{2}:){5}[0-9a-f]{2}) (?:IEEE 802\.11: )?(?P<what>associated|disassociated|deauthenticated|authenticated|WPA: pairwise key handshake completed)", re.IGNORECASE)
def _(m):
    what = _g(m, "what").lower()
    text = ("verbunden" if what in ("associated", "authenticated") or "handshake" in what
            else "getrennt")
    return _e(f"WLAN: Gerät {_g(m, 'mac')} {text} ({_g(m, 'if')})", "network", noise=True)


@rule("dns.refused", r"(?:query|client).*?(?P<ip>\d+\.\d+\.\d+\.\d+).*?(?:denied|REFUSED)")
def _(m):
    return _e(f"DNS-Anfrage von {_g(m, 'ip')} abgelehnt", "network", noise=True)


@rule("ntp", r"(?:time stepped|Time has been changed|System clock wrong|clock_step|Synchronized to time server|Initial clock synchronization)")
def _(m):
    return _e("Systemzeit wurde synchronisiert/angepasst", "system", noise=True)


@rule("nm", r"NetworkManager.*?device \((?P<dev>[^)]+)\): state change: (?P<from>\w+) -> (?P<to>\w+)")
def _(m):
    return _e(f"Netzwerk {_g(m, 'dev')}: {_g(m, 'from')} → {_g(m, 'to')}", "network", noise=True)


# --- Zeitplan / Wartung ---------------------------------------------------------
@rule("cron.cmd", r"\((?P<user>[^)]+)\) CMD \((?P<cmd>.+)\)\s*$")
def _(m):
    return _e(f"Geplante Aufgabe für {_g(m, 'user')}: {_g(m, 'cmd')[:140]}", "schedule", noise=True)


@rule("logrotate", r"logrotate|rotating pattern|log rotation")
def _(m):
    return _e("Protokolldateien wurden rotiert", "schedule", noise=True)


# --- Paketverwaltung ---------------------------------------------------------------
@rule("apt.install", r"(?:^| )(?P<act>install|upgrade|remove|purge) (?P<pkg>[\w.+\-]+)(?::\w+)? (?P<from>\S+) (?P<to>\S+)")
def _(m):
    act = {"install": "installiert", "upgrade": "aktualisiert", "remove": "entfernt", "purge": "entfernt"}[_g(m, "act")]
    return _e(f"Paket {_g(m, 'pkg')} {act} ({_g(m, 'from')} → {_g(m, 'to')})", "update")


@rule("unattended", r"unattended-upgrade|Packages that will be upgraded|All upgrades installed")
def _(m):
    return _e("Automatische Updates (unattended-upgrades) gelaufen", "update", noise=True)


# --- Container ---------------------------------------------------------------------
@rule("docker.health", r"(?:container|Container) (?P<name>[\w.\-]+).*?(?:health_status|health status)[: ]+(?P<st>\w+)")
def _(m):
    st = {"healthy": "gesund", "unhealthy": "ungesund", "starting": "startet"}.get(_g(m, "st"), _g(m, "st"))
    return _e(f"Container {_g(m, 'name')}: {st}", "container", noise=_g(m, "st") == "healthy")


@rule("docker.died", r"(?:container (?P<name>[\w.\-]+) (?:died|exited)|ShouldRestart failed, container will not be restarted)")
def _(m):
    name = _g(m, "name")
    return _e("Container " + (f"{name} " if name else "") + "ist beendet", "container")


@rule("docker.veth", r"veth\w+.*?(?:renamed from|entered (?:blocking|forwarding|disabled) state)")
def _(m):
    return _e("Container-Netzwerk umgeschaltet (veth)", "container", noise=True)


# --- Mail / Web --------------------------------------------------------------------
@rule("postfix.sent", r"to=<(?P<to>[^>]+)>.*?status=(?P<st>sent|bounced|deferred)")
def _(m):
    st = {"sent": "zugestellt", "bounced": "abgewiesen", "deferred": "zurückgestellt"}[_g(m, "st")]
    return _e(f"Mail an {_g(m, 'to')} {st}", "mail", noise=_g(m, "st") == "sent")


@rule("postfix.auth", r"SASL (?:LOGIN|PLAIN) authentication failed.*?\[(?P<ip>[0-9a-fA-F:.]+)\]")
def _(m):
    return _e(f"Mail-Anmeldung von {_g(m, 'ip')} fehlgeschlagen", "security")


@rule("http.access", r'^(?P<ip>[0-9a-fA-F:.]+) \S+ \S+ \[[^\]]+\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" (?P<code>\d{3})')
def _(m):
    code = int(_g(m, "code") or 0)
    ok = 200 <= code < 400
    return _e(f"Webzugriff {_g(m, 'method')} {_g(m, 'path')[:80]} von {_g(m, 'ip')} → {code}", "web",
              "" if ok else ("Seite nicht gefunden." if code == 404 else
                             "Zugriff verweigert." if code in (401, 403) else
                             "Serverfehler." if code >= 500 else ""),
              noise=ok)


# --- Proxmox -----------------------------------------------------------------------
@rule("pve.task", r"(?:starting|end) task UPID:[^:]+:[^:]+:[^:]+:[^:]+:(?P<type>\w+):(?P<id>[^:]*):(?P<user>[^:]+):(?:\s*(?P<st>.*))?")
def _(m):
    t = {"vzdump": "Sicherung", "qmstart": "VM-Start", "qmstop": "VM-Stopp", "qmshutdown": "VM herunterfahren",
         "vzstart": "Container-Start", "vzstop": "Container-Stopp", "qmsnapshot": "Snapshot",
         "vzsnapshot": "Snapshot", "qmrollback": "Snapshot zurückspielen", "aptupdate": "Paketlisten auffrischen",
         "termproxy": "Konsole", "vncproxy": "Konsole", "imgcopy": "Kopie", "qmigrate": "Migration"}.get(_g(m, "type"), _g(m, "type"))
    target = f" {_g(m, 'id')}" if _g(m, "id") else ""
    status = _g(m, "st")
    return _e(f"Proxmox: {t}{target} ({_g(m, 'user')})" + (f" – {status}" if status and status.upper() != "OK" else ""),
              "system", noise=_g(m, "type") in ("termproxy", "vncproxy", "aptupdate"))


@rule("pve.replication", r"pvescheduler|pvestatd.*?(?:status update time|got timeout)")
def _(m):
    return _e("Proxmox-Hintergrunddienst meldet sich", "system", noise=True)


# --- Windows-Ereignisse (nur Text; Ereignis-IDs siehe WINDOWS_EVENTS) ---------------
WINDOWS_EVENTS: Dict[int, Explanation] = {
    4624: _e("Windows-Anmeldung erfolgreich", "auth", noise=True),
    4625: _e("Windows-Anmeldung fehlgeschlagen", "auth",
             "Viele Fehlversuche in kurzer Zeit deuten auf einen Rateangriff."),
    4634: _e("Windows-Abmeldung", "auth", noise=True),
    4647: _e("Benutzer hat sich abgemeldet", "auth", noise=True),
    4648: _e("Anmeldung mit expliziten Zugangsdaten", "auth"),
    4672: _e("Anmeldung mit Administratorrechten", "auth", noise=True),
    4688: _e("Neuer Prozess gestartet", "system", noise=True),
    4720: _e("Benutzerkonto angelegt", "security", "Prüfen, ob das gewollt war."),
    4722: _e("Benutzerkonto aktiviert", "security"),
    4723: _e("Benutzer hat das Passwort geändert", "security"),
    4724: _e("Passwort eines Kontos zurückgesetzt", "security"),
    4725: _e("Benutzerkonto deaktiviert", "security"),
    4726: _e("Benutzerkonto gelöscht", "security"),
    4728: _e("Mitglied zu globaler Sicherheitsgruppe hinzugefügt", "security"),
    4732: _e("Mitglied zu lokaler Gruppe hinzugefügt", "security",
             "Bei der Gruppe Administratoren besonders aufmerksam sein."),
    4740: _e("Benutzerkonto wegen Fehlversuchen gesperrt", "security",
             "Ursache meist ein altes gespeichertes Passwort (Handy, Laufwerk) oder ein Angriff."),
    4767: _e("Benutzerkonto entsperrt", "security"),
    4771: _e("Kerberos-Vorauthentifizierung fehlgeschlagen", "auth"),
    4776: _e("Prüfung der Anmeldedaten (NTLM)", "auth", noise=True),
    1102: _e("Sicherheitsprotokoll wurde gelöscht", "security",
             "Ungewöhnlich – kann das Verwischen von Spuren bedeuten."),
    1074: _e("Neustart/Herunterfahren angefordert", "system"),
    6005: _e("Ereignisprotokoll gestartet (Systemstart)", "system", noise=True),
    6006: _e("Ereignisprotokoll beendet (Herunterfahren)", "system", noise=True),
    6008: _e("Unerwartetes Herunterfahren (Absturz oder Stromausfall)", "system",
             "Stromversorgung, Überhitzung oder Bluescreen prüfen."),
    41: _e("System ohne sauberes Herunterfahren neu gestartet (Kernel-Power)", "system",
           "Stromausfall, Absturz oder hart ausgeschaltet."),
    7031: _e("Dienst unerwartet beendet", "service"),
    7034: _e("Dienst unerwartet beendet", "service"),
    7036: _e("Dienst hat den Zustand gewechselt", "service", noise=True),
    7040: _e("Starttyp eines Dienstes geändert", "service", noise=True),
    7045: _e("Neuer Dienst installiert", "security", "Prüfen, ob die Installation gewollt war."),
    1000: _e("Anwendung abgestürzt", "system"),
    1001: _e("Fehlerbericht erstellt", "system", noise=True),
    1014: _e("DNS-Namensauflösung fehlgeschlagen", "network"),
    10016: _e("DCOM-Berechtigungshinweis (meist harmlos)", "system", noise=True),
    10010: _e("DCOM-Server nicht rechtzeitig registriert", "system", noise=True),
    19: _e("Windows Update: Installation erfolgreich", "update"),
    20: _e("Windows Update: Installation fehlgeschlagen", "update"),
    43: _e("Windows Update: Installation gestartet", "update", noise=True),
    44: _e("Windows Update: Download gestartet", "update", noise=True),
    51: _e("Fehler beim Schreiben auf einen Datenträger", "hardware", "Platte prüfen."),
    55: _e("Dateisystem beschädigt", "storage", "chkdsk ausführen."),
    153: _e("Datenträger: Wiederholter E/A-Vorgang", "hardware"),
    5145: _e("Zugriff auf Netzwerkfreigabe geprüft", "auth", noise=True),
    8004: _e("Anmeldeversuch über NTLM erkannt", "auth", noise=True),
}


def _rules_explain(text: str) -> Optional[Explanation]:
    for name, pattern, fn in RULES:
        m = pattern.search(text)
        if not m:
            continue
        try:
            out = fn(m)
        except Exception:                                           # defensiv
            continue
        out = dict(out)
        out["rule"] = name
        return out
    return None


_SPACE = re.compile(r"\s+")


def explain(text: str, app: str = "", fields: Optional[Dict[str, str]] = None,
            event_id: Any = None, level: str = "") -> Optional[Explanation]:
    """Klartext fuer eine Meldung - oder None, wenn kein Muster passt."""
    text = _SPACE.sub(" ", (text or "")).strip()
    fields = fields or {}

    # Windows-Ereignis-ID: eindeutiger als jeder Text.
    eid = event_id if event_id is not None else (fields.get("EventID") or fields.get("event_id")
                                                 or fields.get("eventid"))
    try:
        eid = int(eid) if eid not in (None, "") else None
    except (TypeError, ValueError):
        eid = None
    if eid is not None and eid in WINDOWS_EVENTS:
        base = dict(WINDOWS_EVENTS[eid])
        user = fields.get("TargetUserName") or fields.get("SubjectUserName") or fields.get("user")
        ip = fields.get("IpAddress") or fields.get("src_ip")
        detail = []
        if user and user not in ("-", "SYSTEM"):
            detail.append(user)
        if ip and ip not in ("-", "::1", "127.0.0.1"):
            detail.append(f"von {ip}")
        if detail:
            base["title"] += " (" + " ".join(detail) + ")"
        base["rule"] = f"windows.{eid}"
        base["event_id"] = eid
        return base

    if not text:
        return None
    found = _rules_explain(text)
    if found:
        return found

    # Programmname allein sagt oft schon die Kategorie.
    category = category_for_app(app)
    if category:
        return {"title": "", "category": category, "hint": "", "noise": False, "rule": "app"}
    return None


_APP_CATEGORIES = [
    (re.compile(r"^(sshd|sudo|su|login|systemd-logind|polkitd|pam|gdm|lightdm|sssd|winbind)", re.I), "auth"),
    (re.compile(r"^(kernel|systemd-udevd|smartd|mcelog|nvme|thermald)", re.I), "hardware"),
    (re.compile(r"^(ufw|iptables|nft|firewalld|pf|filterlog|fortigate|opnsense)", re.I), "firewall"),
    (re.compile(r"^(dhcp|dnsmasq|named|unbound|hostapd|wpa_supplicant|NetworkManager|systemd-networkd|systemd-resolved|chrony|ntpd)", re.I), "network"),
    (re.compile(r"^(cron|CRON|anacron|atd|systemd-timer)", re.I), "schedule"),
    (re.compile(r"^(dockerd|containerd|docker|podman|kubelet|lxc|lxcfs)", re.I), "container"),
    (re.compile(r"^(postfix|dovecot|exim|sendmail|opendkim|rspamd)", re.I), "mail"),
    (re.compile(r"^(nginx|apache2?|httpd|caddy|traefik|haproxy)", re.I), "web"),
    (re.compile(r"^(apt|dpkg|unattended-upgrade|dnf|yum|packagekit|WindowsUpdate)", re.I), "update"),
    (re.compile(r"^(zed|zfs|mdadm|lvm|multipathd)", re.I), "storage"),
    (re.compile(r"^(pve|pvedaemon|pveproxy|pvestatd|pvescheduler|qmeventd|systemd)$", re.I), "system"),
    (re.compile(r"^(fail2ban|crowdsec|auditd|audit|clamd|wazuh)", re.I), "security"),
]


def category_for_app(app: str) -> Optional[str]:
    app = (app or "").strip()
    if not app:
        return None
    for pattern, cat in _APP_CATEGORIES:
        if pattern.search(app):
            return cat
    return None


def category_label(key: Optional[str]) -> str:
    return CATEGORIES.get(key or "other", CATEGORIES["other"])


# =============================================================================
# Vorlage einer Meldung: Zahlen, IPs, IDs raus - fuer Dubletten und Rauschen
# =============================================================================
_TPL_RULES = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I), "<mac>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d+(?:[.,]\d+)?\b"), "#"),
]


def template(message: str, limit: int = 300) -> str:
    """Die Meldung ohne die Teile, die sich bei jeder Wiederholung aendern."""
    text = (message or "")[:2000]
    for pattern, repl in _TPL_RULES:
        text = pattern.sub(repl, text)
    return _SPACE.sub(" ", text).strip()[:limit]
