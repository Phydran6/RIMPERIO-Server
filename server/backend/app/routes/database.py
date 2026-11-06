# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.16.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Auskunft über die genutzte Datenbank (lokal oder extern)
# ==============================================================================
"""
Zeigt, *wohin* RIMPERIO seine Daten schreibt.

Die Verbindung selbst wird bewusst nicht über die Oberfläche umgestellt: ein
Wechsel der Datenbank braucht einen Neustart der Container und in aller Regel
eine Datenübernahme. Beides gehört auf die Kommandozeile, nicht hinter einen
Knopf, der im Fehlerfall die laufende Installation abschneidet. Eingestellt wird
über `DATABASE_URL` bzw. `DB_HOST/DB_PORT/...` in der `.env` — hier steht, was
davon tatsächlich aktiv ist, damit man eine Fehlkonfiguration sofort sieht.
"""

import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_admin
from ..config import settings
from ..database import get_db
from ..models import User

router = APIRouter(prefix="/api/database", tags=["Database"])


def _target() -> dict:
    """Verbindungsziel ohne Zugangsdaten."""
    url = settings.database_url
    # Alles vor dem @ (Benutzer:Passwort) fliegt raus.
    split = urlsplit(url)
    return {
        "host": split.hostname or settings.db_host,
        "port": split.port or settings.db_port,
        "database": (split.path or "").lstrip("/") or settings.db_name,
        "user": split.username or settings.db_user,
        "external": settings.database_is_external,
        "tls": bool((settings.db_sslmode or "").strip()) or "ssl=" in url,
        "configured_via": "DATABASE_URL" if settings.database_url_override.strip() else "DB_HOST/DB_PORT/…",
    }


@router.get("/status")
async def database_status(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """GET /api/database/status - Ziel, Version, Größe und Auslastung (nur Admin)."""
    info = {"target": _target(), "reachable": False}

    started = time.perf_counter()
    try:
        version = (await db.execute(text("SHOW server_version"))).scalar()
        info["reachable"] = True
        info["server_version"] = version
        info["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    except Exception as exc:
        info["error"] = str(exc)
        return info

    # Kennzahlen einzeln absichern: fehlende Rechte auf einer verwalteten Datenbank
    # (z.B. Cloud-Postgres) sollen nicht die ganze Auskunft kippen.
    try:
        size = (await db.execute(
            text("SELECT pg_size_pretty(pg_database_size(current_database()))")
        )).scalar()
        info["size"] = size
    except Exception:
        info["size"] = None

    try:
        rows = (await db.execute(text(
            "SELECT count(*) FILTER (WHERE state = 'active') AS active, count(*) AS total "
            "FROM pg_stat_activity WHERE datname = current_database()"
        ))).mappings().first()
        info["connections"] = {"active": rows["active"], "total": rows["total"]}
    except Exception:
        info["connections"] = None

    try:
        # Bewusst die Schätzung des Planers statt count(*): bei Millionen Zeilen
        # läuft count(*) durch die ganze Tabelle und blockiert diese Auskunft
        # sekundenlang. Für "wie viel liegt hier ungefähr" reicht reltuples.
        rows = (await db.execute(text(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = 'logs'"
        ))).scalar()
        info["log_rows_estimated"] = int(rows) if rows and rows > 0 else 0
    except Exception:
        info["log_rows_estimated"] = None

    return info
