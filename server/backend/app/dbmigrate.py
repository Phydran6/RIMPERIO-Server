# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Datenbank-Updates mit Sicherung, Probelauf, Pruefung
#               und Rueckweg (PostgreSQL, MariaDB/MySQL, MongoDB, Redis/Valkey)
# ==============================================================================
"""
Warum Datenbank-Container anders sind als jeder andere Container
=================================================================

Ein Web-Container ist zustandslos: altes Image weg, neues Image her, fertig -
geht es schief, nimmt man das alte zurueck. Eine Datenbank traegt ihre Daten in
einem Format, das zur *Hauptversion* passt. PostgreSQL 17 startet nicht auf
einem Datenverzeichnis von PostgreSQL 16; ein Image mit `:latest` macht genau
diesen Sprung irgendwann von selbst. Das Ergebnis ist ein Container, der in
einer Neustartschleife haengt, und eine Anwendung ohne Daten.

Das Konzept
===========

Jedes Datenbank-Update laeuft durch dieselben Schritte. Kein Schritt beginnt,
bevor der vorige nachweislich gelungen ist:

1. **Analyse**      Welche Datenbank, welche Version laeuft, welche kaeme?
                    Hauptversions-Sprung? Wie gross sind die Daten, reicht der
                    Platz? Wie viele Zeilen hat jede Tabelle (Referenz)?
2. **Sicherung**    Logischer Abzug (pg_dumpall / mariadb-dump / mongodump /
                    RDB) im Sicherungsordner auf dem Rechner.
3. **Probelauf**    Ein *Wegwerf-Container* mit dem **neuen** Image startet
                    neben der echten Datenbank, bekommt den Abzug eingespielt
                    (plus eigene Anpassungen, s. u.) und wird gezaehlt: jede
                    Tabelle, jede Sammlung, Fehler beim Einspielen. Danach wird
                    er restlos entfernt. Die echte Datenbank merkt davon nichts.
4. **Pruefung**     Zeilenzahlen alt gegen neu, Fehlermeldungen beim Einspielen
                    (bekannte harmlose herausgefiltert). Ist eine KI
                    eingerichtet, liest sie den Bericht und schlaegt - falls
                    noetig - Anpassungen vor (SQL bzw. JS), die vor dem
                    naechsten Probelauf uebernommen werden koennen.
                    Das ist die Antwort auf "die gesicherten Daten passen nicht
                    ganz zur neuen Version": angepasst wird *zwischen*
                    Einspielen und Pruefung, und erst wenn der Probelauf mit
                    den Anpassungen sauber ist, geht es weiter.
5. **Freigabe**     Kleine Updates (gleiche Hauptversion) mit sauberem
                    Probelauf duerfen automatisch weiter (Richtlinie). Ein
                    Hauptversions-Sprung braucht immer eine Freigabe - in der
                    Oberflaeche oder in der App.
6. **Ausfuehrung**  Optional Proxmox-Snapshot der ganzen VM. Dann:
                    * gleiche Hauptversion: frischer Abzug → Container-Update
                      (mit Gesundheitspruefung und automatischem Zurueck auf
                      das alte Image) → Zaehlung.
                    * Hauptversions-Sprung: andere Container des Compose-
                      Projekts anhalten (keine Schreibzugriffe) → frischer
                      Abzug → Datenbank anhalten → Datenverzeichnis als Archiv
                      sichern → leeren → Container mit neuem Image (legt eine
                      frische Datenbank an) → Abzug einspielen → Anpassungen →
                      Zaehlung → Nachbereitung (ANALYZE, mariadb-upgrade) →
                      Projekt wieder starten.
7. **Rueckweg**     Geht nach dem Leeren etwas schief, automatisch: Container
                    anhalten → Datenverzeichnis aus dem Archiv zurueck → altes
                    Image (vorher unter rimperio-dbrollback/… gesichert) →
                    starten. Es gibt also bis zum Schluss drei Sicherungen:
                    Abzug, Archiv des Datenverzeichnisses, ggf. Snapshot.

Alles laeuft ueber den vorhandenen Agent als Shell-Befehl mit `docker` -
dafuer ist der Agent in der Gruppe docker. Zugangsdaten verlassen den Rechner
nie: der Abzug liest sie im Container aus dessen Umgebung, der Probelauf
bekommt sie ueber eine Datei (0600) im Sicherungsordner.

Was dieses Modul NICHT tut: die Compose-Datei aendern. Wer den Tag selbst
umstellen will (z. B. postgres:16 → postgres:17), stellt ihn als Ziel-Image
ein; der Probelauf prueft genau dieses. Fuer die Ausfuehrung muss das Image
dann in der Compose-Datei stehen - der Bericht sagt das ausdruecklich.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import agentwake, ai, journal, notify, pve
from .database import Base, async_session, engine
from .models import DB_MIGRATION_TABLES, Agent, Command, DbMigration, PatchItem, PveHost

logger = logging.getLogger("rimperio.dbmigrate")

LOOP_SECONDS = 15
BACKUP_DIR = "/opt/RMM/rimperio/var/db-backups"

ACTIVE = ("analyzing", "backing_up", "dryrun", "snapshotting", "final_backup", "stopping",
          "archiving", "updating", "restoring", "verifying", "rolling_back")
WAITING = ("analyzed", "awaiting_approval", "approved")
FINAL = ("succeeded", "failed", "rolled_back", "cancelled", "dryrun_failed")

STATUS_LABEL = {
    "new": "neu", "analyzing": "Analyse läuft", "analyzed": "analysiert",
    "backing_up": "Sicherung läuft", "dryrun": "Probelauf läuft",
    "dryrun_failed": "Probelauf fehlgeschlagen", "awaiting_approval": "wartet auf Freigabe",
    "approved": "freigegeben", "snapshotting": "Snapshot", "final_backup": "letzte Sicherung",
    "stopping": "hält an", "archiving": "Datenverzeichnis sichern", "updating": "Update",
    "restoring": "Daten einspielen", "verifying": "Prüfung", "succeeded": "erfolgreich",
    "failed": "fehlgeschlagen", "rolling_back": "Rückweg", "rolled_back": "zurückgeholt",
    "cancelled": "abgebrochen",
}

DB_IMAGE = re.compile(
    r"(^|/)(?P<name>postgres|postgis|timescaledb(?:-ha)?|mariadb|mysql|percona(?:-server)?|mongo|redis|valkey|"
    r"bitnami/postgresql|bitnami/mariadb|bitnami/mysql|bitnami/mongodb|bitnami/redis)([:@]|$)", re.I)

_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,200}$")
_IMAGE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@\-]{0,400}$")


def engine_for_image(image: str) -> Optional[str]:
    m = DB_IMAGE.search(image or "")
    if not m:
        return None
    n = m.group("name").lower()
    if "postg" in n or "timescale" in n:
        return "postgres"
    if "mariadb" in n:
        return "mariadb"
    if "mysql" in n or "percona" in n:
        return "mysql"
    if "mongo" in n:
        return "mongo"
    if "redis" in n or "valkey" in n:
        return "redis"
    return None


def is_db_image(image: str) -> bool:
    return engine_for_image(image) is not None


def short_version(version: str) -> str:
    """"postgres (PostgreSQL) 16.4" / "mariadbd  Ver 11.4.2-MariaDB-…" → "16.4" / "11.4.2"."""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", version or "")
    return m.group(1) if m else (version or "")[:40]


def major_of(version: str) -> Optional[str]:
    m = re.search(r"(\d+)(?:\.(\d+))?", version or "")
    if not m:
        return None
    # MongoDB/MySQL/MariaDB: "Hauptversion" sind die ersten beiden Stellen
    # (MongoDB 6.0 → 7.0, MariaDB 10.11 → 11.4, MySQL 8.0 → 8.4).
    return m.group(1) if not m.group(2) else f"{m.group(1)}.{m.group(2)}"


def is_major_jump(engine_name: str, old: str, new: str) -> bool:
    if not old or not new:
        return True                        # unbekannt = vorsichtig
    o = re.search(r"(\d+)(?:\.(\d+))?", old)
    n = re.search(r"(\d+)(?:\.(\d+))?", new)
    if not o or not n:
        return True
    if engine_name == "postgres":
        return o.group(1) != n.group(1)    # ab PG 10 ist nur die erste Stelle die Hauptversion
    if engine_name == "redis":
        return False                       # RDB ist vorwaertskompatibel
    return (o.group(1), o.group(2)) != (n.group(1), n.group(2))


# =============================================================================
# Schema
# =============================================================================
async def ensure_schema() -> None:
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=DB_MIGRATION_TABLES))
        logger.info("db_migrations bereit")
    except Exception as exc:                                        # defensiv
        logger.error("db_migrations nicht angelegt: %s", exc)


# =============================================================================
# Shell-Bausteine (laufen auf dem Rechner, als Agent-Konto mit Gruppe docker)
# =============================================================================
def _q(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def _header(m: DbMigration) -> str:
    bk = f"{(m.plan or {}).get('backup_dir') or BACKUP_DIR}/{m.container}/m{m.id}"
    return f"""set -u
C={_q(m.container)}
BK={_q(bk)}
UIDGID="$(id -u):$(id -g)"
command -v docker >/dev/null 2>&1 || {{ echo "@@FAIL=Der Befehl docker fehlt auf diesem Rechner."; exit 2; }}
docker inspect "$C" >/dev/null 2>&1 || {{ echo "@@FAIL=Container $C gibt es nicht (mehr)."; exit 2; }}
mkdir -p "$BK" 2>/dev/null && chmod 700 "$BK" 2>/dev/null || {{ echo "@@FAIL=Sicherungsordner $BK lässt sich nicht anlegen."; exit 2; }}
echo "@@BK=$BK"
"""


# Je Datenbank: Version, Datenpfad, Abzug, Einspielen, Zaehlen, Bereitschaft.
# Alles, was im Container laeuft, steht in Heredocs mit 'EOS' - der Rechner
# expandiert darin nichts, erst die Shell im Container (mit deren Umgebung).
# Zaehlen bei MariaDB/MySQL: die erste Abfrage erzeugt je Tabelle ein
# SELECT COUNT(*), das zweite Programm fuehrt sie aus.
# Ausgabe: @@COUNT|schema|tabelle|anzahl
_MYSQL_COUNT = (
    '$CLI -uroot -N -e "SELECT CONCAT(' + "'SELECT ''', table_schema, '|', table_name, '|'', COUNT(*) FROM "
    + "\\`', table_schema, '\\`.\\`', table_name, '\\`;') FROM information_schema.tables "
    + "WHERE table_type='BASE TABLE' AND table_schema NOT IN "
    + "('mysql','sys','performance_schema','information_schema') LIMIT 3000\" "
    + "| $CLI -uroot -N 2>/dev/null | sed 's/^/@@COUNT|/; s/|\\t/|/; s/\\t/|/'"
)


ENGINES: Dict[str, Dict[str, str]] = {
    "postgres": {
        "version": "postgres -V 2>/dev/null | head -1",
        "datadir": 'echo "${PGDATA:-/var/lib/postgresql/data}"',
        "ready": 'pg_isready -q -U "${POSTGRES_USER:-postgres}"',
        "dump": 'pg_dumpall -U "${POSTGRES_USER:-postgres}"',
        "dumpfile": "dump.sql",
        "restore": 'psql -U "${POSTGRES_USER:-postgres}" -d postgres -v ON_ERROR_STOP=0 -q',
        "fixup": 'psql -U "${POSTGRES_USER:-postgres}" -d "${FIXDB:-postgres}" -v ON_ERROR_STOP=1 -q',
        "count": r'''U="${POSTGRES_USER:-postgres}"
for d in $(psql -U "$U" -d postgres -Atc "select datname from pg_database where datallowconn and not datistemplate order by 1"); do
  psql -U "$U" -d "$d" -At -F '|' <<SQL 2>/dev/null | sed "s/^/@@COUNT|$d|/"
SELECT format('SELECT %L, count(*) FROM %I.%I', n.nspname||'.'||c.relname, n.nspname, c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1 LIMIT 3000 \gexec
SQL
done''',
        "post": 'vacuumdb -U "${POSTGRES_USER:-postgres}" --all --analyze-only -q',
        "harmless": r'role "[^"]+" already exists|database "[^"]+" already exists|current user cannot be dropped',
    },
    "mariadb": {
        "version": "(mariadbd --version || mysqld --version) 2>/dev/null | head -1",
        "datadir": "echo /var/lib/mysql",
        "pw": r'''P="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"
[ -z "$P" ] && [ -n "${MARIADB_ROOT_PASSWORD_FILE:-${MYSQL_ROOT_PASSWORD_FILE:-}}" ] && P="$(cat "${MARIADB_ROOT_PASSWORD_FILE:-$MYSQL_ROOT_PASSWORD_FILE}")"
CLI="$(command -v mariadb || command -v mysql)"; DUMP="$(command -v mariadb-dump || command -v mysqldump)"
ADM="$(command -v mariadb-admin || command -v mysqladmin)"
if [ -n "$P" ]; then export MYSQL_PWD="$P"; fi''',
        "ready": '$ADM ping -uroot --silent',
        "dump": r'''DBS="$($CLI -uroot -N -e "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('mysql','sys','performance_schema','information_schema')" | tr '\n' ' ')"
$DUMP -uroot --single-transaction --routines --events --triggers --hex-blob --databases $DBS''',
        "dumpfile": "dump.sql",
        "restore": "$CLI -uroot --force",
        "fixup": "$CLI -uroot",
        "count": _MYSQL_COUNT,
        "post": "(command -v mariadb-upgrade >/dev/null && mariadb-upgrade -uroot --force) || (command -v mysql_upgrade >/dev/null && mysql_upgrade -uroot --force) || true",
        "harmless": r"",
    },
    "mongo": {
        "version": "mongod --version 2>/dev/null | head -1",
        "datadir": "echo /data/db",
        "pw": r'''AUTH=""
if [ -n "${MONGO_INITDB_ROOT_USERNAME:-}" ]; then AUTH="-u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin"; fi
SH="$(command -v mongosh || command -v mongo)"''',
        "ready": '$SH --quiet $AUTH --eval "db.adminCommand({ping:1}).ok" >/dev/null',
        "dump": "mongodump --quiet --archive --gzip $AUTH",
        "dumpfile": "dump.archive.gz",
        "restore": "mongorestore --quiet --archive --gzip --drop $AUTH",
        "fixup": '$SH --quiet $AUTH',
        "count": r'''$SH --quiet $AUTH --eval '
db.adminCommand({listDatabases:1}).databases.forEach(function(d){
  if (["admin","local","config"].indexOf(d.name) >= 0) return;
  var x = db.getSiblingDB(d.name);
  x.getCollectionNames().forEach(function(c){ print("@@COUNT|" + d.name + "|" + c + "|" + x.getCollection(c).estimatedDocumentCount()); });
});' ''',
        "post": "true",
        "harmless": r"",
    },
    "redis": {
        "version": "(redis-server --version || valkey-server --version) 2>/dev/null | head -1",
        "datadir": 'echo "$( (redis-cli CONFIG GET dir || valkey-cli CONFIG GET dir) 2>/dev/null | tail -1 )"',
        "pw": r'''RC="$(command -v redis-cli || command -v valkey-cli)"
[ -n "${REDIS_PASSWORD:-}" ] && export REDISCLI_AUTH="$REDIS_PASSWORD"''',
        "ready": '$RC ping | grep -q PONG',
        "dump": "",
        "dumpfile": "dump.rdb",
        "restore": "",
        "fixup": "",
        "count": r'''$RC INFO keyspace | grep '^db' | sed 's/^\(db[0-9]*\):keys=\([0-9]*\).*/@@COUNT|\1|keys|\2/' | tr -d '\r' ''',
        "post": "true",
        "harmless": r"",
    },
}
ENGINES["mysql"] = ENGINES["mariadb"]


def _in_container(target: str, engine_name: str, body: str, stdin: str = "") -> str:
    """Befehl in einem Container (target: Variable wie "$C" oder "$X")."""
    e = ENGINES[engine_name]
    pre = e.get("pw", "")
    return (f"docker exec -i {target} sh <<'EOS' {stdin}\n{pre}\n{body}\nEOS\n")


def _wait_ready(target: str, engine_name: str, seconds: int = 180) -> str:
    e = ENGINES[engine_name]
    return f"""i=0; until docker exec {target} sh -c {_q((e.get('pw', '') + chr(10) + e['ready']))} >/dev/null 2>&1; do
  i=$((i+3)); [ $i -ge {seconds} ] && {{ echo "@@FAIL=Datenbank in {target} wurde nicht bereit ({seconds} s)."; docker logs --tail 30 {target} 2>&1 | sed 's/^/@@LOG|/'; break; }}
  sleep 3
done
[ $i -lt {seconds} ] && echo "@@READY={target}"
"""


def _dump_cmd(engine_name: str, out: str) -> str:
    e = ENGINES[engine_name]
    if engine_name == "redis":
        return f"""docker exec "$C" sh -c {_q(e['pw'] + chr(10) + '$RC SAVE >/dev/null')} || {{ echo "@@FAIL=SAVE fehlgeschlagen"; exit 3; }}
DD="$(docker exec "$C" sh -c {_q(e['pw'] + chr(10) + '$RC CONFIG GET dir | tail -1')} | tr -d '\\r')"
docker cp "$C:$DD/dump.rdb" "{out}" || {{ echo "@@FAIL=dump.rdb nicht kopiert"; exit 3; }}
"""
    return f"""{_in_container('"$C"', engine_name, e['dump'], '> "' + out + '.part" 2> "$BK/dump.err"')}rc=$?
if [ $rc -ne 0 ] || [ ! -s "{out}.part" ]; then echo "@@FAIL=Abzug fehlgeschlagen (rc $rc): $(tail -c 400 "$BK/dump.err" | tr '\\n' ' ')"; exit 3; fi
mv "{out}.part" "{out}"
"""


def script_analyze(m: DbMigration) -> str:
    e = ENGINES[m.engine]
    target = m.target_image or m.image
    return _header(m) + f"""
IMG="$(docker inspect -f '{{{{.Config.Image}}}}' "$C")"; echo "@@IMAGE=$IMG"
echo "@@IMAGE_ID=$(docker inspect -f '{{{{.Image}}}}' "$C")"
echo "@@STATE=$(docker inspect -f '{{{{.State.Status}}}}' "$C")"
echo "@@PROJECT=$(docker inspect -f '{{{{index .Config.Labels "com.docker.compose.project"}}}}' "$C" 2>/dev/null)"
echo "@@SERVICE=$(docker inspect -f '{{{{index .Config.Labels "com.docker.compose.service"}}}}' "$C" 2>/dev/null)"
echo "@@WORKDIR=$(docker inspect -f '{{{{index .Config.Labels "com.docker.compose.project.working_dir"}}}}' "$C" 2>/dev/null)"
echo "@@FILES=$(docker inspect -f '{{{{index .Config.Labels "com.docker.compose.project.config_files"}}}}' "$C" 2>/dev/null)"
echo "@@MOUNTS=$(docker inspect -f '{{{{json .Mounts}}}}' "$C")"
echo "@@OLDVER=$(docker exec "$C" sh -c {_q(e['version'])} 2>/dev/null | tr -d '\\r')"
DD="$(docker exec "$C" sh -c {_q(e.get('pw', '') + chr(10) + e['datadir'])} 2>/dev/null | tr -d '\\r')"; echo "@@DATADIR=$DD"
echo "@@DATASIZE=$(docker exec "$C" sh -c "du -sb '$DD' 2>/dev/null | cut -f1")"
T={_q(target)}
docker pull -q "$T" >/dev/null 2>&1 || echo "@@WARN=Ziel-Image $T ließ sich nicht holen (vorhandenes wird genutzt)."
echo "@@TARGET=$T"
echo "@@TARGET_ID=$(docker image inspect -f '{{{{.Id}}}}' "$T" 2>/dev/null)"
echo "@@NEWVER=$(docker run --rm --entrypoint sh "$T" -c {_q(e['version'])} 2>/dev/null | tr -d '\\r')"
echo "@@FREE_BK=$(df -PB1 "$BK" | awk 'NR==2{{print $4}}')"
DR="$(docker info -f '{{{{.DockerRootDir}}}}' 2>/dev/null)"; echo "@@FREE_DOCKER=$(df -PB1 "${{DR:-/var/lib/docker}}" 2>/dev/null | awk 'NR==2{{print $4}}')"
# Umgebung fuer den Probelauf: nur, was die Datenbank braucht - bleibt auf dem Rechner.
docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' "$C" | grep -E '^(POSTGRES_|PG|MARIADB_|MYSQL_|MONGO_|REDIS_|TZ=)' | grep -v '^PGDATA=' > "$BK/env.list"
chmod 600 "$BK/env.list"
echo "@@ENVKEYS=$(cut -d= -f1 "$BK/env.list" | tr '\\n' ' ')"
if [ "$(docker inspect -f '{{{{.State.Status}}}}' "$C")" = "running" ]; then
{_in_container('"$C"', m.engine, e['count'])}
fi
echo "@@DONE=analyze"
"""


def script_backup(m: DbMigration, name: str = "dump") -> str:
    e = ENGINES[m.engine]
    out = f"$BK/{name}-{e['dumpfile']}"
    return _header(m) + _dump_cmd(m.engine, out) + f"""
echo "@@DUMP={out}"
echo "@@DUMPSIZE=$(stat -c %s "{out}")"
sha256sum "{out}" | cut -d' ' -f1 | sed 's/^/@@SHA=/'
echo "@@DONE=backup"
"""


def _fixups_block(m: DbMigration, target: str) -> str:
    fixes = [f for f in ((m.plan or {}).get("fixups") or []) if str(f.get("code", "")).strip()]
    if not fixes or m.engine == "redis":
        return ""
    out = []
    for n, f in enumerate(fixes, 1):
        code = str(f["code"])
        db = re.sub(r"[^A-Za-z0-9_\-]", "", str(f.get("database") or ""))
        marker = f"FIX{n}_EOF"
        env = f"export FIXDB={_q(db)}; " if db else ""
        out.append(f"""cat > "$BK/fixup-{n}.txt" <<'{marker}'
{code}
{marker}
docker exec -i {target} sh -c {_q(ENGINES[m.engine].get('pw', '') + chr(10) + env + ENGINES[m.engine]['fixup'])} < "$BK/fixup-{n}.txt" > "$BK/fixup-{n}.log" 2>&1
rc=$?; echo "@@FIXUP|{n}|$rc|$(tail -c 300 "$BK/fixup-{n}.log" | tr '\\n' ' ')"
""")
    return "\n".join(out)


def script_dryrun(m: DbMigration) -> str:
    e = ENGINES[m.engine]
    target = m.target_image or m.image
    dump = f"$BK/dump-{e['dumpfile']}"
    x = f"rimperio-dryrun-{m.id}"
    if m.engine == "redis":
        restore = f"""mkdir -p "$BK/dryrun-data" && cp "{dump}" "$BK/dryrun-data/dump.rdb" && chmod -R a+rwX "$BK/dryrun-data"
docker run -d --name "$X" --env-file "$BK/env.list" -v "$BK/dryrun-data:/data" {_q(target)} >/dev/null || {{ echo "@@FAIL=Probelauf-Container startet nicht"; exit 4; }}
"""
    else:
        restore = f"""docker run -d --name "$X" --env-file "$BK/env.list" {_q(target)} >/dev/null || {{ echo "@@FAIL=Probelauf-Container startet nicht"; exit 4; }}
"""
    body = _header(m) + f"""
X={_q(x)}
T={_q(target)}
[ -s "{dump}" ] || {{ echo "@@FAIL=Kein Abzug vorhanden – erst sichern."; exit 4; }}
docker rm -fv "$X" >/dev/null 2>&1
cleanup() {{ docker rm -fv "$X" >/dev/null 2>&1; rm -rf "$BK/dryrun-data"; }}
trap cleanup EXIT
{restore}{_wait_ready('"$X"', m.engine, 240)}
"""
    if m.engine != "redis":
        body += f"""sleep 3
{_wait_ready('"$X"', m.engine, 120)}
START=$(date +%s)
docker exec -i "$X" sh -c {_q(e.get('pw', '') + chr(10) + e['restore'])} < "{dump}" > "$BK/restore-dryrun.log" 2>&1
echo "@@RESTORE_RC=$?"
echo "@@RESTORE_SECONDS=$(( $(date +%s) - START ))"
grep -iE 'error|fatal|failed' "$BK/restore-dryrun.log" | head -80 | sed 's/^/@@ERR|/'
echo "@@ERRCOUNT=$(grep -ciE 'error|fatal' "$BK/restore-dryrun.log")"
{_fixups_block(m, '"$X"')}
"""
    body += f"""{_in_container('"$X"', m.engine, e['count'])}
echo "@@NEWVER=$(docker exec "$X" sh -c {_q(e['version'])} 2>/dev/null | tr -d '\\r')"
docker logs --tail 15 "$X" 2>&1 | sed 's/^/@@LOG|/'
echo "@@DONE=dryrun"
"""
    return body


def _compose_siblings_stop() -> str:
    return """P="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$C" 2>/dev/null)"
: > "$BK/stopped.list"
if [ -n "$P" ]; then
  for s in $(docker ps -q --filter "label=com.docker.compose.project=$P"); do
    n="$(docker inspect -f '{{.Name}}' "$s" | sed 's#^/##')"
    [ "$n" = "$C" ] && continue
    docker stop -t 30 "$s" >/dev/null && echo "$n" >> "$BK/stopped.list" && echo "@@STOPPED=$n"
  done
fi
"""


def script_major_prepare(m: DbMigration) -> str:
    """Hauptversion: Projekt anhalten, letzter Abzug, DB anhalten, Archiv, leeren."""
    e = ENGINES[m.engine]
    plan = m.plan or {}
    dd = plan.get("datadir") or "/var/lib/postgresql/data"
    tag = f"rimperio-dbrollback/{re.sub(r'[^a-z0-9_.-]', '-', m.container.lower())}:m{m.id}"
    dump = f"$BK/final-{e['dumpfile']}"
    return _header(m) + f"""
DD={_q(dd)}
OLDID="$(docker inspect -f '{{{{.Image}}}}' "$C")"
docker tag "$OLDID" {_q(tag)} && echo "@@ROLLBACK_IMAGE={tag}"
{_compose_siblings_stop()}
{_dump_cmd(m.engine, dump)}echo "@@DUMP={dump}"
echo "@@DUMPSIZE=$(stat -c %s "{dump}")"
{_in_container('"$C"', m.engine, e['count'])}
docker stop -t 60 "$C" >/dev/null || {{ echo "@@FAIL=Datenbank-Container hält nicht an."; exit 5; }}
echo "@@DBSTOPPED=1"
docker run --rm --user 0:0 --volumes-from "$C" -v "$BK:/rimperio-bk" --entrypoint sh "$OLDID" -c "tar czf /rimperio-bk/datadir.tgz -C '$DD' . && chown $UIDGID /rimperio-bk/datadir.tgz" \\
  || {{ echo "@@FAIL=Datenverzeichnis ließ sich nicht sichern – nichts geändert."; docker start "$C" >/dev/null; exit 5; }}
gzip -t "$BK/datadir.tgz" || {{ echo "@@FAIL=Archiv des Datenverzeichnisses ist beschädigt – nichts geändert."; docker start "$C" >/dev/null; exit 5; }}
echo "@@ARCHIVE=$BK/datadir.tgz"
echo "@@ARCHIVESIZE=$(stat -c %s "$BK/datadir.tgz")"
docker run --rm --user 0:0 --volumes-from "$C" --entrypoint sh "$OLDID" -c "find '$DD' -mindepth 1 -delete" \\
  || {{ echo "@@FAIL=Datenverzeichnis ließ sich nicht leeren."; exit 6; }}
echo "@@CLEARED=1"
echo "@@DONE=prepare"
"""


def script_restore_and_verify(m: DbMigration, fresh: bool) -> str:
    """Nach dem Container-Update: bereit? Abzug einspielen (nur Hauptversion),
    Anpassungen, zaehlen, Nachbereitung, Projekt wieder starten."""
    e = ENGINES[m.engine]
    dump = f"$BK/final-{e['dumpfile']}"
    body = _header(m) + _wait_ready('"$C"', m.engine, 300)
    if fresh and m.engine != "redis":
        body += f"""sleep 5
{_wait_ready('"$C"', m.engine, 120)}
docker exec -i "$C" sh -c {_q(e.get('pw', '') + chr(10) + e['restore'])} < "{dump}" > "$BK/restore-final.log" 2>&1
echo "@@RESTORE_RC=$?"
grep -iE 'error|fatal|failed' "$BK/restore-final.log" | head -80 | sed 's/^/@@ERR|/'
echo "@@ERRCOUNT=$(grep -ciE 'error|fatal' "$BK/restore-final.log")"
{_fixups_block(m, '"$C"')}
"""
    body += f"""{_in_container('"$C"', m.engine, e['count'])}
echo "@@NEWVER=$(docker exec "$C" sh -c {_q(e['version'])} 2>/dev/null | tr -d '\\r')"
docker exec "$C" sh -c {_q(e.get('pw', '') + chr(10) + e['post'])} > "$BK/post.log" 2>&1; echo "@@POST_RC=$?"
echo "@@DONE=verify"
"""
    return body


def script_start_siblings(m: DbMigration) -> str:
    return _header(m) + """
if [ -s "$BK/stopped.list" ]; then
  while read -r n; do [ -n "$n" ] && docker start "$n" >/dev/null && echo "@@STARTED=$n"; done < "$BK/stopped.list"
fi
echo "@@DONE=siblings"
"""


def script_rollback(m: DbMigration) -> str:
    plan = m.plan or {}
    rep = m.report or {}
    dd = plan.get("datadir") or "/var/lib/postgresql/data"
    tag = (rep.get("prepare") or {}).get("rollback_image") or ""
    configured = plan.get("image") or m.image
    project, service = plan.get("project") or "", plan.get("service") or ""
    workdir, files = plan.get("workdir") or "", plan.get("files") or ""
    compose = ""
    if project and service:
        fargs = " ".join(f"-f {_q(f)}" for f in files.split(",") if f.strip())
        # Die Compose-Datei kann schon das neue Image nennen - eine Override-
        # Datei erzwingt fuer diesen einen Start das gesicherte alte Image.
        compose = f"""if docker compose version >/dev/null 2>&1; then
  printf 'services:\\n  %s:\\n    image: %s\\n    pull_policy: never\\n' {_q(service)} "$RB" > "$BK/rollback-override.yml"
  docker compose -p {_q(project)} {('--project-directory ' + _q(workdir)) if workdir else ''} {fargs} -f "$BK/rollback-override.yml" up -d --no-deps --force-recreate {_q(service)} \\
    && echo "@@RECREATED=compose" && RECREATED=1
fi
"""
    return _header(m) + f"""
DD={_q(dd)}
RB={_q(tag)}
[ -n "$RB" ] && docker image inspect "$RB" >/dev/null 2>&1 || {{ echo "@@FAIL=Gesichertes altes Image fehlt – Rückweg nur über Proxmox-Snapshot bzw. von Hand ($BK)."; exit 7; }}
[ -s "$BK/datadir.tgz" ] || {{ echo "@@FAIL=Archiv des Datenverzeichnisses fehlt ($BK)."; exit 7; }}
docker stop -t 30 "$C" >/dev/null 2>&1
docker run --rm --user 0:0 --volumes-from "$C" -v "$BK:/rimperio-bk:ro" --entrypoint sh "$RB" -c "find '$DD' -mindepth 1 -delete; tar xzf /rimperio-bk/datadir.tgz -C '$DD'" \\
  || {{ echo "@@FAIL=Datenverzeichnis ließ sich nicht zurückspielen – Archiv liegt in $BK."; exit 7; }}
echo "@@RESTORED_DATADIR=1"
RECREATED=0
{compose}
if [ "$RECREATED" != 1 ]; then
  # Ohne Compose: Container mit gleicher Konfiguration, aber altem Image ueber die Docker-API neu anlegen.
  SOCK=/var/run/docker.sock
  if command -v curl >/dev/null 2>&1 && [ -S "$SOCK" ]; then
    CFG="$(docker inspect -f '{{{{json .Config}}}}' "$C" | sed "s#\\"Image\\":\\"[^\\"]*\\"#\\"Image\\":\\"$RB\\"#")"
    HC="$(docker inspect -f '{{{{json .HostConfig}}}}' "$C")"
    NETS="$(docker inspect -f '{{{{range $k, $v := .NetworkSettings.Networks}}}}{{{{$k}}}} {{{{end}}}}' "$C")"
    docker rename "$C" "$C-rimperio-failed-m{m.id}" || exit 7
    BODY="${{CFG%?}},\\"HostConfig\\":$HC}}"
    curl -sf --unix-socket "$SOCK" -H 'Content-Type: application/json' -d "$BODY" "http://localhost/containers/create?name=$C" >/dev/null \\
      && for n in $NETS; do docker network connect "$n" "$C" >/dev/null 2>&1; done \\
      && docker start "$C" >/dev/null && echo "@@RECREATED=api" && RECREATED=1
    [ "$RECREATED" = 1 ] || {{ docker rename "$C-rimperio-failed-m{m.id}" "$C"; }}
  fi
fi
[ "$RECREATED" = 1 ] || {{ echo "@@FAIL=Daten sind zurück, aber der Container konnte nicht mit dem alten Image neu angelegt werden. Altes Image: $RB"; exit 7; }}
{_wait_ready('"$C"', m.engine, 240)}
if [ -s "$BK/stopped.list" ]; then
  while read -r n; do [ -n "$n" ] && docker start "$n" >/dev/null && echo "@@STARTED=$n"; done < "$BK/stopped.list"
fi
echo "@@DONE=rollback"
"""


def script_cleanup(m: DbMigration, keep_days: int = 7) -> str:
    return _header(m) + f"""
docker rm -fv "rimperio-dryrun-{m.id}" >/dev/null 2>&1
find "$(dirname "$BK")" -maxdepth 1 -type d -name 'm*' -mtime +{int(keep_days)} -exec rm -rf {{}} + 2>/dev/null
echo "@@DONE=cleanup"
"""


# =============================================================================
# Ausgabe lesen
# =============================================================================
def parse_output(text: str) -> dict:
    out: Dict[str, Any] = {"counts": {}, "errors": [], "logs": [], "warnings": [], "fixups": [],
                           "stopped": [], "started": []}
    for line in (text or "").splitlines():
        if not line.startswith("@@"):
            continue
        body = line[2:]
        if body.startswith("COUNT|"):
            parts = body.split("|")
            if len(parts) >= 4:
                db, table, n = parts[1], "|".join(parts[2:-1]), parts[-1]
                try:
                    out["counts"][f"{db}.{table}" if not table.startswith(db + ".") else table] = int(n.strip())
                except ValueError:
                    pass
            continue
        if body.startswith("ERR|"):
            out["errors"].append(body[4:][:400])
            continue
        if body.startswith("LOG|"):
            out["logs"].append(body[4:][:300])
            continue
        if body.startswith("FIXUP|"):
            p = body.split("|", 3)
            out["fixups"].append({"n": p[1], "rc": p[2], "tail": p[3] if len(p) > 3 else ""})
            continue
        if "=" in body:
            k, v = body.split("=", 1)
            k = k.lower()
            if k == "warn":
                out["warnings"].append(v)
            elif k == "stopped":
                out["stopped"].append(v)
            elif k == "started":
                out["started"].append(v)
            else:
                out[k] = v
    return out


def compare_counts(before: Dict[str, int], after: Dict[str, int]) -> dict:
    missing = sorted(k for k in before if k not in after)
    extra = sorted(k for k in after if k not in before)
    diffs = []
    for k, n in before.items():
        if k in after and after[k] != n:
            diffs.append({"table": k, "before": n, "after": after[k]})
    return {"tables_before": len(before), "tables_after": len(after),
            "rows_before": sum(before.values()), "rows_after": sum(after.values()),
            "missing": missing[:100], "extra": extra[:100], "diffs": diffs[:100],
            "ok": not missing and not diffs}


def real_errors(engine_name: str, errors: List[str]) -> List[str]:
    harmless = ENGINES[engine_name].get("harmless") or ""
    if not harmless:
        return errors
    pat = re.compile(harmless, re.I)
    return [e for e in errors if not pat.search(e)]


# =============================================================================
# Ablauf
# =============================================================================
def _log(m: DbMigration, text: str, level: str = "info") -> None:
    entries = list(m.log or [])
    entries.append({"ts": datetime.utcnow().isoformat(timespec="seconds") + "Z", "level": level, "text": text})
    m.log = entries[-300:]


def _stage(m: DbMigration, status: str, text: str = "", level: str = "info") -> None:
    m.status, m.stage_since = status, datetime.utcnow()
    if text:
        _log(m, text, level)


def _rep(m: DbMigration, key: str, value: Any) -> None:
    rep = dict(m.report or {})
    rep[key] = value
    m.report = rep


async def _queue_shell(db: AsyncSession, m: DbMigration, agent: Agent, stage: str, script: str,
                       timeout: int) -> Command:
    cmd = Command(agent_id=agent.id, shell="sh", command=script, timeout=timeout, kind="shell",
                  source=f"DB-Update #{m.id}: {stage}", created_by="dbmigrate")
    db.add(cmd)
    await db.flush()
    m.commands = {**(m.commands or {}), stage: cmd.id}
    agentwake.notify(agent.id, delay=1.0)
    return cmd


async def _queue_docker_update(db: AsyncSession, m: DbMigration, agent: Agent) -> Command:
    payload = {"containers": [m.container], "cleanup": False, "health_timeout": 300,
               "rollback": (m.plan or {}).get("path") != "major", "prefer_compose": True}
    cmd = Command(agent_id=agent.id, shell="sh", command=json.dumps(payload), timeout=3600,
                  kind="docker_update", source=f"DB-Update #{m.id}: Container", created_by="dbmigrate")
    db.add(cmd)
    await db.flush()
    m.commands = {**(m.commands or {}), "update": cmd.id}
    agentwake.notify(agent.id, delay=1.0)
    return cmd


async def _cmd(db: AsyncSession, m: DbMigration, stage: str) -> Optional[Command]:
    cid = (m.commands or {}).get(stage)
    return await db.get(Command, cid) if cid else None


def _done(cmd: Optional[Command]) -> bool:
    return cmd is not None and cmd.status not in ("pending", "running")


def _result(cmd: Command) -> dict:
    res = parse_output((cmd.stdout or "") + "\n" + (cmd.stderr or ""))
    res["exit_code"] = cmd.exit_code
    res["status"] = cmd.status
    if cmd.status != "done" and not res.get("fail"):
        res["fail"] = f"Befehl {cmd.status}: {(cmd.stderr or '')[-400:]}"
    if cmd.exit_code not in (0, None) and not res.get("fail"):
        res["fail"] = f"Rückgabewert {cmd.exit_code}: {(cmd.stderr or cmd.stdout or '')[-400:]}"
    return res


async def create(db: AsyncSession, agent: Agent, container: str, user: str,
                 target_image: str = "", mode: str = "manual", patch_item_id: Optional[int] = None,
                 snapshot: bool = True, auto_execute_minor: bool = True) -> DbMigration:
    if not _NAME_OK.match(container or ""):
        raise ValueError("Ungültiger Containername.")
    if target_image and not _IMAGE_OK.match(target_image):
        raise ValueError("Ungültiges Ziel-Image.")
    if (agent.platform or "").lower().startswith("win"):
        raise ValueError("Datenbank-Updates mit Probelauf gibt es für Linux-Rechner mit Docker.")
    active = (await db.execute(select(DbMigration).where(
        DbMigration.agent_id == agent.id, DbMigration.container == container,
        DbMigration.status.notin_(FINAL)))).scalars().first()
    if active:
        raise ValueError(f"Für {container} läuft bereits DB-Update #{active.id} ({STATUS_LABEL.get(active.status)}).")
    image = ""
    for c in (agent.docker_inventory or {}).get("containers") or []:
        if c.get("name") == container:
            image = c.get("image") or ""
    eng = engine_for_image(target_image or image)
    if not eng:
        raise ValueError(f"{container} ({image or 'Image unbekannt'}) ist kein bekannter Datenbank-Container "
                         "(PostgreSQL, MariaDB/MySQL, MongoDB, Redis/Valkey).")
    m = DbMigration(agent_id=agent.id, container=container, engine=eng, image=image,
                    target_image=target_image or "", mode=mode, status="analyzing", created_by=user,
                    patch_item_id=patch_item_id,
                    plan={"snapshot": bool(snapshot), "auto_execute_minor": bool(auto_execute_minor),
                          "fixups": [], "backup_dir": BACKUP_DIR},
                    report={}, commands={}, log=[])
    db.add(m)
    await db.flush()
    _log(m, f"Angelegt von {user} ({'automatisch aus Patch-Lauf' if mode == 'auto' else 'von Hand'}).")
    await _queue_shell(db, m, agent, "analyze", script_analyze(m), 1800)
    journal.fire(category="rmm", event="dbmigrate.created", actor=user, target=f"{agent.hostname}/{container}",
                 message=f"DB-Update #{m.id} für {container} auf {agent.hostname} angelegt.")
    return m


async def rerun_dryrun(db: AsyncSession, m: DbMigration, user: str, fixups: Optional[list] = None) -> DbMigration:
    if m.status not in ("analyzed", "awaiting_approval", "dryrun_failed", "approved"):
        raise ValueError("Ein Probelauf geht nur vor der Ausführung.")
    agent = await db.get(Agent, m.agent_id)
    if fixups is not None:
        clean = []
        for f in fixups[:20]:
            code = str((f or {}).get("code") or "").strip()
            if code:
                clean.append({"code": code[:20000], "database": str(f.get("database") or "")[:64],
                              "note": str(f.get("note") or "")[:300]})
        m.plan = {**(m.plan or {}), "fixups": clean}
        _log(m, f"{user}: {len(clean)} Anpassung(en) für den Probelauf gesetzt.")
    m.approved_by = ""
    _stage(m, "dryrun", f"{user}: Probelauf (erneut) gestartet.")
    await _queue_shell(db, m, agent, "dryrun", script_dryrun(m), 4 * 3600)
    await db.commit()
    return m


async def approve(db: AsyncSession, m: DbMigration, user: str, force: bool = False) -> DbMigration:
    if m.status not in ("awaiting_approval", "dryrun_failed"):
        raise ValueError("Freigeben lässt sich nur ein DB-Update, das auf eine Freigabe wartet.")
    plan = m.plan or {}
    if plan.get("target") and plan.get("image") and plan["target"] != plan["image"] and not plan.get("project"):
        raise ValueError(f"Ziel-Image {plan['target']} weicht vom eingestellten Image {plan['image']} ab, und der "
                         "Container läuft nicht über Compose – hier ist nur ein Probelauf möglich. Für die "
                         "Ausführung das Image in der Konfiguration umstellen (ohne neu zu starten) und erneut analysieren.")
    if m.status == "dryrun_failed" and not force:
        raise ValueError("Der Probelauf ist fehlgeschlagen. Erst Anpassungen setzen und den Probelauf "
                         "wiederholen – oder ausdrücklich trotzdem freigeben (force).")
    m.approved_by = user
    _stage(m, "approved", f"Freigegeben von {user}" + (" (trotz fehlgeschlagenem Probelauf)" if force else "") + ".",
           "warning" if force else "info")
    await notify.resolve(db, action="db_approve", match={"migration_id": m.id}, commit=False)
    await db.commit()
    return m


async def cancel(db: AsyncSession, m: DbMigration, user: str) -> DbMigration:
    if m.status in ACTIVE and m.status not in ("analyzing", "backing_up", "dryrun"):
        raise ValueError("Mitten in der Ausführung lässt sich nicht abbrechen – das wäre gefährlicher als "
                         "weiterzumachen. Bei Problemen läuft der Rückweg automatisch.")
    _stage(m, "cancelled", f"Abgebrochen von {user}.", "warning")
    m.finished_at = datetime.utcnow()
    await _set_item(db, m, None)
    agent = await db.get(Agent, m.agent_id)
    if agent:
        await _queue_shell(db, m, agent, "cleanup", script_cleanup(m), 600)
    await db.commit()
    return m


async def rollback(db: AsyncSession, m: DbMigration, user: str) -> DbMigration:
    if (m.plan or {}).get("path") != "major" or not (m.report or {}).get("prepare", {}).get("archive"):
        raise ValueError("Für dieses DB-Update gibt es keinen Rückweg über das Datenverzeichnis "
                         "(nur bei Hauptversions-Updates nach dem Sichern). Bei Bedarf den Proxmox-Snapshot nutzen.")
    if m.status in ACTIVE:
        raise ValueError("Es läuft gerade ein Schritt.")
    agent = await db.get(Agent, m.agent_id)
    _stage(m, "rolling_back", f"{user}: Rückweg gestartet.", "warning")
    await _queue_shell(db, m, agent, "rollback", script_rollback(m), 3600)
    await db.commit()
    return m


async def _set_item(db: AsyncSession, m: DbMigration, ok: Optional[bool]) -> None:
    if not m.patch_item_id:
        return
    it = await db.get(PatchItem, m.patch_item_id)
    if it is None:
        return
    if ok is True:
        it.status, it.installed_at = "installed", datetime.utcnow()
    elif ok is False:
        it.status, it.error = "failed", (m.error or "")[:2000]
    else:
        it.status = "needs_approval"


async def _finish(db: AsyncSession, m: DbMigration, agent: Agent, ok: bool, text: str,
                  status: Optional[str] = None) -> None:
    m.finished_at = datetime.utcnow()
    _stage(m, status or ("succeeded" if ok else "failed"), text, "info" if ok else "error")
    if not ok:
        m.error = text[:2000]
    await _set_item(db, m, ok if status != "rolled_back" else False)
    await notify.push(db, category="patch", level="info" if ok else "critical",
                      title=f"{agent.hostname}: DB-Update {m.container} "
                            + ("erfolgreich" if ok else ("zurückgeholt" if status == "rolled_back" else "fehlgeschlagen")),
                      body=text, link=f"/rmm/db-updates?id={m.id}",
                      data={"migration_id": m.id, "agent_uid": agent.agent_uid},
                      event="patch_done" if ok else "patch_failed", commit=False)
    journal.fire(category="rmm", event="dbmigrate." + (status or ("succeeded" if ok else "failed")),
                 level="info" if ok else "error", target=f"{agent.hostname}/{m.container}", ok=ok,
                 message=f"DB-Update #{m.id} ({m.container} auf {agent.hostname}): {text}")


async def _ai_review(db: AsyncSession, m: DbMigration) -> Optional[dict]:
    cfg = await ai.load_config(db)
    if cfg.get("provider", "off") == "off":
        return None
    plan, rep = m.plan or {}, m.report or {}
    dry = rep.get("dryrun") or {}
    cmp_ = dry.get("compare") or {}
    prompt = (
        f"Datenbank: {m.engine}, Container {m.container}\n"
        f"Version alt: {short_version(plan.get('old_version'))} → neu: {short_version(plan.get('new_version'))} "
        f"(Hauptversions-Sprung: {'ja' if plan.get('path') == 'major' else 'nein'})\n"
        f"Tabellen vorher/nachher: {cmp_.get('tables_before')}/{cmp_.get('tables_after')}, "
        f"Zeilen vorher/nachher: {cmp_.get('rows_before')}/{cmp_.get('rows_after')}\n"
        f"Fehlende Tabellen: {', '.join(cmp_.get('missing', [])[:30]) or 'keine'}\n"
        f"Abweichende Zeilenzahlen: {json.dumps(cmp_.get('diffs', [])[:20], ensure_ascii=False)}\n"
        f"Fehler beim Einspielen ({len(dry.get('real_errors') or [])}):\n"
        + "\n".join((dry.get("real_errors") or [])[:40]) +
        f"\nBereits gesetzte Anpassungen: {json.dumps(plan.get('fixups') or [], ensure_ascii=False)[:3000]}\n"
        f"Letzte Container-Logs: " + " | ".join((dry.get("logs") or [])[-10:])
    )
    system = (
        "Du bist ein erfahrener Datenbank-Administrator. Du bewertest den Probelauf eines Datenbank-Updates "
        "(logischer Abzug der alten Version, eingespielt in die neue Version). Beurteile, ob das Update sicher "
        "ausgeführt werden kann, und schlage – nur wenn nötig – konkrete Anpassungen vor, die NACH dem Einspielen "
        "ausgeführt werden (SQL für PostgreSQL/MariaDB/MySQL, JavaScript für MongoDB). Erfinde nichts; wenn alles "
        "passt, keine Anpassungen. Antworte nur mit JSON: {\"recommendation\":\"go|caution|stop\","
        "\"summary\":\"zwei Sätze auf Deutsch\",\"risks\":[\"…\"],"
        "\"fixups\":[{\"database\":\"name oder leer\",\"code\":\"…\",\"note\":\"warum\"}]}"
    )
    try:
        result = await ai.ask_text(cfg, system, prompt)
        parsed = ai.parse_json_answer(result.get("answer", "")) or {}
        return {"recommendation": str(parsed.get("recommendation", "")).lower()[:16],
                "summary": str(parsed.get("summary", ""))[:1200],
                "risks": [str(r)[:300] for r in (parsed.get("risks") or [])][:10],
                "fixups": [{"database": str(f.get("database") or "")[:64], "code": str(f.get("code") or "")[:8000],
                            "note": str(f.get("note") or "")[:300]}
                           for f in (parsed.get("fixups") or []) if isinstance(f, dict)][:10],
                "model": result.get("model"), "at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    except Exception as exc:
        return {"error": str(exc)[:300]}


async def _link_snapshot(db: AsyncSession, m: DbMigration, agent: Agent) -> bool:
    """Proxmox-Snapshot anstossen. True = laeuft (Status 'snapshotting')."""
    found = await pve.link_for(db, agent.id)
    if not found or not (m.plan or {}).get("snapshot"):
        return False
    host, link = found
    name = pve.snapshot_name("rimdb")
    try:
        upid = await pve.create_snapshot(host, link, name, f"RIMPERIO vor DB-Update #{m.id} ({m.container})")
    except pve.PveError as exc:
        _log(m, f"Proxmox-Snapshot nicht möglich ({exc}) – weiter mit Abzug und Archiv.", "warning")
        return False
    _rep(m, "snapshot", {"host_id": host.id, "node": link.node, "vmid": link.vmid, "guest_type": link.guest_type,
                         "name": name, "upid": upid})
    _stage(m, "snapshotting", f"Proxmox-Snapshot {name} angestoßen.")
    return True


async def advance(db: AsyncSession, m: DbMigration) -> None:
    agent = await db.get(Agent, m.agent_id)
    if agent is None:
        _stage(m, "failed", "Rechner existiert nicht mehr.", "error")
        return
    now = datetime.utcnow()
    age = (now - (m.stage_since or now)).total_seconds()
    plan = dict(m.plan or {})

    if m.status == "analyzing":
        cmd = await _cmd(db, m, "analyze")
        if not _done(cmd):
            return
        r = _result(cmd)
        if r.get("fail"):
            await _finish(db, m, agent, False, f"Analyse: {r['fail']}")
            return
        size = int(r.get("datasize") or 0)
        free_bk, free_docker = int(r.get("free_bk") or 0), int(r.get("free_docker") or 0)
        try:
            mounts = json.loads(r.get("mounts") or "[]")
        except ValueError:
            mounts = []
        datadir = (r.get("datadir") or "").strip()
        data_mount = next((mt for mt in mounts if (mt.get("Destination") or "").rstrip("/") == datadir.rstrip("/")
                           or datadir.startswith((mt.get("Destination") or "#").rstrip("/") + "/")), None)
        old_v, new_v = r.get("oldver") or "", r.get("newver") or ""
        path = "major" if is_major_jump(m.engine, old_v, new_v) else "minor"
        plan.update({"image": r.get("image") or m.image, "image_id": r.get("image_id"),
                     "target": r.get("target"), "target_id": r.get("target_id"),
                     "old_version": old_v, "new_version": new_v, "path": path,
                     "datadir": datadir, "data_mount": data_mount, "data_size": size,
                     "project": r.get("project") or "", "service": r.get("service") or "",
                     "workdir": r.get("workdir") or "", "files": r.get("files") or "",
                     "env_keys": (r.get("envkeys") or "").split(), "backup_path": r.get("bk"),
                     "same_image": (r.get("image_id") or "") == (r.get("target_id") or "")})
        m.plan = plan
        _rep(m, "analyze", {"counts": r["counts"], "warnings": r["warnings"], "state": r.get("state"),
                            "free_backup": free_bk, "free_docker": free_docker})
        problems = []
        if r.get("state") != "running":
            problems.append(f"Der Container läuft nicht ({r.get('state')}) – ohne laufende Datenbank kein Abzug.")
        need = size * 3 + 1_000_000_000
        if free_bk and free_bk < need:
            problems.append(f"Zu wenig Platz im Sicherungsordner: frei {free_bk // 2**20} MB, nötig etwa {need // 2**20} MB.")
        if free_docker and free_docker < size * 2 + 500_000_000:
            problems.append(f"Zu wenig Platz für Docker (Probelauf): frei {free_docker // 2**20} MB.")
        if path == "major" and not data_mount:
            problems.append("Das Datenverzeichnis liegt nicht auf einem Volume/Bind-Mount – ein Hauptversions-Update "
                            "würde die Daten mit dem Container verwerfen. Bitte zuerst ein Volume einrichten.")
        if m.engine in ("mariadb", "mysql") and "MARIADB_ROOT_PASSWORD" not in plan["env_keys"] \
                and "MYSQL_ROOT_PASSWORD" not in plan["env_keys"] \
                and not any(k.endswith("ROOT_PASSWORD_FILE") for k in plan["env_keys"]):
            problems.append("Kein Root-Passwort in der Container-Umgebung (MARIADB_ROOT_PASSWORD/MYSQL_ROOT_PASSWORD) – "
                            "ohne lässt sich nicht vollständig sichern.")
        if plan.get("same_image") and path == "minor":
            _log(m, "Ziel-Image ist identisch mit dem laufenden – es gibt nichts zu aktualisieren.", "warning")
        _log(m, f"Analyse: {m.engine} {short_version(old_v) or '?'} → {short_version(new_v) or '?'} "
                f"({'Hauptversions-Sprung' if path == 'major' else 'gleiche Hauptversion'}), "
                f"{size // 2**20} MB Daten, {len(r['counts'])} Tabellen/Sammlungen.")
        if problems:
            _rep(m, "problems", problems)
            await _finish(db, m, agent, False, "Vorprüfung: " + " ".join(problems))
            return
        _stage(m, "backing_up", "Sicherung (logischer Abzug) läuft.")
        await _queue_shell(db, m, agent, "backup", script_backup(m), 4 * 3600)
        return

    if m.status == "backing_up":
        cmd = await _cmd(db, m, "backup")
        if not _done(cmd):
            return
        r = _result(cmd)
        if r.get("fail"):
            await _finish(db, m, agent, False, f"Sicherung: {r['fail']}")
            return
        _rep(m, "backup", {"dump": r.get("dump"), "size": int(r.get("dumpsize") or 0), "sha256": r.get("sha")})
        _stage(m, "dryrun", f"Abzug fertig ({int(r.get('dumpsize') or 0) // 2**20} MB). Probelauf mit "
                            f"{plan.get('target') or m.image} läuft.")
        await _queue_shell(db, m, agent, "dryrun", script_dryrun(m), 4 * 3600)
        return

    if m.status == "dryrun":
        cmd = await _cmd(db, m, "dryrun")
        if not _done(cmd):
            return
        r = _result(cmd)
        before = ((m.report or {}).get("analyze") or {}).get("counts") or {}
        cmp_ = compare_counts(before, r["counts"]) if not r.get("fail") else {}
        errs = real_errors(m.engine, r["errors"])
        fix_fail = [f for f in r["fixups"] if f.get("rc") not in ("0", 0)]
        dry = {"compare": cmp_, "errors": r["errors"][:80], "real_errors": errs[:80], "logs": r["logs"][-15:],
               "restore_seconds": r.get("restore_seconds"), "new_version": r.get("newver"),
               "fixups": r["fixups"], "fail": r.get("fail"),
               "at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
        _rep(m, "dryrun", dry)
        ok = not r.get("fail") and cmp_.get("ok") and not errs and not fix_fail
        review = await _ai_review(db, m)
        if review:
            _rep(m, "ai", review)
        if not ok:
            reasons = []
            if r.get("fail"):
                reasons.append(r["fail"])
            if errs:
                reasons.append(f"{len(errs)} Fehler beim Einspielen")
            if cmp_.get("missing"):
                reasons.append(f"{len(cmp_['missing'])} Tabelle(n) fehlen")
            if cmp_.get("diffs"):
                reasons.append(f"{len(cmp_['diffs'])} Tabelle(n) mit abweichender Zeilenzahl")
            if fix_fail:
                reasons.append(f"{len(fix_fail)} Anpassung(en) fehlgeschlagen")
            _stage(m, "dryrun_failed", "Probelauf nicht sauber: " + "; ".join(reasons) + ". Die echte Datenbank ist "
                   "unverändert.", "warning")
            await notify.push(db, category="patch", level="warning",
                              title=f"{agent.hostname}: Probelauf für {m.container} nicht sauber",
                              body="; ".join(reasons) + (f" — KI: {review.get('summary')}" if review and review.get("summary") else ""),
                              link=f"/rmm/db-updates?id={m.id}", action="db_approve",
                              data={"migration_id": m.id, "agent_uid": agent.agent_uid},
                              event="patch_approval", key=f"db:dry:{m.id}", commit=False)
            return
        _log(m, f"Probelauf sauber: {cmp_.get('tables_after')} Tabellen, {cmp_.get('rows_after')} Zeilen – "
                f"alles wie im Original.")
        if not before and int(plan.get("data_size") or 0) > 50_000_000:
            _log(m, "Achtung: keine Tabellen gezählt, obwohl Daten vorhanden sind – Vergleich nicht aussagekräftig. "
                    "Bitte den Bericht prüfen.", "warning")
        auto = (m.mode == "auto" and plan.get("path") == "minor" and plan.get("auto_execute_minor", True)
                and bool(before)
                and (not review or review.get("recommendation") in ("go", "", None)))
        if auto:
            m.approved_by = "system (Probelauf sauber, gleiche Hauptversion)"
            _stage(m, "approved", "Automatisch freigegeben: gleiche Hauptversion, Probelauf sauber.")
            return
        _stage(m, "awaiting_approval", "Wartet auf Freigabe" + (" (Hauptversions-Sprung)." if plan.get("path") == "major" else "."))
        await notify.push(db, category="patch", level="warning",
                          title=f"{agent.hostname}: DB-Update {m.container} wartet auf Freigabe",
                          body=f"{m.engine} {short_version(plan.get('old_version'))} → {short_version(plan.get('new_version'))}. Probelauf sauber"
                               + (f" — KI: {review.get('summary')}" if review and review.get("summary") else "."),
                          link=f"/rmm/db-updates?id={m.id}", action="db_approve",
                          data={"migration_id": m.id, "agent_uid": agent.agent_uid},
                          event="patch_approval", key=f"db:approve:{m.id}", commit=False)
        return

    if m.status == "approved":
        if await _link_snapshot(db, m, agent):
            return
        await _start_execution(db, m, agent)
        return

    if m.status == "snapshotting":
        snap = (m.report or {}).get("snapshot") or {}
        host = await db.get(PveHost, snap.get("host_id") or 0)
        try:
            st = await pve.task_status(host, pve.node_of_upid(snap.get("upid", ""), snap.get("node", "")),
                                       snap.get("upid", "")) if host else {"done": True, "ok": False, "exitstatus": "Host fehlt"}
        except pve.PveError as exc:
            st = {"done": age > 1800, "ok": False, "exitstatus": str(exc)}
        if not st["done"] and age < 3 * 3600:
            return
        if st.get("ok"):
            _log(m, f"Snapshot {snap.get('name')} fertig.")
        else:
            _log(m, f"Snapshot fehlgeschlagen ({st.get('exitstatus')}) – weiter mit Abzug und Archiv.", "warning")
        await _start_execution(db, m, agent)
        return

    if m.status == "final_backup":            # gleiche Hauptversion
        cmd = await _cmd(db, m, "final_backup")
        if not _done(cmd):
            return
        r = _result(cmd)
        if r.get("fail"):
            await _finish(db, m, agent, False, f"Letzte Sicherung: {r['fail']} – nichts geändert.")
            return
        _rep(m, "final_backup", {"dump": r.get("dump"), "size": int(r.get("dumpsize") or 0)})
        _stage(m, "updating", "Container wird aktualisiert (mit Gesundheitsprüfung).")
        await _queue_docker_update(db, m, agent)
        return

    if m.status == "archiving":               # Hauptversion
        cmd = await _cmd(db, m, "prepare")
        if not _done(cmd):
            return
        r = _result(cmd)
        prep = {"rollback_image": r.get("rollback_image"), "archive": r.get("archive"),
                "archive_size": int(r.get("archivesize") or 0), "dump": r.get("dump"),
                "counts": r["counts"], "stopped": r["stopped"], "cleared": bool(r.get("cleared"))}
        _rep(m, "prepare", prep)
        if r.get("fail"):
            if prep["cleared"] or (r.get("archive") and not prep["cleared"] and "leeren" in r["fail"]):
                _log(m, f"Vorbereitung: {r['fail']} – Rückweg startet.", "error")
                m.error = r["fail"]
                _stage(m, "rolling_back", "Rückweg (automatisch).", "warning")
                await _queue_shell(db, m, agent, "rollback", script_rollback(m), 3600)
            else:
                await _queue_shell(db, m, agent, "siblings", script_start_siblings(m), 600)
                await _finish(db, m, agent, False, f"Vorbereitung: {r['fail']} – Datenbank unverändert.")
            return
        _log(m, f"Abzug, Archiv ({prep['archive_size'] // 2**20} MB) und altes Image gesichert; "
                f"Datenverzeichnis geleert. {len(prep['stopped'])} Projekt-Container angehalten.")
        _stage(m, "updating", "Container wird mit dem neuen Image neu angelegt.")
        await _queue_docker_update(db, m, agent)
        return

    if m.status == "updating":
        cmd = await _cmd(db, m, "update")
        if not _done(cmd):
            if age > 2 * 3600:
                await _fail_or_rollback(db, m, agent, "Container-Update hat nicht geantwortet.")
            return
        try:
            res = json.loads(cmd.stdout or "{}")
        except ValueError:
            res = {}
        outcome = next((o for o in (res.get("results") or []) if o.get("name") == m.container), {})
        _rep(m, "update", {"status": outcome.get("status") or cmd.status, "message": outcome.get("message"),
                           "command_status": cmd.status})
        if outcome.get("status") not in ("updated", "unchanged"):
            msg = f"Container-Update: {outcome.get('status') or cmd.status} — {outcome.get('message') or (cmd.stderr or '')[-300:]}"
            await _fail_or_rollback(db, m, agent, msg)
            return
        fresh = plan.get("path") == "major"
        _stage(m, "restoring" if fresh else "verifying",
               "Neue Version läuft. " + ("Daten werden eingespielt." if fresh else "Prüfung läuft."))
        await _queue_shell(db, m, agent, "verify", script_restore_and_verify(m, fresh), 4 * 3600)
        return

    if m.status in ("restoring", "verifying"):
        cmd = await _cmd(db, m, "verify")
        if not _done(cmd):
            return
        r = _result(cmd)
        ref = ((m.report or {}).get("prepare") or {}).get("counts") or ((m.report or {}).get("analyze") or {}).get("counts") or {}
        cmp_ = compare_counts(ref, r["counts"])
        errs = real_errors(m.engine, r["errors"])
        fix_fail = [f for f in r["fixups"] if f.get("rc") not in ("0", 0)]
        _rep(m, "verify", {"compare": cmp_, "errors": r["errors"][:80], "real_errors": errs[:80],
                           "new_version": r.get("newver"), "post_rc": r.get("post_rc"), "fixups": r["fixups"],
                           "fail": r.get("fail")})
        problems = []
        if r.get("fail"):
            problems.append(r["fail"])
        # Zwischen Analyse und Ausfuehrung kann sich bei laufenden Anwendungen
        # (gleiche Hauptversion, nichts angehalten) die Zeilenzahl aendern -
        # dort zaehlt nur, dass keine Tabelle fehlt.
        if cmp_.get("missing"):
            problems.append(f"{len(cmp_['missing'])} Tabelle(n) fehlen")
        if plan.get("path") == "major" and cmp_.get("diffs"):
            problems.append(f"{len(cmp_['diffs'])} Tabelle(n) mit abweichender Zeilenzahl")
        if plan.get("path") == "major" and errs:
            problems.append(f"{len(errs)} Fehler beim Einspielen")
        if fix_fail:
            problems.append(f"{len(fix_fail)} Anpassung(en) fehlgeschlagen")
        if plan.get("path") == "major" and r.get("newver") and plan.get("new_version") \
                and major_of(r["newver"]) != major_of(plan["new_version"]):
            problems.append(f"Der Container läuft mit {r['newver']} statt {plan['new_version']} – steht das neue "
                            "Image schon in der Compose-Datei?")
        if problems:
            await _fail_or_rollback(db, m, agent, "Prüfung nach dem Update: " + "; ".join(problems))
            return
        await _queue_shell(db, m, agent, "siblings", script_start_siblings(m), 600)
        hint = ""
        if plan.get("path") == "major" and plan.get("project") and plan.get("target") and \
                plan.get("target") != plan.get("image"):
            hint = (f" Falls noch nicht geschehen: in der Compose-Datei von {plan.get('project')} das Image auf "
                    f"{plan.get('target')} setzen – sonst holt das nächste 'docker compose up' das alte zurück.")
        await _finish(db, m, agent, True,
                      f"{m.engine} {short_version(plan.get('old_version'))} → {short_version(r.get('newver') or plan.get('new_version'))}: "
                      f"{cmp_.get('tables_after')} Tabellen, {cmp_.get('rows_after')} Zeilen geprüft." + hint)
        await _queue_shell(db, m, agent, "cleanup", script_cleanup(m), 600)
        return

    if m.status == "rolling_back":
        cmd = await _cmd(db, m, "rollback")
        if not _done(cmd):
            return
        r = _result(cmd)
        _rep(m, "rollback", {"fail": r.get("fail"), "recreated": r.get("recreated"),
                             "started": r["started"], "ready": r.get("ready")})
        if r.get("fail"):
            await _finish(db, m, agent, False, f"Rückweg fehlgeschlagen: {r['fail']} Sicherungen liegen unter "
                                               f"{plan.get('backup_path')}.")
            return
        extra = ""
        if plan.get("project"):
            extra = (f" Wichtig: In der Compose-Datei von {plan.get('project')} muss wieder {plan.get('image')} "
                     f"stehen (gerade läuft das gesicherte Image {((m.report or {}).get('prepare') or {}).get('rollback_image')}).")
        await _finish(db, m, agent, False, f"Zurückgeholt: alte Version mit altem Datenverzeichnis läuft wieder. "
                                           f"Ursache: {m.error or 'von Hand angestoßen'}." + extra, status="rolled_back")
        return


async def _start_execution(db: AsyncSession, m: DbMigration, agent: Agent) -> None:
    if (m.plan or {}).get("path") == "major":
        _stage(m, "archiving", "Hauptversion: Projekt anhalten, letzter Abzug, Archiv des Datenverzeichnisses.")
        await _queue_shell(db, m, agent, "prepare", script_major_prepare(m), 4 * 3600)
    else:
        _stage(m, "final_backup", "Letzter Abzug vor dem Update.")
        await _queue_shell(db, m, agent, "final_backup", script_backup(m, "final"), 4 * 3600)


async def _fail_or_rollback(db: AsyncSession, m: DbMigration, agent: Agent, text: str) -> None:
    m.error = text[:2000]
    if (m.plan or {}).get("path") == "major" and ((m.report or {}).get("prepare") or {}).get("archive"):
        _stage(m, "rolling_back", f"{text} – Rückweg startet automatisch.", "error")
        await _queue_shell(db, m, agent, "rollback", script_rollback(m), 3600)
        return
    await _finish(db, m, agent, False, text + (" Der Container wurde vom Agent auf das alte Image "
                                               "zurückgesetzt, die Daten sind unverändert."
                                               if (m.plan or {}).get("path") != "major" else ""))


async def tick() -> None:
    async with async_session() as db:
        rows = (await db.execute(select(DbMigration).where(
            DbMigration.status.in_(ACTIVE + ("approved",))))).scalars().all()
        for m in rows:
            try:
                await advance(db, m)
            except Exception as exc:                                # defensiv
                logger.exception("DB-Update #%s: %s", m.id, exc)
                _log(m, f"Interner Fehler: {exc}", "error")
            await db.commit()


async def loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await tick()
        except Exception as exc:                                    # defensiv
            logger.warning("DB-Update-Takt: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)


# =============================================================================
# Ausgabe
# =============================================================================
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def public(m: DbMigration, agent: Optional[Agent] = None, full: bool = True) -> dict:
    plan = dict(m.plan or {})
    out = {"id": m.id, "agent_id": m.agent_id, "hostname": agent.hostname if agent else None,
           "agent_uid": agent.agent_uid if agent else None, "container": m.container, "engine": m.engine,
           "image": m.image, "target_image": m.target_image, "mode": m.mode, "status": m.status,
           "status_label": STATUS_LABEL.get(m.status, m.status), "stage_since": _iso(m.stage_since),
           "path": plan.get("path"), "old_version": short_version(plan.get("old_version")),
           "new_version": short_version(plan.get("new_version")),
           "data_size": plan.get("data_size"), "created_by": m.created_by, "approved_by": m.approved_by,
           "error": m.error, "created_at": _iso(m.created_at), "finished_at": _iso(m.finished_at),
           "can_approve": m.status in ("awaiting_approval", "dryrun_failed"),
           "can_rollback": plan.get("path") == "major" and bool(((m.report or {}).get("prepare") or {}).get("archive"))
           and m.status in ("failed", "succeeded"),
           "can_cancel": m.status in ("analyzed", "awaiting_approval", "dryrun_failed", "approved", "analyzing",
                                      "backing_up", "dryrun"),
           "can_rerun": m.status in ("analyzed", "awaiting_approval", "dryrun_failed")}
    if full:
        out.update({"plan": plan, "report": m.report or {}, "log": m.log or []})
    return out


async def candidates(db: AsyncSession) -> List[dict]:
    """Datenbank-Container aller Rechner - mit offenem Update und laufendem Vorgang."""
    agents = (await db.execute(select(Agent).where(Agent.docker_inventory.is_not(None)))).scalars().all()
    active = {(m.agent_id, m.container): m for m in (await db.execute(
        select(DbMigration).where(DbMigration.status.notin_(FINAL)))).scalars()}
    items = {(i.agent_id, i.key): i for i in (await db.execute(select(PatchItem).where(
        PatchItem.kind == "docker", PatchItem.status.notin_(("installed", "superseded"))))).scalars()}
    out = []
    for a in agents:
        for c in (a.docker_inventory or {}).get("containers") or []:
            eng = engine_for_image(c.get("image") or "")
            if not eng:
                continue
            it = items.get((a.id, c.get("name")))
            mig = active.get((a.id, c.get("name")))
            out.append({"agent_uid": a.agent_uid, "hostname": a.hostname, "container": c.get("name"),
                        "image": c.get("image"), "engine": eng, "state": c.get("state"),
                        "local_version": c.get("local_version"), "remote_version": c.get("remote_version"),
                        "update_available": bool(c.get("update_available")),
                        "compose_project": c.get("compose_project"),
                        "patch_item_id": it.id if it else None, "patch_status": it.status if it else None,
                        "migration": public(mig, a, full=False) if mig else None})
    return sorted(out, key=lambda x: (not x["update_available"], x["hostname"] or "", x["container"] or ""))


async def from_patch_items(db: AsyncSession, agent: Agent, items: List[PatchItem], user: str) -> List[PatchItem]:
    """Patch-Lauf: DB-Container herausnehmen und als DB-Update mit Probelauf
    anlegen. Gibt die uebrigen Updates zurueck."""
    rest = []
    for it in items:
        image = (it.data or {}).get("image", "")
        if it.kind == "docker" and is_db_image(image):
            try:
                m = await create(db, agent, it.key, user, mode="auto", patch_item_id=it.id)
                it.status = "installing"
                it.data = {**(it.data or {}), "db_migration_id": m.id}
            except ValueError as exc:
                logger.info("DB-Update für %s nicht angelegt: %s", it.key, exc)
                it.status, it.error = "needs_approval", str(exc)[:2000]
            continue
        rest.append(it)
    return rest
