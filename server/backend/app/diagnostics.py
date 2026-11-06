# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.14.12.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Selbstdiagnose: prueft das komplette System durch
# ==============================================================================
"""
Der Systemcheck. Laeuft das ganze System einmal durch und sagt, was nicht geht -
und warum.

Aufbau: Jede Pruefung ist eine kleine async-Funktion, die ein `Check`-Ergebnis
zurueckgibt. Der Laeufer ruft alle auf, faengt Fehler ab und begrenzt die
Laufzeit jeder einzelnen Pruefung. Faellt eine Pruefung aus, faellt nur sie aus -
der Rest des Berichts bleibt brauchbar.

Jedes Ergebnis beantwortet drei Fragen:
  * `summary` - was ist der Befund?
  * `detail`  - woran wurde das gemessen (Zahlen, Fehlertext)?
  * `hint`    - was ist zu tun?

Bewertung:
  ok    - alles in Ordnung
  warn  - laeuft, sollte aber angesehen werden
  fail  - kaputt, Funktion faellt aus
  info  - reine Auskunft, keine Bewertung
  skip  - nicht pruefbar (z.B. Funktion nicht eingerichtet)
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Callable, List, Optional

import httpx
import psutil
from sqlalchemy import select, func

from .auth import verify_password
from .config import settings
from .database import async_session, engine
from .models import Agent, Log, Setting, User
from . import archiving as archiving_module
from . import diskguard
from . import hostexec
from . import ldap_auth
from . import updater

logger = logging.getLogger("rimperio.diagnostics")

# Schwellen (per Umgebungsvariable anpassbar)
DISK_WARN = float(os.getenv("CHECK_DISK_WARN", "80"))
DISK_FAIL = float(os.getenv("CHECK_DISK_FAIL", "92"))
MEM_WARN = float(os.getenv("CHECK_MEMORY_WARN", "85"))
MEM_FAIL = float(os.getenv("CHECK_MEMORY_FAIL", "95"))
CPU_WARN = float(os.getenv("CHECK_CPU_WARN", "85"))
CPU_FAIL = float(os.getenv("CHECK_CPU_FAIL", "95"))
LOG_SILENCE_WARN_HOURS = float(os.getenv("CHECK_LOG_SILENCE_HOURS", "24"))
DB_LATENCY_WARN_MS = float(os.getenv("CHECK_DB_LATENCY_WARN_MS", "250"))

# Zeitlimit je Pruefung. Grosszuegig genug fuer die Update-Pruefung (Host-Abfrage
# plus GitHub), damit sie nicht faelschlich als "haengt" gemeldet wird.
CHECK_TIMEOUT = float(os.getenv("CHECK_TIMEOUT_SECONDS", "30"))

# Kategorien in der Reihenfolge, in der sie angezeigt werden.
CATEGORIES = ["Kern", "Dienste", "Sicherheit", "Betrieb", "Netz & Updates"]


# =============================================================================
# Ergebnis-Objekt
# =============================================================================
@dataclass
class Check:
    id: str
    title: str
    category: str
    status: str = "ok"
    summary: str = ""
    detail: str = ""
    hint: str = ""
    duration_ms: float = 0.0
    values: dict = field(default_factory=dict)


def _ok(check_id, title, category, summary, detail="", **values) -> Check:
    return Check(check_id, title, category, "ok", summary, detail, "", 0.0, values)


def _warn(check_id, title, category, summary, detail="", hint="", **values) -> Check:
    return Check(check_id, title, category, "warn", summary, detail, hint, 0.0, values)


def _fail(check_id, title, category, summary, detail="", hint="", **values) -> Check:
    return Check(check_id, title, category, "fail", summary, detail, hint, 0.0, values)


def _info(check_id, title, category, summary, detail="", **values) -> Check:
    return Check(check_id, title, category, "info", summary, detail, "", 0.0, values)


def _skip(check_id, title, category, summary, detail="") -> Check:
    return Check(check_id, title, category, "skip", summary, detail, "", 0.0, {})


# =============================================================================
# Kleine Helfer
# =============================================================================
async def _sql_scalar(sql: str, timeout: float = 8.0):
    """Einzelwert lesen - auf eigener Verbindung, damit ein Fehler nichts vergiftet."""
    async def run():
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            result = await conn.exec_driver_sql(sql)
            return result.scalar()
    return await asyncio.wait_for(run(), timeout=timeout)


async def _tcp_reachable(host: str, port: int, timeout: float = 4.0) -> Optional[str]:
    """None = erreichbar, sonst der Fehlertext."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return None
    except asyncio.TimeoutError:
        return f"Zeitueberschreitung nach {timeout:.0f}s"
    except Exception as exc:
        return str(exc)


def _db_host_port() -> tuple:
    from urllib.parse import urlsplit
    split = urlsplit(settings.database_url)
    return (split.hostname or settings.db_host, split.port or settings.db_port)


# =============================================================================
# Kern
# =============================================================================
async def check_backend() -> Check:
    process = psutil.Process()
    started = datetime.fromtimestamp(process.create_time())
    uptime = datetime.now() - started
    return _ok(
        "backend", "Backend-Dienst", "Kern",
        f"Laeuft, Version {settings.app_version}",
        f"Gestartet am {started.strftime('%d.%m.%Y %H:%M')} Uhr, seit {_human_delta(uptime)} online.",
        version=settings.app_version, uptime_seconds=int(uptime.total_seconds()),
    )


async def check_database() -> Check:
    host, port = _db_host_port()
    started = time.perf_counter()
    try:
        version = await _sql_scalar("SHOW server_version")
    except Exception as exc:
        return _fail(
            "database", "Datenbank-Verbindung", "Kern",
            "Die Datenbank antwortet nicht",
            f"Ziel {host}:{port} - Fehler: {exc}",
            "Laeuft der Datenbank-Container? Pruefe `docker compose ps` und die Zugangsdaten "
            "(DB_HOST/DB_PASSWORD bzw. DATABASE_URL) in der .env.",
            host=host, port=port,
        )

    latency = round((time.perf_counter() - started) * 1000, 1)
    detail = f"PostgreSQL {version} auf {host}:{port}, Antwortzeit {latency} ms."
    if latency > DB_LATENCY_WARN_MS:
        return _warn(
            "database", "Datenbank-Verbindung", "Kern",
            f"Antwortet langsam ({latency} ms)", detail,
            "Traege Antworten deuten auf eine ausgelastete Platte oder eine ueberlastete "
            "Datenbank hin. Speicherplatz und Log-Menge pruefen.",
            latency_ms=latency, server_version=str(version),
        )
    return _ok(
        "database", "Datenbank-Verbindung", "Kern",
        f"Verbunden ({latency} ms)", detail,
        latency_ms=latency, server_version=str(version),
    )


# Tabellen und Spalten, ohne die einzelne Funktionen ausfallen.
REQUIRED_TABLES = {
    "users": "Benutzerverwaltung und Anmeldung",
    "logs": "Logspeicher",
    "agents": "Geraeteverwaltung",
    "agent_tokens": "Anmeldung der Agenten",
    "webhooks": "Abholung durch externe Werkzeuge",
    "settings": "Einstellungen, LDAP, Archivierung",
    "app_login_tokens": "Anmeldung per QR-Code",
    "mfa_backup_codes": "Backup-Codes der Zwei-Faktor-Anmeldung",
    "webauthn_credentials": "Passkeys",
}

REQUIRED_COLUMNS = {
    ("logs", "dedup_key"): "Doppelte Eintraege beim HTTPS-Empfang erkennen",
    ("agents", "retention_days"): "Aufbewahrung je Geraet",
    ("agents", "retention_max_logs"): "Aufbewahrung je Geraet",
    ("users", "auth_source"): "Unterscheidung lokales Konto / Verzeichnis",
    ("users", "mfa_enabled"): "Zwei-Faktor-Anmeldung",
    ("agent_tokens", "device_type"): "Unterscheidung Linux-/Windows-Agent",
}


async def check_database_schema() -> Check:
    try:
        rows = await _get_rows(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
    except Exception as exc:
        return _fail(
            "db_schema", "Datenbank-Schema", "Kern",
            "Schema nicht lesbar", str(exc),
            "Ohne Verbindung zur Datenbank ist keine Aussage moeglich - zuerst die "
            "Datenbank-Verbindung in Ordnung bringen.",
        )

    present_tables = {row[0] for row in rows}
    present_columns = {(row[0], row[1]) for row in rows}

    missing_tables = [name for name in REQUIRED_TABLES if name not in present_tables]
    missing_columns = [key for key in REQUIRED_COLUMNS if key[0] in present_tables and key not in present_columns]

    if missing_tables:
        return _fail(
            "db_schema", "Datenbank-Schema", "Kern",
            f"{len(missing_tables)} Tabelle(n) fehlen",
            "Fehlend: " + ", ".join(f"{t} ({REQUIRED_TABLES[t]})" for t in missing_tables),
            "Die Tabellen werden beim Start des Backends angelegt. Backend neu starten "
            "(`docker compose restart backend`) und danach dessen Protokoll ansehen.",
            missing_tables=missing_tables,
        )
    if missing_columns:
        return _warn(
            "db_schema", "Datenbank-Schema", "Kern",
            f"{len(missing_columns)} Spalte(n) aus neueren Versionen fehlen",
            "Fehlend: " + ", ".join(f"{t}.{c} ({REQUIRED_COLUMNS[(t, c)]})" for t, c in missing_columns),
            "Diese Spalten werden beim Start des Backends nachgezogen. Backend neu starten; "
            "bleibt es dabei, steht der Grund im Backend-Protokoll.",
            missing_columns=[f"{t}.{c}" for t, c in missing_columns],
        )
    return _ok(
        "db_schema", "Datenbank-Schema", "Kern",
        "Vollstaendig",
        f"{len(present_tables)} Tabellen vorhanden, alle erwarteten Spalten da.",
        tables=len(present_tables),
    )


async def check_database_indexes() -> Check:
    expected = {
        "idx_logs_facility": "Filter nach Logtyp in der Log-Ansicht",
        "idx_logs_dedup_key": "Duplikatpruefung beim HTTPS-Empfang",
    }
    try:
        rows = await _get_rows(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        )
    except Exception as exc:
        return _skip("db_indexes", "Datenbank-Indizes", "Kern", "Nicht pruefbar", str(exc))

    present = {row[0] for row in rows}
    missing = [name for name in expected if name not in present]
    if missing:
        return _warn(
            "db_indexes", "Datenbank-Indizes", "Kern",
            f"{len(missing)} Index/Indizes fehlen",
            "Fehlend: " + ", ".join(f"{n} ({expected[n]})" for n in missing),
            "Ohne diese Indizes laufen die betroffenen Abfragen langsam. Sie werden beim "
            "Start des Backends angelegt - das kann bei vielen Logs eine Weile dauern.",
            missing=missing,
        )
    return _ok("db_indexes", "Datenbank-Indizes", "Kern", "Vorhanden",
               f"{len(present)} Indizes im Schema, die wichtigen sind dabei.")


async def check_disk() -> Check:
    """Wie voll ist die Platte - und zwar so gerechnet, wie `df` es tut.

    Die frueher hier benutzte Rechnung `used/total` zaehlt die fuer root
    reservierten Bloecke als frei und meldet deshalb zu wenig: eine Platte, die
    `df` mit 95 % ausweist, stand hier mit 90 %. Genau in diesem Bereich
    entscheidet sich aber, ob der Waechter rechtzeitig anfaengt.
    """
    info = diskguard.usage(os.getenv("DISK_MONITOR_PATH", "/"))
    percent = info["percent"]
    free_gb = info["free_bytes"] / 1024 ** 3
    detail = (f"{percent:.1f} % belegt, {free_gb:.1f} GB frei von "
              f"{info['total_bytes'] / 1024 ** 3:.1f} GB.")
    hint = (f"Der Plattenwächter räumt ab {diskguard.THRESHOLD_WARN:.0f} % von selbst auf "
            f"und hält die Belegung Richtung {diskguard.TARGET_USAGE:.0f} %. Die letzten "
            f"{diskguard.MIN_KEEP_HOURS} h Logs bleiben dabei immer stehen. Wer mehr Luft "
            f"will: Aufbewahrung verkürzen (Einstellungen → Aufbewahrung) oder "
            f"Archivierung einschalten.")
    if percent >= DISK_FAIL:
        return _fail("disk", "Speicherplatz", "Kern", f"Kritisch voll ({percent:.1f} %)",
                     detail, hint, percent=round(percent, 1), free_gb=round(free_gb, 1))
    if percent >= DISK_WARN:
        return _warn("disk", "Speicherplatz", "Kern", f"Wird eng ({percent:.1f} %)",
                     detail, hint, percent=round(percent, 1), free_gb=round(free_gb, 1))
    return _ok("disk", "Speicherplatz", "Kern", f"{percent:.1f} % belegt", detail,
               percent=round(percent, 1), free_gb=round(free_gb, 1))


async def check_memory() -> Check:
    memory = psutil.virtual_memory()
    detail = (f"{memory.percent:.1f} % belegt, {memory.available / 1024 ** 3:.1f} GB frei "
              f"von {memory.total / 1024 ** 3:.1f} GB.")
    hint = ("Wenig Arbeitsspeicher fuehrt dazu, dass der Kernel Container beendet. "
            "Weniger gleichzeitige Abfragen, kleinere Aufbewahrung oder mehr RAM.")
    if memory.percent >= MEM_FAIL:
        return _fail("memory", "Arbeitsspeicher", "Kern", f"Kritisch ({memory.percent:.1f} %)",
                     detail, hint, percent=memory.percent)
    if memory.percent >= MEM_WARN:
        return _warn("memory", "Arbeitsspeicher", "Kern", f"Hoch ({memory.percent:.1f} %)",
                     detail, hint, percent=memory.percent)
    return _ok("memory", "Arbeitsspeicher", "Kern", f"{memory.percent:.1f} % belegt", detail,
               percent=memory.percent)


async def check_cpu() -> Check:
    # Kurz messen statt seit Systemstart mitteln.
    psutil.cpu_percent(interval=None)
    await asyncio.sleep(0.4)
    percent = psutil.cpu_percent(interval=None)
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    detail = (f"{percent:.1f} % Auslastung, Lastmittel {load[0]:.2f} / {load[1]:.2f} / "
              f"{load[2]:.2f} bei {psutil.cpu_count() or '?'} Kernen.")
    if percent >= CPU_FAIL:
        return _fail("cpu", "CPU-Auslastung", "Kern", f"Dauerlast ({percent:.1f} %)", detail,
                     "Meist laeuft gerade eine grosse Abfrage oder ein Aufraeumlauf. Haelt es an, "
                     "die Log-Menge und die Aufbewahrung pruefen.", percent=percent)
    if percent >= CPU_WARN:
        return _warn("cpu", "CPU-Auslastung", "Kern", f"Hoch ({percent:.1f} %)", detail,
                     "Kurzzeitig unkritisch. Haelt es an, die Log-Menge pruefen.", percent=percent)
    return _ok("cpu", "CPU-Auslastung", "Kern", f"{percent:.1f} %", detail, percent=percent)


# =============================================================================
# Dienste
# =============================================================================
async def check_frontend() -> Check:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://frontend:80/")
        if response.status_code < 400:
            return _ok("frontend", "Weboberflaeche", "Dienste", "Wird ausgeliefert",
                       f"Der Frontend-Container antwortet mit HTTP {response.status_code}.")
        return _fail("frontend", "Weboberflaeche", "Dienste",
                     f"Antwortet mit HTTP {response.status_code}",
                     "Der Frontend-Container laeuft, liefert aber einen Fehler.",
                     "Protokoll ansehen: `docker compose logs frontend`.")
    except Exception as exc:
        return _fail("frontend", "Weboberflaeche", "Dienste", "Nicht erreichbar",
                     f"http://frontend:80/ - {exc}",
                     "Der Frontend-Container laeuft nicht oder ist nicht im selben Docker-Netz. "
                     "`docker compose ps` und `docker compose logs frontend` pruefen.")


async def check_caddy() -> Check:
    admin_url = settings.caddy_admin_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{admin_url}/config/")
            response.raise_for_status()
            config = response.json() or {}
    except Exception as exc:
        return _fail("caddy", "Reverse Proxy (Caddy)", "Dienste", "Nicht erreichbar",
                     f"{admin_url} - {exc}",
                     "Ohne Caddy ist die Oberflaeche nur ueber den Container-Port erreichbar. "
                     "`docker compose ps caddy` und `docker compose logs caddy` pruefen.")

    servers = ((config.get("apps") or {}).get("http") or {}).get("servers") or {}
    listen = []
    for server in servers.values():
        listen.extend(server.get("listen") or [])
    return _ok("caddy", "Reverse Proxy (Caddy)", "Dienste", "Laeuft",
               f"Aktive Konfiguration geladen, lauscht auf: {', '.join(listen) or 'unbekannt'}.",
               listen=listen)


async def check_syslog() -> Check:
    error = await _tcp_reachable("syslog", 514, timeout=4.0)
    if error is None:
        return _ok("syslog", "Syslog-Empfaenger", "Dienste", "Nimmt Verbindungen an",
                   "Port 514/TCP im Container-Netz erreichbar (UDP wird gleichzeitig bedient).")
    return _warn("syslog", "Syslog-Empfaenger", "Dienste", "Nicht erreichbar",
                 f"syslog:514 - {error}",
                 "Geraete, die per Syslog senden (Switches, Firewalls, Linux-Hosts), kommen "
                 "gerade nicht durch. `docker compose ps syslog` pruefen. Agenten, die per "
                 "HTTPS liefern, sind davon nicht betroffen.")


async def check_containers() -> Check:
    if not hostexec.nsenter_available():
        return _skip("containers", "Container", "Dienste", "Kein Zugriff auf den Host",
                     "Ohne privileged/pid:host kann der Container die anderen Container "
                     "nicht abfragen. Die Dienste werden stattdessen einzeln geprueft.")

    result = await hostexec.run_host(
        ["docker", "ps", "-a", "--filter", "name=rimperio-",
         "--format", "{{.Names}}|{{.State}}|{{.Status}}"],
        timeout=12.0,
    )
    if not result.ok:
        return _warn("containers", "Container", "Dienste", "Zustand nicht abfragbar",
                     (result.error or result.stderr or "Unbekannter Fehler").strip(),
                     "Laeuft Docker auf dem Host? `docker ps` von Hand versuchen.")

    entries, unhealthy = [], []
    for line in hostexec.split_lines(result.stdout, limit=50):
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name, state, status = parts[0], parts[1], parts[2]
        entries.append({"name": name, "state": state, "status": status})
        # Aeltere Docker-Fassungen kennen {{.State}} nicht - dann bleibt das Feld
        # leer und der Status ("Up 3 hours") muss die Auskunft geben.
        running = state.lower() == "running" if state.strip() else status.lower().startswith("up")
        if not running or "unhealthy" in status.lower():
            unhealthy.append(f"{name}: {status or 'Zustand unbekannt'}")

    if not entries:
        return _warn("containers", "Container", "Dienste", "Keine RIMPERIO-Container gefunden",
                     "`docker ps` liefert keine Container mit dem Namensmuster rimperio-.",
                     "Laeuft der Stack unter einem anderen Namen oder auf einem anderen Host?")
    if unhealthy:
        return _fail("containers", "Container", "Dienste",
                     f"{len(unhealthy)} von {len(entries)} Containern laufen nicht",
                     "; ".join(unhealthy),
                     "Protokoll des betroffenen Containers ansehen: "
                     "`docker compose logs <name>`.", containers=entries)
    return _ok("containers", "Container", "Dienste", f"Alle {len(entries)} laufen",
               ", ".join(e["name"] for e in entries), containers=entries)


# =============================================================================
# Sicherheit
# =============================================================================
async def check_jwt_secret() -> Check:
    secret = settings.jwt_secret or ""
    if secret == "change-me":
        return _fail("jwt_secret", "Sitzungsschluessel", "Sicherheit",
                     "Noch der Standardwert",
                     "JWT_SECRET steht auf 'change-me'. Damit kann sich jeder, der diesen "
                     "Wert kennt, gueltige Anmelde-Token ausstellen.",
                     "In der .env einen langen Zufallswert setzen "
                     "(`openssl rand -base64 48`) und die Container neu starten.")
    if len(secret) < 32:
        return _warn("jwt_secret", "Sitzungsschluessel", "Sicherheit",
                     f"Sehr kurz ({len(secret)} Zeichen)",
                     "Kurze Schluessel lassen sich raten.",
                     "JWT_SECRET in der .env auf mindestens 32 zufaellige Zeichen aendern.")
    return _ok("jwt_secret", "Sitzungsschluessel", "Sicherheit", "Eigener Wert gesetzt",
               f"JWT_SECRET ist {len(secret)} Zeichen lang.")


async def check_admin_password() -> Check:
    try:
        async with async_session() as session:
            users = (await session.execute(
                select(User).where(User.role == "admin", User.is_active == True)  # noqa: E712
            )).scalars().all()
    except Exception as exc:
        return _skip("admin_password", "Administrator-Konten", "Sicherheit",
                     "Nicht pruefbar", str(exc))

    if not users:
        return _fail("admin_password", "Administrator-Konten", "Sicherheit",
                     "Kein aktiver Administrator",
                     "In der Benutzertabelle steht kein aktives Konto mit der Rolle admin.",
                     "Ohne Administrator lassen sich Einstellungen nicht mehr aendern. "
                     "Konto in der Datenbank wieder aktivieren.")

    weak = []
    for user in users:
        if (user.auth_source or "local") != "local" or not user.password_hash:
            continue
        for candidate in ("admin", "password", "rimperio"):
            try:
                if await asyncio.to_thread(verify_password, candidate, user.password_hash):
                    weak.append(user.username)
                    break
            except Exception:
                break

    if weak:
        return _fail("admin_password", "Administrator-Konten", "Sicherheit",
                     "Standardpasswort noch aktiv",
                     f"Betroffen: {', '.join(weak)}. Diese Zugangsdaten stehen in jeder Anleitung.",
                     "Passwort sofort aendern: Einstellungen -> Passwort. "
                     "Zusaetzlich Zwei-Faktor oder einen Passkey einrichten.",
                     users=weak)
    return _ok("admin_password", "Administrator-Konten", "Sicherheit",
               f"{len(users)} Administrator(en), kein Standardpasswort",
               "Es wurde gegen die bekannten Standardpasswoerter geprueft.")


async def check_https() -> Check:
    admin_url = settings.caddy_admin_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{admin_url}/config/")
            response.raise_for_status()
            config = response.json() or {}
    except Exception:
        return _skip("https", "Verschluesselte Verbindung", "Sicherheit",
                     "Nicht pruefbar", "Die Caddy-Konfiguration ist gerade nicht lesbar.")

    servers = ((config.get("apps") or {}).get("http") or {}).get("servers") or {}
    https_ports = []
    for server in servers.values():
        for address in server.get("listen") or []:
            if address.endswith(":443") or ":443" in address:
                https_ports.append(address)

    if https_ports:
        return _ok("https", "Verschluesselte Verbindung", "Sicherheit", "HTTPS ist aktiv",
                   f"Caddy nimmt Verbindungen auf {', '.join(https_ports)} an.")
    return _warn("https", "Verschluesselte Verbindung", "Sicherheit", "Nur HTTP",
                 "Caddy lauscht nicht auf Port 443. Anmeldedaten und Logs laufen im Klartext "
                 "durchs Netz.",
                 "Einstellungen -> Netzwerk -> Reverse Proxy: HTTPS einschalten "
                 "(Let's Encrypt, eigenes Zertifikat oder selbstsigniert).")


async def check_database_tls() -> Check:
    if not settings.database_is_external:
        return _ok("db_tls", "Datenbank-Verschluesselung", "Sicherheit",
                   "Nicht noetig (Datenbank im selben Stack)",
                   "Die Datenbank laeuft als Container im internen Docker-Netz und ist von "
                   "aussen nicht erreichbar.")
    if (settings.db_sslmode or "").strip():
        return _ok("db_tls", "Datenbank-Verschluesselung", "Sicherheit",
                   f"Aktiv (Modus {settings.db_sslmode})",
                   "Die Verbindung zur externen Datenbank ist verschluesselt.")
    return _fail("db_tls", "Datenbank-Verschluesselung", "Sicherheit",
                 "Externe Datenbank ohne TLS",
                 "Zugangsdaten und Logs laufen unverschluesselt zum Datenbankserver.",
                 "In der .env `DB_SSLMODE=require` setzen (besser: verify-full) und die "
                 "Container neu starten.")


# =============================================================================
# Betrieb
# =============================================================================
async def check_log_flow() -> Check:
    try:
        async with async_session() as session:
            newest = (await session.execute(select(func.max(Log.timestamp)))).scalar()
            last_hour = (await session.execute(
                select(func.count(Log.id)).where(
                    Log.timestamp >= datetime.utcnow() - timedelta(hours=1))
            )).scalar() or 0
    except Exception as exc:
        return _skip("log_flow", "Logeingang", "Betrieb", "Nicht pruefbar", str(exc))

    if newest is None:
        return _warn("log_flow", "Logeingang", "Betrieb", "Noch kein einziger Log",
                     "Die Tabelle logs ist leer.",
                     "Sendet schon ein Geraet? Agent installieren (Bereich Geraete) oder ein "
                     "Geraet auf Syslog-Ziel Port 514 einstellen.")

    age = datetime.utcnow() - newest
    detail = (f"Neuester Eintrag von {newest.strftime('%d.%m.%Y %H:%M')} Uhr (UTC), "
              f"{last_hour} Eintraege in der letzten Stunde.")
    if age > timedelta(hours=LOG_SILENCE_WARN_HOURS):
        return _warn("log_flow", "Logeingang", "Betrieb",
                     f"Seit {_human_delta(age)} nichts angekommen", detail,
                     "Entweder sendet gerade niemand, oder der Empfang haengt. Syslog-Port 514 "
                     "und die Agenten-Token pruefen (Bereich Geraete).",
                     last_hour=last_hour)
    return _ok("log_flow", "Logeingang", "Betrieb", f"Zuletzt vor {_human_delta(age)}", detail,
               last_hour=last_hour)


async def check_agents() -> Check:
    try:
        async with async_session() as session:
            total = (await session.execute(select(func.count(Agent.id)))).scalar() or 0
            if not total:
                return _info("agents", "Geraete", "Betrieb", "Noch keine Geraete erfasst",
                             "Sobald ein Agent oder ein Syslog-Sender liefert, taucht das "
                             "Geraet hier auf.")
            online = (await session.execute(
                select(func.count(Agent.id)).where(
                    Agent.last_seen >= datetime.utcnow() - timedelta(minutes=15))
            )).scalar() or 0
            stale = (await session.execute(
                select(Agent.hostname).where(
                    Agent.last_seen < datetime.utcnow() - timedelta(days=7)).limit(10)
            )).scalars().all()
    except Exception as exc:
        return _skip("agents", "Geraete", "Betrieb", "Nicht pruefbar", str(exc))

    detail = f"{online} von {total} Geraeten haben in den letzten 15 Minuten geliefert."
    if stale:
        return _warn("agents", "Geraete", "Betrieb",
                     f"{len(stale)} Geraet(e) seit ueber einer Woche still",
                     detail + " Lange still: " + ", ".join(stale),
                     "Entweder sind die Geraete aus, oder der Agent laeuft nicht mehr. "
                     "Nicht mehr genutzte Geraete koennen im Bereich Geraete entfernt werden.",
                     total=total, online=online)
    return _ok("agents", "Geraete", "Betrieb", f"{online} von {total} online", detail,
               total=total, online=online)


async def check_retention() -> Check:
    try:
        async with async_session() as session:
            setting = (await session.execute(
                select(Setting).where(Setting.key == "log_retention_days")
            )).scalar_one_or_none()
            rows = await _sql_scalar(
                "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
            ) or 0
    except Exception as exc:
        return _skip("retention", "Aufbewahrung", "Betrieb", "Nicht pruefbar", str(exc))

    if not setting or not str(setting.value).strip():
        return _warn("retention", "Aufbewahrung", "Betrieb", "Keine Aufbewahrung eingestellt",
                     f"Aktuell liegen etwa {_num(rows)} Zeilen in der Tabelle logs. "
                     f"Ohne Grenze waechst sie, bis die Platte voll ist.",
                     "Einstellungen -> Aufbewahrung: eine Anzahl Tage setzen. "
                     "Alternativ die Archivierung einschalten.")
    return _ok("retention", "Aufbewahrung", "Betrieb", f"{setting.value} Tage",
               f"Etwa {_num(rows)} Zeilen in der Tabelle logs.",
               days=setting.value)


async def check_archiving() -> Check:
    try:
        async with async_session() as session:
            config = await archiving_module.load_config(session)
            history = await archiving_module.load_history(session)
    except Exception as exc:
        return _skip("archiving", "Archivierung", "Betrieb", "Nicht pruefbar", str(exc))

    if not config.get("enabled"):
        return _skip("archiving", "Archivierung", "Betrieb", "Nicht eingeschaltet",
                     "Alte Logs werden nicht ausgelagert. Das ist in Ordnung, solange die "
                     "Aufbewahrung begrenzt ist.")

    target = f"{config.get('protocol', '?')}://{config.get('host', '')}{config.get('remote_path', '')}"
    last = history[0] if history else None
    if last and not last.get("success", True):
        return _fail("archiving", "Archivierung", "Betrieb", "Letzter Lauf fehlgeschlagen",
                     f"Ziel {target}. Meldung: {last.get('message', 'ohne Angabe')}",
                     "Einstellungen -> Archivierung: Zugangsdaten und Zielpfad pruefen und "
                     "einen Testlauf starten.")
    return _ok("archiving", "Archivierung", "Betrieb", "Eingeschaltet",
               f"Ziel {target}, geplant taeglich um {config.get('schedule_hour', '?')} Uhr."
               + (f" Letzter Lauf: {last.get('message', '')}" if last else ""))


async def check_ldap() -> Check:
    try:
        async with async_session() as session:
            config = await ldap_auth.load_config(session)
    except Exception as exc:
        return _skip("ldap", "Verzeichnis (LDAP)", "Betrieb", "Nicht pruefbar", str(exc))

    if not config.get("enabled"):
        return _skip("ldap", "Verzeichnis (LDAP)", "Betrieb", "Nicht eingeschaltet",
                     "Angemeldet wird nur mit lokalen Konten - fuer den privaten Einsatz "
                     "der Normalfall.")

    uri = (config.get("server_uri") or "").strip()
    if not uri:
        return _fail("ldap", "Verzeichnis (LDAP)", "Betrieb", "Eingeschaltet, aber ohne Server",
                     "In den Einstellungen ist keine Server-Adresse hinterlegt.",
                     "Einstellungen -> Verzeichnis: Server-Adresse eintragen oder die "
                     "Anmeldung ueber das Verzeichnis ausschalten.")

    host, port = _split_ldap_uri(uri)
    error = await _tcp_reachable(host, port, timeout=5.0)
    if error:
        return _fail("ldap", "Verzeichnis (LDAP)", "Betrieb", "Server nicht erreichbar",
                     f"{host}:{port} - {error}",
                     "Solange der Verzeichnisdienst nicht antwortet, koennen sich nur lokale "
                     "Konten anmelden. Adresse, Firewall und DNS pruefen.")
    return _ok("ldap", "Verzeichnis (LDAP)", "Betrieb", "Server erreichbar",
               f"{host}:{port} nimmt Verbindungen an. Ob Anmeldungen klappen, zeigt der "
               "Testknopf in den Einstellungen.")


# =============================================================================
# Netz & Updates
# =============================================================================
async def check_dns() -> Check:
    names = ["github.com"]
    results, errors = [], []
    for name in names:
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(name, None), timeout=6.0)
            addresses = sorted({info[4][0] for info in infos})
            results.append(f"{name} -> {', '.join(addresses[:3])}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if errors:
        return _fail("dns", "Namensaufloesung", "Netz & Updates", "DNS antwortet nicht",
                     "; ".join(errors),
                     "Ohne DNS gibt es keine Update-Pruefung und keine Zertifikate von "
                     "Let's Encrypt. Einstellungen -> Netzwerk -> DNS pruefen.")
    return _ok("dns", "Namensaufloesung", "Netz & Updates", "Funktioniert", "; ".join(results))


async def check_internet() -> Check:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get("https://api.github.com/rate_limit",
                                        headers={"User-Agent": "RIMPERIO-Diagnose"})
        if response.status_code >= 500:
            return _warn("internet", "Verbindung zu GitHub", "Netz & Updates",
                         f"GitHub antwortet mit HTTP {response.status_code}",
                         "Vermutlich eine Stoerung bei GitHub.",
                         "Spaeter erneut versuchen - der Betrieb von RIMPERIO ist davon nicht "
                         "betroffen, nur die Update-Pruefung.")
        return _ok("internet", "Verbindung zu GitHub", "Netz & Updates", "Erreichbar",
                   f"api.github.com antwortet mit HTTP {response.status_code}.")
    except Exception as exc:
        return _warn("internet", "Verbindung zu GitHub", "Netz & Updates", "Nicht erreichbar",
                     str(exc),
                     "Ohne Internetzugang kann nicht auf Updates geprueft werden. Der "
                     "laufende Betrieb ist davon nicht betroffen.")


async def check_update() -> Check:
    try:
        # Schlanker Weg ohne Sicherungsliste - der Systemcheck hat ein Zeitlimit.
        status = await updater.version_check()
    except Exception as exc:
        return _skip("update", "Aktualitaet", "Netz & Updates", "Nicht pruefbar", str(exc))

    local, remote = status["local"], status["remote"]
    if not remote.get("reachable"):
        return _skip("update", "Aktualitaet", "Netz & Updates", "Stand unbekannt",
                     remote.get("error") or "GitHub war nicht erreichbar.")
    if status["update_available"]:
        return _warn("update", "Aktualitaet", "Netz & Updates", "Update verfuegbar",
                     f"Installiert: {local.get('version')} ({local.get('commit_short') or 'ohne Git'}), "
                     f"auf GitHub: {remote.get('version') or remote.get('commit_short')} "
                     f"vom {_short_date(remote.get('commit_date'))}.",
                     "Bereich System -> Updates: dort das Update einspielen. Vorher die "
                     "Hinweise zum Datenbestand lesen.",
                     update_available=True)
    return _ok("update", "Aktualitaet", "Netz & Updates", "Aktueller Stand",
               f"Installiert ist {local.get('version')} - das entspricht dem Stand auf GitHub.")


async def check_host_access() -> Check:
    if not hostexec.nsenter_available():
        return _warn("host_access", "Zugriff auf den Host", "Netz & Updates",
                     "Nicht moeglich",
                     "Der Backend-Container laeuft ohne privileged/pid:host.",
                     "Update und Neustart ueber die Oberflaeche funktionieren dann nicht. "
                     "Beides geht weiter ueber die Kommandozeile. Gewollt? Dann ist diese "
                     "Meldung in Ordnung - sie ist der Preis fuer die engeren Rechte.")

    result = await hostexec.run_host(["docker", "version", "--format", "{{.Server.Version}}"],
                                     timeout=8.0)
    if not result.ok:
        return _warn("host_access", "Zugriff auf den Host", "Netz & Updates",
                     "Docker auf dem Host nicht ansprechbar",
                     (result.error or result.stderr or "Unbekannter Fehler").strip(),
                     "Ohne Docker-Zugriff kann die Oberflaeche kein Update einspielen.")

    systemd = await hostexec.run_host(["sh", "-c", "command -v systemd-run"], timeout=6.0)
    note = ("Updates werden ueber systemd gestartet und ueberleben damit den Neubau der "
            "Container." if systemd.ok else
            "Achtung: systemd-run fehlt. Das Update laeuft dann als Hintergrundprozess und "
            "kann beim Neubau der Container abbrechen.")
    return _ok("host_access", "Zugriff auf den Host", "Netz & Updates", "Vorhanden",
               f"Docker {result.output} auf dem Host. {note}")


# =============================================================================
# Laeufer
# =============================================================================
ALL_CHECKS: List[Callable] = [
    check_backend,
    check_database,
    check_database_schema,
    check_database_indexes,
    check_disk,
    check_memory,
    check_cpu,
    check_frontend,
    check_caddy,
    check_syslog,
    check_containers,
    check_jwt_secret,
    check_admin_password,
    check_https,
    check_database_tls,
    check_log_flow,
    check_agents,
    check_retention,
    check_archiving,
    check_ldap,
    check_dns,
    check_internet,
    check_update,
    check_host_access,
]


async def _run_one(func: Callable) -> Check:
    started = time.perf_counter()
    name = func.__name__.replace("check_", "")
    try:
        check = await asyncio.wait_for(func(), timeout=CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        check = _fail(name, name.replace("_", " ").title(), "Kern",
                      "Pruefung dauerte zu lange",
                      f"Abbruch nach {CHECK_TIMEOUT:.0f} Sekunden.",
                      "Das System ist gerade stark ausgelastet oder ein Dienst haengt.")
    except Exception as exc:
        logger.exception("Pruefung %s fehlgeschlagen", name)
        check = _fail(name, name.replace("_", " ").title(), "Kern",
                      "Pruefung selbst fehlgeschlagen", f"{type(exc).__name__}: {exc}",
                      "Das ist ein Fehler in der Diagnose, nicht zwingend im System. "
                      "Der Eintrag steht im Backend-Protokoll.")
    check.duration_ms = round((time.perf_counter() - started) * 1000, 1)
    return check


async def run_all() -> dict:
    """Fuehrt alle Pruefungen aus und fasst das Ergebnis zusammen."""
    started = time.perf_counter()
    # Nacheinander statt parallel: die Messungen (CPU, Antwortzeiten) sollen sich
    # nicht gegenseitig verfaelschen, und der Bericht ist so reproduzierbar.
    checks = [await _run_one(func) for func in ALL_CHECKS]

    counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0, "skip": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1

    if counts["fail"]:
        overall, headline = "fail", f"{counts['fail']} Problem(e) gefunden"
    elif counts["warn"]:
        overall, headline = "warn", f"{counts['warn']} Hinweis(e) - der Betrieb laeuft"
    else:
        overall, headline = "ok", "Alles in Ordnung"

    return {
        "overall": overall,
        "headline": headline,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "version": settings.app_version,
        "counts": counts,
        "categories": CATEGORIES,
        "checks": [asdict(check) for check in checks],
    }


# =============================================================================
# Kleinkram
# =============================================================================
async def _get_rows(sql: str, timeout: float = 10.0) -> list:
    async def run():
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            result = await conn.exec_driver_sql(sql)
            return result.fetchall()
    return await asyncio.wait_for(run(), timeout=timeout)


def _num(value) -> str:
    """Zahl mit Punkt als Tausendertrennzeichen."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def _human_delta(delta: timedelta) -> str:
    seconds = int(abs(delta.total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} Tag(en), {hours} Std."
    if hours:
        return f"{hours} Std., {minutes} Min."
    if minutes:
        return f"{minutes} Min."
    return f"{seconds} Sek."


def _short_date(value: Optional[str]) -> str:
    if not value:
        return "unbekanntem Datum"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except Exception:
        return value


def _split_ldap_uri(uri: str) -> tuple:
    """ldap://host:389 oder ldaps://host -> (host, port)"""
    raw = uri.strip()
    secure = raw.lower().startswith("ldaps://")
    for prefix in ("ldaps://", "ldap://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.split("/")[0]
    if ":" in raw:
        host, _, port = raw.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return raw, 636 if secure else 389
    return raw, 636 if secure else 389
