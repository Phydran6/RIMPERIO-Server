# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Datenbank in der Oberflaeche umstellen: eingebaut <->
#               externer PostgreSQL-Server (testen, Daten uebernehmen, umschalten)
# ==============================================================================
"""
Eingebaute Datenbank oder eigener Datenbankserver - umstellbar im Betrieb.

Die Installation nimmt immer die eingebaute Datenbank (Container
``postgres``), damit das System sofort laeuft. Spaeter laesst sich auf einen
dedizierten PostgreSQL-Server umziehen - und wieder zurueck.

Ablauf beim Umzug (laeuft auf dem Host, losgeloest vom Container, weil das
Backend dabei neu startet - wie beim Update):

1. Test vorher (hier im Backend): Erreichbar? Version? Darf der Benutzer
   Tabellen anlegen? Liegt dort schon etwas?
2. Backend und Syslog anhalten, damit waehrend der Kopie nichts dazukommt.
3. Daten uebernehmen: ``pg_dump`` aus dem eingebauten Container direkt per
   ``psql`` in den Zielserver (beides mit den Werkzeugen aus dem
   Postgres-Container - auf dem Host muss nichts installiert sein).
   Ohne Uebernahme wird nur das Grundschema (init.sql) angelegt.
4. ``.env`` sichern, ``DATABASE_URL``/``DB_SSLMODE`` setzen,
   ``server/deploy/external-db.yml`` in ``COMPOSE_FILE`` aufnehmen.
5. ``docker compose up -d`` - das Backend startet gegen den neuen Server.
   Der eingebaute Container wird angehalten; sein Volume bleibt unangetastet,
   der Rueckweg ist also jederzeit moeglich (mit dem Datenstand vom Umzug).

Rueckweg: Eintraege aus der ``.env`` entfernen, ``external-db.yml`` aus
``COMPOSE_FILE`` nehmen, neu starten.
"""

from __future__ import annotations

import json
import logging
import ssl as ssl_mod
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import asyncpg

from . import hostexec
from .config import settings

logger = logging.getLogger("rimperio.dbswitch")

LOCAL_CONTAINER = settings.container("ext-postgres")
EXTERNAL_COMPOSE = "server/deploy/external-db.yml"
SSL_MODES = ("", "require", "verify-ca", "verify-full")


def build_url(host: str, port: int, database: str, user: str, password: str) -> str:
    return (f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{host}:{int(port)}/{quote(database, safe='')}")


def _ssl_arg(mode: str):
    mode = (mode or "").strip()
    if not mode:
        return False
    if mode == "require":
        ctx = ssl_mod.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl_mod.CERT_NONE
        return ctx
    ctx = ssl_mod.create_default_context()
    if mode == "verify-ca":
        ctx.check_hostname = False
    return ctx


async def test(host: str, port: int, database: str, user: str, password: str, sslmode: str) -> dict:
    """Erreichbar, passende Rechte, leer oder schon mit RIMPERIO-Daten?"""
    started = time.perf_counter()
    try:
        conn = await asyncpg.connect(host=host, port=int(port), database=database, user=user,
                                     password=password, ssl=_ssl_arg(sslmode), timeout=8)
    except Exception as exc:
        text = str(exc) or exc.__class__.__name__
        hint = ""
        if "password authentication failed" in text:
            hint = " Benutzer oder Passwort falsch."
        elif "does not exist" in text:
            hint = " Die Datenbank gibt es auf dem Server nicht — bitte vorher anlegen (CREATE DATABASE …)."
        elif "pg_hba" in text:
            hint = " Der Server lässt diese Adresse nicht zu — pg_hba.conf um die IP des RIMPERIO-Servers ergänzen."
        elif "SSL" in text or "ssl" in text:
            hint = " TLS-Einstellung prüfen."
        return {"ok": False, "message": f"Keine Verbindung: {text}.{hint}"}
    try:
        latency = round((time.perf_counter() - started) * 1000, 1)
        version = await conn.fetchval("SHOW server_version")
        major = int(str(version).split(".")[0]) if version else 0
        tables = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        has_rimperio = bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
            "AND table_name IN ('users','logs','settings')"))
        can_create = True
        try:
            async with conn.transaction():
                await conn.execute("CREATE TABLE rimperio_rights_probe (id int)")
                raise _Rollback()
        except _Rollback:
            pass
        except Exception:
            can_create = False
    finally:
        await conn.close()

    problems = []
    if major and major < 13:
        problems.append(f"PostgreSQL {version} ist zu alt (mindestens 13).")
    if not can_create:
        problems.append("Der Benutzer darf im Schema public keine Tabellen anlegen.")
    ok = not problems
    if ok and tables and not has_rimperio:
        message = (f"Verbindung klappt (PostgreSQL {version}, {latency} ms). Achtung: Die Datenbank ist "
                   f"nicht leer ({tables} fremde Tabellen) — besser eine eigene, leere Datenbank nehmen.")
    elif ok and has_rimperio:
        message = (f"Verbindung klappt (PostgreSQL {version}, {latency} ms). Dort liegen bereits "
                   f"RIMPERIO-Daten — beim Umzug ohne Übernahme werden diese benutzt.")
    elif ok:
        message = f"Verbindung klappt (PostgreSQL {version}, {latency} ms). Die Datenbank ist leer."
    else:
        message = "Verbindung klappt, aber: " + " ".join(problems)
    return {"ok": ok, "message": message, "version": version, "latency_ms": latency,
            "tables": tables, "has_rimperio": has_rimperio, "empty": not tables}


class _Rollback(Exception):
    pass


def current_mode() -> str:
    return "external" if settings.database_is_external else "builtin"


def _q(value: str) -> str:
    return hostexec._shell_quote(value)


def _env_value(value: str) -> str:
    """Wert fuer die .env - in einfachen Anfuehrungszeichen, wortwoertlich."""
    return "'" + value.replace("'", "") + "'"


def _script(target_url: Optional[str], sslmode: str, migrate: bool, state_file: str) -> str:
    paths = hostexec.host_paths()
    base = paths["install_dir"]
    to_external = target_url is not None
    lines = [
        "#!/bin/sh",
        "set -u",
        f"cd {_q(base)} || exit 1",
        f"STATE={_q(state_file)}",
        'step() { printf \'{"step":"%s","status":"%s","at":"%s"}\\n\' "$1" "$2" "$(date -Iseconds)" >> "$STATE"; echo "[$(date -Iseconds)] $1: $2"; }',
        'fail() { step "$1" "error"; echo "FEHLER: $2"; step done error; exit 1; }',
        ': > "$STATE"',
        "DBU=$(grep -E '^DB_USER=' .env | head -n1 | cut -d= -f2- | tr -d \"'\\\"\"); DBU=${DBU:-rimperio}",
        "DBN=$(grep -E '^DB_NAME=' .env | head -n1 | cut -d= -f2- | tr -d \"'\\\"\"); DBN=${DBN:-rimperio}",
        f"cp -a .env .env.before-db-switch-$(date +%Y%m%d%H%M%S) || fail backup '.env nicht sicherbar'",
        "step backup ok",
    ]
    if to_external:
        psql_target = _q(target_url + (f"?sslmode={sslmode}" if sslmode else ""))
        lines += [
            f"docker start {LOCAL_CONTAINER} >/dev/null 2>&1",
            "docker compose stop backend syslog >/dev/null 2>&1",
            "step stop ok",
        ]
        if migrate:
            lines += [
                "step copy running",
                f"docker exec {LOCAL_CONTAINER} pg_dump -U \"$DBU\" -d \"$DBN\" --no-owner --no-acl --clean --if-exists "
                f"| sed -e '/^SET transaction_timeout/d' "
                f"| docker exec -i {LOCAL_CONTAINER} psql -q -v ON_ERROR_STOP=1 {psql_target} "
                "|| { docker compose start backend syslog; fail copy 'Daten konnten nicht übernommen werden - alte Datenbank läuft weiter.'; }",
                "step copy ok",
            ]
        else:
            lines += [
                "step schema running",
                f"docker exec -i {LOCAL_CONTAINER} psql -q {psql_target} -c 'SELECT 1 FROM users LIMIT 1' >/dev/null 2>&1 "
                f"|| docker exec -i {LOCAL_CONTAINER} psql -q -v ON_ERROR_STOP=1 {psql_target} < server/db/init.sql "
                "|| { docker compose start backend syslog; fail schema 'Grundschema konnte nicht angelegt werden.'; }",
                "step schema ok",
            ]
        lines += [
            "sed -i -e '/^DATABASE_URL=/d' -e '/^DB_SSLMODE=/d' .env",
            f"echo DATABASE_URL={_q(_env_value(target_url))} >> .env",
            f"echo DB_SSLMODE={_q(sslmode)} >> .env",
            "CF=$(grep -E '^COMPOSE_FILE=' .env | head -n1 | cut -d= -f2-); CF=${CF:-docker-compose.yml}",
            f"case \":$CF:\" in *:{EXTERNAL_COMPOSE}:*) ;; *) CF=\"$CF:{EXTERNAL_COMPOSE}\" ;; esac",
            "sed -i '/^COMPOSE_FILE=/d' .env; echo \"COMPOSE_FILE=$CF\" >> .env",
            "step env ok",
            "docker compose up -d --remove-orphans || fail start 'Neustart fehlgeschlagen - .env-Sicherung liegt daneben.'",
            f"docker stop {LOCAL_CONTAINER} >/dev/null 2>&1",
            "step start ok",
        ]
    else:
        lines += [
            "sed -i -e '/^DATABASE_URL=/d' -e '/^DB_SSLMODE=/d' .env",
            "CF=$(grep -E '^COMPOSE_FILE=' .env | head -n1 | cut -d= -f2-)",
            f"CF=$(printf '%s' \"$CF\" | sed -e 's#:{EXTERNAL_COMPOSE}##g' -e 's#{EXTERNAL_COMPOSE}:##g' -e 's#^{EXTERNAL_COMPOSE}$##')",
            "sed -i '/^COMPOSE_FILE=/d' .env; [ -n \"$CF\" ] && echo \"COMPOSE_FILE=$CF\" >> .env",
            "step env ok",
            "docker compose up -d --remove-orphans || fail start 'Neustart fehlgeschlagen - .env-Sicherung liegt daneben.'",
            "step start ok",
        ]
    lines += ["step done ok", 'rm -f "$0"']
    return "\n".join(lines) + "\n"


async def start_switch(target: Optional[dict], migrate: bool, actor: str) -> dict:
    """Startet den Umzug auf dem Host. target=None heisst: zurueck zur eingebauten."""
    if not hostexec.nsenter_available():
        raise RuntimeError("Kein Zugriff auf den Host (gehärteter Betrieb) — Umstellen nur per Kommandozeile, "
                           "siehe server/deploy/external-db.yml.")
    paths = hostexec.host_paths()
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    state_file = f"{paths['state_dir']}/db-switch-state.jsonl"
    log_file = f"{paths['state_dir']}/db-switch.log"
    script_path = f"{paths['state_dir']}/db-switch-{stamp}.sh"
    url = None
    sslmode = ""
    if target:
        url = build_url(target["host"], target["port"], target["database"], target["user"], target["password"])
        sslmode = target.get("sslmode") or ""
    written = await hostexec.write_host_file(script_path, _script(url, sslmode, migrate, state_file), mode="0700")
    if not written.ok:
        raise RuntimeError(f"Skript konnte nicht abgelegt werden: {written.stderr or written.error}")
    await hostexec.write_host_file(log_file, f"--- Datenbank umstellen ({actor}, {stamp}) ---\n", mode="0600")
    result = await hostexec.spawn_host(["/bin/sh", script_path], unit_name=f"rimperio-dbswitch-{stamp}",
                                       log_path=log_file)
    if not result.ok:
        raise RuntimeError(f"Umzug konnte nicht gestartet werden: {result.stderr or result.error}")
    logger.warning("Datenbank-Umstellung gestartet von %s: %s", actor,
                   f"extern {target['host']}" if target else "zurück zur eingebauten")
    return {"started": True}


async def switch_status() -> dict:
    paths = hostexec.host_paths()
    raw = await hostexec.read_host_file(f"{paths['state_dir']}/db-switch-state.jsonl") or ""
    steps = []
    for line in raw.splitlines():
        try:
            steps.append(json.loads(line))
        except ValueError:
            continue
    log = await hostexec.read_host_file(f"{paths['state_dir']}/db-switch.log") or ""
    done = next((s for s in reversed(steps) if s.get("step") == "done"), None)
    return {"steps": steps, "done": bool(done), "ok": bool(done and done.get("status") == "ok"),
            "log": hostexec.split_lines(log, limit=60), "mode": current_mode()}
