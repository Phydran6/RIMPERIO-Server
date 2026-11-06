# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Plattenwaechter: raeumt auf, BEVOR die Platte voll ist
# ==============================================================================
"""
Der Plattenwaechter.

**Was vorher nicht funktioniert hat, und warum.** Die alte Fassung wartete bis
90 % und rief dann `VACUUM FULL logs`. Genau das kann an dieser Stelle nicht
klappen: `VACUUM FULL` schreibt die Tabelle komplett neu und braucht dafuer
noch einmal so viel freien Platz, wie die Tabelle gross ist. Bei 90 % Belegung
ist dieser Platz nicht da - der Lauf bricht ab oder treibt die Belegung sogar
ueber 95 %. Ab dort half dann nur noch ein `TRUNCATE`, also *alle* Logs weg.
Und weil der erste Blick auf die Platte erst nach fuenf Minuten Wartezeit kam,
lief ein voller Server nach einem Neustart erst einmal weiter voll.

**Wie es jetzt laeuft.** Der Waechter arbeitet vorbeugend statt in Panik:

1. Er sieht sofort nach dem Start nach, dann im kurzen Takt (Standard 60 s).
2. Er misst wie `df`: belegt gegenueber dem, was einem normalen Benutzer
   tatsaechlich zur Verfuegung steht (`f_bavail`). Die frueher genutzte
   Rechnung `used/total` zaehlt die fuer root reservierten Bloecke als frei
   und meldet deshalb zu wenig.
3. Ab der **Vorwarnstufe** (Standard 80 %) faengt er an zu raeumen, und zwar
   vom Unwichtigsten aufwaerts: abgelaufene Einmal-Token, altes Systemtagebuch,
   dann Logs jenseits der eingestellten Aufbewahrung.
4. Reicht das nicht, **verkuerzt er die Aufbewahrung schrittweise** und loescht
   die jeweils aeltesten Zeilen - in Haeppchen von je 50.000, jedes fuer sich
   festgeschrieben. Ein einziges riesiges DELETE wuerde das Transaktionslog
   aufblaehen und die Platte waehrend des Aufraeumens noch voller machen.
5. Nach jedem Haeppchen ein gewoehnliches `VACUUM` (nicht `FULL`). Das gibt den
   Platz zwar nicht an das Betriebssystem zurueck, aber es gibt ihn *innerhalb*
   der Datenbank wieder frei - und damit hoert die Datei auf zu wachsen. Das
   ist genau das, was in dieser Lage gebraucht wird.
6. `VACUUM FULL` laeuft nur dann, wenn wirklich genug Luft dafuer da ist
   (freier Platz > Tabellengroesse x 1,2). Sonst wird es uebersprungen und das
   auch so vermerkt.
7. Es gibt **keinen automatischen Komplett-Rundumschlag** mehr. Eine
   Mindestmenge (Standard: die letzten 24 Stunden) bleibt immer stehen. Wer das
   anders will, setzt `DISK_ALLOW_TRUNCATE=true` - bewusst und nachlesbar.

Jeder Schritt landet im Systemtagebuch. Wer spaeter fragt "wo sind meine Logs
von letzter Woche?", findet dort die Antwort mitsamt Uhrzeit und Anlass.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import delete, func, select, text

from . import journal
from .events import DISK_PRESSURE, bus
from .models import Log, Setting

logger = logging.getLogger("rimperio.diskguard")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s ist unlesbar - nehme %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s ist unlesbar - nehme %s", name, default)
        return default


# Wie oft nachgesehen wird. Kurz genug, um eine volllaufende Platte zu
# erwischen; das Nachsehen selbst kostet nichts (ein statvfs-Aufruf).
CHECK_INTERVAL = max(15, _env_int("DISK_MONITOR_INTERVAL", 60))

# Die Schwellen. Der Waechter will die Belegung unter ZIEL halten und beginnt
# bei VORWARNUNG damit - lange bevor es eng wird.
THRESHOLD_WARN = _env_float("DISK_USAGE_WARN", 80.0)      # ab hier wird geraeumt
THRESHOLD_HIGH = _env_float("DISK_USAGE_HIGH", 88.0)      # ab hier wird es ernst
THRESHOLD_CRIT = _env_float("DISK_USAGE_THRESHOLD", 93.0)  # ab hier mit Nachdruck
TARGET_USAGE = _env_float("DISK_USAGE_TARGET", 75.0)      # so weit runter wollen wir

MONITOR_PATH = os.getenv("DISK_MONITOR_PATH", "/")

# Aufbewahrung, wenn in den Einstellungen nichts steht.
FALLBACK_RETENTION_DAYS = _env_int("DISK_CLEANUP_RETENTION_DAYS", 30)

# So viel bleibt IMMER stehen, egal wie voll es wird. Ein Log-Server ohne die
# letzten Stunden ist bei einem Zwischenfall wertlos.
MIN_KEEP_HOURS = max(1, _env_int("DISK_MIN_KEEP_HOURS", 24))

# Groesse eines Loeschhaeppchens. Gross genug, um voranzukommen, klein genug,
# dass Transaktionslog und Sperren niemanden stoeren.
BATCH_SIZE = max(1000, _env_int("DISK_CLEANUP_BATCH", 50_000))

# Obergrenze fuer einen einzelnen Lauf - der Waechter soll nicht stundenlang
# loeschen, sondern beim naechsten Takt weitermachen.
MAX_BATCHES = max(1, _env_int("DISK_CLEANUP_MAX_BATCHES", 40))

# Der Rundumschlag ("alle Logs weg") ist ausgeschaltet. Bewusst.
ALLOW_TRUNCATE = os.getenv("DISK_ALLOW_TRUNCATE", "false").strip().lower() in ("1", "true", "yes", "on")

# Ab hier nimmt der Server keine Logs mehr an (HTTPS-Agents bekommen 503 und
# puffern bei sich, Syslog verwirft). Lieber ein paar Minuten nichts annehmen
# als eine Datenbank, die wegen voller Platte gar nicht mehr startet - genau
# das ist am 2026-10-10 passiert.
INGEST_STOP = _env_float("DISK_INGEST_STOP", 95.0)

# Notreserve: eine Platzhalter-Datei, die bei voller Platte geloescht wird.
# Dann hat Postgres wieder Luft zum Starten und der Waechter zum Aufraeumen.
# Ohne sie half am 2026-10-10 nur Handarbeit per SSH.
RESERVE_MB = max(0, _env_int("DISK_RESERVE_MB", 1024))
RESERVE_RELEASE = _env_float("DISK_RESERVE_RELEASE", 97.0)
RESERVE_PATH = os.path.join(os.getenv("RIMPERIO_DATA_DIR", "/app/data"), ".notreserve")

# Die eingestellte Aufbewahrung gilt immer - nicht erst, wenn die Platte voll
# wird. Frueher wurde sie nur ab der Vorwarnstufe durchgesetzt: bis 80 %
# wuchs alles einfach weiter, egal was eingestellt war.
RETENTION_INTERVAL = max(300, _env_int("LOG_RETENTION_INTERVAL", 3600))

# Unwichtige Stufen, die auf Wunsch kuerzer aufbewahrt werden.
LOW_LEVELS = ("debug", "info", "notice")

# Nicht bei jedem Takt dieselbe Warnung ins Tagebuch schreiben.
_ANNOUNCE_EVERY = timedelta(minutes=30)
_last_announced: Optional[datetime] = None


# =============================================================================
# Messen
# =============================================================================
def usage(path: str = "") -> dict:
    """Belegung des Dateisystems - so gerechnet, wie `df` es anzeigt.

    `shutil.disk_usage` liefert used/total und zaehlt damit die fuer root
    reservierten fuenf Prozent als frei. Genau diese fuenf Prozent sind aber
    der Unterschied zwischen "90 %" und "voll".
    """
    target = path or MONITOR_PATH
    try:
        stat = os.statvfs(target)
        block = stat.f_frsize or stat.f_bsize
        total = stat.f_blocks * block
        free_for_us = stat.f_bavail * block
        used = (stat.f_blocks - stat.f_bfree) * block
        usable_total = used + free_for_us
        percent = (used / usable_total * 100.0) if usable_total else 0.0
    except (AttributeError, OSError):
        # Kein statvfs (Windows) oder Pfad weg: die einfache Rechnung tut es auch.
        info = shutil.disk_usage(target)
        total, used, free_for_us = info.total, info.used, info.free
        percent = (used / total * 100.0) if total else 0.0

    return {
        "path": target,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free_for_us,
        "percent": round(percent, 1),
    }


_ingest_checked_at = 0.0
_ingest_blocked = False


def ingest_blocked() -> bool:
    """Ist die Platte so voll, dass keine Logs mehr angenommen werden sollen?

    Wird bei jeder Lieferung gefragt - deshalb hoechstens alle 5 s gemessen.
    """
    global _ingest_checked_at, _ingest_blocked
    now = time.monotonic()
    if now - _ingest_checked_at > 5:
        _ingest_checked_at = now
        try:
            _ingest_blocked = usage()["percent"] >= INGEST_STOP
        except Exception:                                           # defensiv
            _ingest_blocked = False
    return _ingest_blocked


# =============================================================================
# Notreserve
# =============================================================================
def _reserve_bytes() -> int:
    """Groesse der Reserve: wie eingestellt, aber hoechstens 5 % der Platte."""
    if RESERVE_MB <= 0:
        return 0
    try:
        total = usage(os.path.dirname(RESERVE_PATH))["total_bytes"]
    except Exception:
        return 0
    return int(min(RESERVE_MB * 1024 * 1024, total * 0.05))


def reserve_present() -> bool:
    try:
        return os.path.getsize(RESERVE_PATH) > 0
    except OSError:
        return False


def _create_reserve(size: int) -> None:
    tmp = RESERVE_PATH + ".tmp"
    with open(tmp, "wb") as handle:
        try:
            os.posix_fallocate(handle.fileno(), 0, size)
        except (AttributeError, OSError):
            # Dateisystem ohne fallocate: echte Nullen schreiben. Sparse-Dateien
            # (truncate) taugen nicht - die belegen keinen Platz.
            chunk = b"\0" * (8 * 1024 * 1024)
            written = 0
            while written < size:
                handle.write(chunk[: min(len(chunk), size - written)])
                written += len(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, RESERVE_PATH)


async def ensure_reserve(percent: float) -> None:
    """Legt die Reserve an, wenn wieder reichlich Platz ist."""
    size = _reserve_bytes()
    if not size or reserve_present() or percent >= TARGET_USAGE:
        return
    try:
        await asyncio.to_thread(_create_reserve, size)
        logger.info("Notreserve angelegt: %s (%s)", RESERVE_PATH, _human(size))
    except Exception as exc:                                        # defensiv
        logger.warning("Notreserve nicht angelegt: %s", exc)
        try:
            os.remove(RESERVE_PATH + ".tmp")
        except OSError:
            pass


async def release_reserve(percent: float) -> bool:
    """Gibt die Reserve frei - der letzte Ausweg, bevor die Datenbank stirbt."""
    if not reserve_present():
        return False
    try:
        size = os.path.getsize(RESERVE_PATH)
        os.remove(RESERVE_PATH)
    except OSError as exc:
        logger.error("Notreserve konnte nicht freigegeben werden: %s", exc)
        return False
    logger.critical("Platte bei %s %% - Notreserve (%s) freigegeben", percent, _human(size))
    from . import alerts
    await alerts.dispatch(
        "disk", "Platte voll – Notreserve freigegeben",
        (f"Die Platte war bei {percent} %. RIMPERIO hat seine Notreserve "
         f"({_human(size)}) gelöscht, damit die Datenbank weiterläuft. "
         f"Das verschafft nur Zeit: bitte Platz schaffen oder die Aufbewahrung kürzen."),
        level="critical", key="disk:reserve")
    await journal.write(
        category="retention", event="disk.reserve.released", level="critical",
        message=f"Notreserve freigegeben ({_human(size)}) bei {percent} % Belegung.",
        actor="system", ok=False, detail={"percent": percent, "bytes": size})
    return True


async def _database_size(session) -> Tuple[int, int]:
    """(Groesse der logs-Tabelle, Groesse der ganzen Datenbank) in Bytes."""
    try:
        logs_bytes = (await session.execute(
            text("SELECT pg_total_relation_size('logs')")
        )).scalar() or 0
        db_bytes = (await session.execute(
            text("SELECT pg_database_size(current_database())")
        )).scalar() or 0
        return int(logs_bytes), int(db_bytes)
    except Exception as exc:                                        # defensiv
        logger.debug("Groesse der Datenbank nicht lesbar: %s", exc)
        return 0, 0


async def _retention_days(session) -> int:
    """Eingestellte Aufbewahrung in Tagen."""
    row = (await session.execute(
        select(Setting).where(Setting.key == "log_retention_days")
    )).scalar_one_or_none()
    if row is None:
        return FALLBACK_RETENTION_DAYS
    try:
        return max(1, int(row.value))
    except (TypeError, ValueError):
        return FALLBACK_RETENTION_DAYS


async def _low_level_days(session) -> int:
    """Kuerzere Aufbewahrung fuer debug/info/notice in Tagen (0 = wie alle)."""
    row = (await session.execute(
        select(Setting).where(Setting.key == "log_retention_days_info")
    )).scalar_one_or_none()
    try:
        return max(0, int(row.value)) if row is not None else 0
    except (TypeError, ValueError):
        return 0


# =============================================================================
# Loeschen in Haeppchen
# =============================================================================
async def delete_older_than(session, cutoff: datetime, budget: int = MAX_BATCHES,
                            levels: Optional[Tuple[str, ...]] = None) -> int:
    """Loescht Logzeilen aelter als `cutoff` - haeppchenweise.

    Jedes Haeppchen wird einzeln festgeschrieben. Das ist wichtig: ein einziges
    DELETE ueber Millionen Zeilen haelt eine Transaktion offen, laesst das
    Transaktionslog wachsen und macht die Platte waehrend des Aufraeumens
    *voller* statt leerer.

    `levels`: nur diese Stufen (z.B. INFO frueher altern lassen als FEHLER).
    """
    removed = 0
    for _ in range(max(1, budget)):
        condition = Log.timestamp < cutoff
        if levels:
            condition = condition & Log.level.in_(levels)
        oldest = (
            select(Log.id)
            .where(condition)
            .order_by(Log.timestamp.asc())
            .limit(BATCH_SIZE)
            .scalar_subquery()
        )
        result = await session.execute(delete(Log).where(Log.id.in_(oldest)))
        await session.commit()
        count = result.rowcount or 0
        removed += count
        if count < BATCH_SIZE:
            break
        # Kurz Luft lassen: der Waechter darf den Server nicht lahmlegen.
        await asyncio.sleep(0)
    return removed


async def vacuum(table: str, full: bool = False) -> bool:
    """`VACUUM` auf einer Tabelle. `full` nur, wenn der Platz dafuer da ist."""
    from .database import engine

    command = f"VACUUM (FULL, ANALYZE) {table}" if full else f"VACUUM (ANALYZE) {table}"
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(command)
        logger.info("%s abgeschlossen", command)
        return True
    except Exception as exc:
        logger.error("%s fehlgeschlagen: %s", command, exc)
        return False


async def maybe_vacuum_full(session) -> dict:
    """`VACUUM FULL` - aber nur, wenn genug Luft dafuer da ist.

    Faustregel: der freie Platz muss die Tabelle noch einmal aufnehmen koennen,
    mit 20 % Sicherheitsaufschlag. Sonst wird uebersprungen; ein gescheitertes
    `VACUUM FULL` bei 93 % Belegung hilft niemandem, es macht es schlimmer.
    """
    logs_bytes, _ = await _database_size(session)
    free = usage()["free_bytes"]
    needed = int(logs_bytes * 1.2)

    if logs_bytes and free < needed:
        message = (f"VACUUM FULL übersprungen: es sind {_human(free)} frei, "
                   f"gebraucht würden etwa {_human(needed)}. "
                   f"Stattdessen läuft ein gewöhnliches VACUUM.")
        logger.warning(message)
        await vacuum("logs", full=False)
        return {"full": False, "skipped": True, "reason": message}

    ok = await vacuum("logs", full=True)
    return {"full": ok, "skipped": False,
            "reason": "" if ok else "VACUUM FULL ist fehlgeschlagen."}


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


# =============================================================================
# Regelmaessige Aufbewahrung - unabhaengig vom Platz
# =============================================================================
async def enforce_retention(session) -> Tuple[int, list]:
    """Setzt die eingestellte Aufbewahrung durch. Gibt (geloescht, Schritte)."""
    steps: list = []
    deleted = 0
    days = await _retention_days(session)
    count = await delete_older_than(session, datetime.utcnow() - timedelta(days=days))
    if count:
        deleted += count
        steps.append(f"{count:,} Logzeilen älter als {days} Tage entfernt".replace(",", "."))

    low_days = await _low_level_days(session)
    if 0 < low_days < days:
        count = await delete_older_than(
            session, datetime.utcnow() - timedelta(days=low_days), levels=LOW_LEVELS)
        if count:
            deleted += count
            steps.append(f"{count:,} INFO/DEBUG-Zeilen älter als {low_days} Tage entfernt"
                         .replace(",", "."))
    return deleted, steps


async def retention_pass() -> None:
    """Der stuendliche Lauf. Ein gewoehnliches VACUUM danach gibt den Platz
    innerhalb der Datenbank frei - dann waechst die Datei nicht weiter."""
    from .database import async_session

    async with async_session() as session:
        deleted, steps = await enforce_retention(session)
        if not deleted:
            return
        await vacuum("logs", full=False)
        await journal.record(
            session, category="retention", event="retention.auto", level="notice",
            message=f"Aufbewahrung durchgesetzt: {'; '.join(steps)}.",
            actor="system", detail={"deleted": deleted, "steps": steps})


# =============================================================================
# Ein Aufraeumlauf
# =============================================================================
async def cleanup(reason: str = "automatisch", force: bool = False,
                  actor: str = "system") -> dict:
    """Raeumt so weit auf, bis das Ziel erreicht ist - oder nichts mehr geht.

    Gibt einen Bericht zurueck: was getan wurde, wie viel es gebracht hat, und
    was bewusst NICHT getan wurde. Dieser Bericht landet auch im Tagebuch.
    """
    from .database import async_session
    from .models import AppLoginToken

    started = datetime.utcnow()
    before = usage()
    steps: list = []
    deleted_logs = 0

    async with async_session() as session:
        # -- Stufe 0: Kleinkram, kostet nichts und tut niemandem weh -----------
        try:
            result = await session.execute(
                delete(AppLoginToken).where(
                    (AppLoginToken.expires_at < datetime.utcnow())
                    | (AppLoginToken.used_at.is_not(None))
                )
            )
            await session.commit()
            if result.rowcount:
                steps.append(f"{result.rowcount} abgelaufene App-Token entfernt")
        except Exception as exc:
            await session.rollback()
            logger.warning("App-Token-Aufräumen fehlgeschlagen: %s", exc)

        try:
            pruned = await journal.prune(session)
            if pruned:
                steps.append(f"{pruned} alte Tagebuch-Einträge entfernt")
        except Exception as exc:
            await session.rollback()
            logger.warning("Tagebuch-Aufräumen fehlgeschlagen: %s", exc)

        current = usage()
        if not force and current["percent"] < THRESHOLD_WARN:
            return _report(before, current, steps, deleted_logs, started, reason,
                           "Unterhalb der Vorwarnstufe — nichts weiter zu tun.")

        # -- Stufe 1: die eingestellte Aufbewahrung durchsetzen ----------------
        days = await _retention_days(session)
        count, retention_steps = await enforce_retention(session)
        if count:
            deleted_logs += count
            steps.extend(retention_steps)
            await vacuum("logs", full=False)

        current = usage()
        if current["percent"] <= TARGET_USAGE and not force:
            return _report(before, current, steps, deleted_logs, started, reason,
                           "Die eingestellte Aufbewahrung hat gereicht.")

        # -- Stufe 2: Aufbewahrung schrittweise verkuerzen ---------------------
        # Erst ab der hohen Stufe, und pro Lauf nur EIN Halbierungsschritt -
        # ausser es ist kritisch. Hintergrund: ein gewoehnliches VACUUM gibt den
        # Platz nicht ans Betriebssystem zurueck, die Belegung bleibt also
        # stehen, obwohl die Tabelle nicht mehr waechst. Die alte Fassung
        # halbierte deshalb bei 80-88 % jede Minute weiter, bis nur noch 24 h
        # uebrig waren - auch dann, wenn ganz andere Dateien die Platte fuellten.
        if current["percent"] >= THRESHOLD_HIGH or force:
            aggressive = current["percent"] >= THRESHOLD_CRIT or force
            window_hours = max(MIN_KEEP_HOURS, days * 24)
            while current["percent"] > TARGET_USAGE and window_hours > MIN_KEEP_HOURS:
                window_hours = max(MIN_KEEP_HOURS, window_hours // 2)
                cutoff = datetime.utcnow() - timedelta(hours=window_hours)
                count = await delete_older_than(session, cutoff)
                if count:
                    deleted_logs += count
                    steps.append(f"Aufbewahrung auf {window_hours} h verkürzt, "
                                 f"{count:,} Logzeilen entfernt".replace(",", "."))
                    await vacuum("logs", full=False)
                current = usage()
                if count and not aggressive:
                    break
                if not count and window_hours <= MIN_KEEP_HOURS:
                    break

        # -- Stufe 3: Platz an das Betriebssystem zurueckgeben -----------------
        if current["percent"] > THRESHOLD_HIGH:
            outcome = await maybe_vacuum_full(session)
            steps.append(outcome["reason"] or "Tabelle neu geschrieben (VACUUM FULL).")
            current = usage()

        # -- Stufe 4: der Rundumschlag, nur auf ausdrueckliche Ansage ----------
        if current["percent"] > THRESHOLD_CRIT:
            if ALLOW_TRUNCATE:
                try:
                    await session.execute(text("TRUNCATE TABLE logs RESTART IDENTITY"))
                    await session.commit()
                    steps.append("ALLE Logs entfernt (DISK_ALLOW_TRUNCATE=true).")
                    current = usage()
                except Exception as exc:
                    await session.rollback()
                    steps.append(f"Der Rundumschlag ist fehlgeschlagen: {exc}")
            else:
                steps.append(
                    f"Die Platte liegt weiterhin bei {current['percent']} %. Die letzten "
                    f"{MIN_KEEP_HOURS} h Logs bleiben absichtlich stehen. Hier hilft nur "
                    f"mehr Plattenplatz, eine kürzere Aufbewahrung oder Archivierung "
                    f"auf ein anderes Ziel.")

        result = _report(before, current, steps, deleted_logs, started, reason, "")

        await journal.record(
            session,
            category="retention",
            event="disk.cleanup",
            level=("warning" if current["percent"] > THRESHOLD_HIGH else "notice"),
            message=(f"Aufräumen ({reason}): {before['percent']} % → {current['percent']} %, "
                     f"{deleted_logs:,} Logzeilen entfernt.".replace(",", ".")),
            actor=actor,
            ok=current["percent"] <= THRESHOLD_CRIT,
            duration_ms=int((datetime.utcnow() - started).total_seconds() * 1000),
            detail=result,
        )
        return result


def _report(before: dict, after: dict, steps: list, deleted: int,
            started: datetime, reason: str, note: str) -> dict:
    return {
        "reason": reason,
        "note": note,
        "before_percent": before["percent"],
        "after_percent": after["percent"],
        "freed_bytes": max(0, before["used_bytes"] - after["used_bytes"]),
        "freed_human": _human(max(0, before["used_bytes"] - after["used_bytes"])),
        "deleted_logs": deleted,
        "steps": steps,
        "duration_seconds": round((datetime.utcnow() - started).total_seconds(), 1),
        "thresholds": {
            "warn": THRESHOLD_WARN, "high": THRESHOLD_HIGH,
            "critical": THRESHOLD_CRIT, "target": TARGET_USAGE,
        },
        "min_keep_hours": MIN_KEEP_HOURS,
        "truncate_allowed": ALLOW_TRUNCATE,
    }


# =============================================================================
# Zustand fuer die Oberflaeche
# =============================================================================
async def status() -> dict:
    """Wie voll ist es, was ist eingestellt, wie gross ist die Datenbank?"""
    from .database import async_session

    info = usage()
    logs_bytes = db_bytes = 0
    oldest = newest = None
    rows = 0
    try:
        async with async_session() as session:
            logs_bytes, db_bytes = await _database_size(session)
            span = (await session.execute(
                select(func.min(Log.timestamp), func.max(Log.timestamp))
            )).first()
            if span:
                oldest, newest = span
            rows = (await session.execute(text(
                "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
            ))).scalar() or 0
    except Exception as exc:                                        # defensiv
        logger.debug("Plattenzustand unvollständig: %s", exc)

    percent = info["percent"]
    if percent >= THRESHOLD_CRIT:
        state, advice = "critical", "Es wird mit Nachdruck aufgeräumt."
    elif percent >= THRESHOLD_HIGH:
        state, advice = "high", "Der Wächter räumt bereits auf."
    elif percent >= THRESHOLD_WARN:
        state, advice = "warn", "Der Wächter hat mit dem Aufräumen begonnen."
    else:
        state, advice = "ok", "Genug Platz."

    return {
        **info,
        "used_human": _human(info["used_bytes"]),
        "free_human": _human(info["free_bytes"]),
        "total_human": _human(info["total_bytes"]),
        "state": state,
        "advice": advice,
        "logs_bytes": logs_bytes,
        "logs_human": _human(logs_bytes),
        "database_bytes": db_bytes,
        "database_human": _human(db_bytes),
        "log_rows_estimate": int(rows),
        "oldest_log": oldest,
        "newest_log": newest,
        "thresholds": {"warn": THRESHOLD_WARN, "high": THRESHOLD_HIGH,
                       "critical": THRESHOLD_CRIT, "target": TARGET_USAGE},
        "check_interval_seconds": CHECK_INTERVAL,
        "min_keep_hours": MIN_KEEP_HOURS,
        "truncate_allowed": ALLOW_TRUNCATE,
        "ingest_stop": INGEST_STOP,
        "ingest_blocked": percent >= INGEST_STOP,
        "reserve_present": reserve_present(),
        "reserve_human": _human(_reserve_bytes()),
        "reserve_release": RESERVE_RELEASE,
    }


# =============================================================================
# Der Wachhund
# =============================================================================
async def watch_task() -> None:
    """Sieht im kurzen Takt nach - und zwar sofort, nicht erst nach der Pause."""
    global _last_announced

    logger.info("Plattenwächter läuft (alle %ss, Vorwarnung ab %s %%, Ziel %s %%)",
                CHECK_INTERVAL, THRESHOLD_WARN, TARGET_USAGE)

    # Erst hochfahren lassen, dann gleich der erste Blick. Die alte Fassung
    # wartete hier fuenf Minuten - auf einer schon vollen Platte fuenf Minuten
    # zu viel.
    await asyncio.sleep(10)
    # Erster Aufbewahrungslauf nach 5 min, dann stuendlich.
    next_retention = time.monotonic() + 300

    while True:
        # Notreserve zuerst und OHNE Datenbank: genau dann, wenn sie wegen
        # voller Platte nicht startet, muss das hier noch funktionieren.
        try:
            info = usage()
            if info["percent"] >= RESERVE_RELEASE:
                await release_reserve(info["percent"])
            else:
                await ensure_reserve(info["percent"])
        except Exception as exc:                                    # defensiv
            logger.error("Notreserve: %s", exc)

        if time.monotonic() >= next_retention:
            next_retention = time.monotonic() + RETENTION_INTERVAL
            try:
                await retention_pass()
            except Exception as exc:                                # defensiv
                logger.error("Aufbewahrung: %s", exc)

        try:
            info = usage()
            if info["percent"] >= THRESHOLD_WARN:
                now = datetime.utcnow()
                if _last_announced is None or now - _last_announced > _ANNOUNCE_EVERY:
                    _last_announced = now
                    bus.publish(DISK_PRESSURE, {
                        "percent": info["percent"], "free_human": _human(info["free_bytes"]),
                        "path": info["path"],
                    }, sticky=False)
                await cleanup(reason=f"Belegung {info['percent']} %")
            elif _last_announced is not None and info["percent"] < TARGET_USAGE:
                _last_announced = None
                bus.clear(DISK_PRESSURE)
        except Exception as exc:                                    # defensiv
            logger.error("Plattenwächter: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL)
