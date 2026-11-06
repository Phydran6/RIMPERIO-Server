# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.05.13.20.58.33
# Beschreibung: RIMPERIO v2026.03.31.17.26.46 - Settings API Endpoints
# ==============================================================================

import asyncio
import platform
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models import Setting, Log
from ..config import settings as app_settings
from ..schemas import SettingsResponse, SettingUpdate, RetentionResponse, DatabaseSettingsResponse
from ..auth import get_current_user, get_current_admin
from ..limiter import client_ip as real_client_ip
from .. import diskguard, journal

router = APIRouter(prefix="/api/settings", tags=["Settings"])


def _choose_reboot_command():
    """Ermittelt ein geeignetes Reboot-Kommando je nach Plattform."""
    if platform.system().lower() == "windows":
        return ["shutdown", "/r", "/t", "0"]

    candidates = [
        ["systemctl", "reboot"],
        ["/bin/systemctl", "reboot"],
        ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "/sbin/reboot"],
        ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "/sbin/reboot", "-f"],
        ["reboot"],
        ["reboot", "-f"],
        ["/sbin/reboot"],
        ["/usr/sbin/reboot"],
        ["shutdown", "-r", "now"],
        ["/sbin/shutdown", "-r", "now"],
        ["/usr/sbin/shutdown", "-r", "now"],
        ["busybox", "reboot"],
        ["busybox", "reboot", "-f"],
        ["/bin/busybox", "reboot"],
        ["/bin/busybox", "reboot", "-f"],
    ]

    for cmd in candidates:
        exe = cmd[0]
        if shutil.which(exe) or Path(exe).exists():
            return cmd
    return None


def _try_sysrq_reboot() -> bool:
    """Versucht einen direkten SysRq-Reboot auszulösen. Gibt True bei Erfolg."""
    try:
        # sysrq einschalten (idempotent)
        Path("/proc/sys/kernel/sysrq").write_text("1")
        Path("/proc/sysrq-trigger").write_text("b")
        return True
    except Exception:
        return False

@router.get("", response_model=SettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Setting))
    return SettingsResponse(settings={s.key: s.value for s in result.scalars().all()})

@router.get("/database", response_model=DatabaseSettingsResponse)
async def get_database_settings(_=Depends(get_current_admin)):
    return DatabaseSettingsResponse(
        host=app_settings.db_host,
        port=app_settings.db_port,
        user=app_settings.db_user,
        name=app_settings.db_name,
        password=app_settings.db_password or ""
    )

@router.get("/{key}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' nicht gefunden")
    return {"key": setting.key, "value": setting.value}

@router.put("/{key}")
async def update_setting(key: str, data: SettingUpdate, request: Request,
                         db: AsyncSession = Depends(get_db),
                         admin=Depends(get_current_admin)):
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    previous = setting.value if setting else None
    if setting:
        setting.value = data.value
    else:
        setting = Setting(key=key, value=data.value)
        db.add(setting)
    await db.commit()
    await db.refresh(setting)

    # Eine geaenderte Einstellung ist ein Eingriff wie jeder andere - und die
    # Frage "wer hat die Aufbewahrung auf drei Tage gestellt?" soll eine
    # Antwort haben.
    await journal.record(
        db, category="settings", event="setting.changed",
        message=f"Einstellung '{key}' geändert.",
        actor=admin.username, source_ip=real_client_ip(request), target=key,
        detail={"before": previous, "after": data.value})
    return {"key": setting.key, "value": setting.value}

@router.post("/retention/preview", response_model=RetentionResponse)
async def preview_retention(days: int = Query(..., ge=1), db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    count = (await db.execute(select(func.count(Log.id)).where(Log.timestamp < cutoff))).scalar() or 0
    oldest = (await db.execute(select(func.min(Log.timestamp)))).scalar()
    return RetentionResponse(logs_to_delete=count, oldest_log_date=oldest)

@router.post("/retention/execute", response_model=RetentionResponse)
async def execute_retention(days: int = Query(..., ge=1), request: Request = None,
                            db: AsyncSession = Depends(get_db),
                            admin=Depends(get_current_admin)):
    """Loescht Logzeilen jenseits der Aufbewahrung - in Haeppchen.

    Warum haeppchenweise: ein einziges DELETE ueber Millionen Zeilen haelt eine
    Transaktion minutenlang offen, laesst das Transaktionslog wachsen und macht
    die Platte waehrend des Aufraeumens erst einmal voller. Die Aufteilung
    macht genau denselben Job, nur ohne diesen Nebeneffekt - dieselbe Mechanik,
    die auch der Plattenwaechter benutzt.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = await diskguard.delete_older_than(db, cutoff)
    # Gewoehnliches VACUUM: gibt den Platz innerhalb der Datenbank wieder frei,
    # ohne den doppelten Plattenbedarf von VACUUM FULL.
    await diskguard.vacuum("logs", full=False)

    await journal.record(
        db, category="retention", event="retention.manual", level="notice",
        message=f"{deleted} Logzeilen älter als {days} Tage von Hand gelöscht.",
        actor=admin.username,
        source_ip=real_client_ip(request) if request else "",
        detail={"days": days, "deleted": deleted})
    return RetentionResponse(deleted_count=deleted,
                             message=f"{deleted} Logs geloescht")


# =============================================================================
# Plattenwaechter
# =============================================================================
@router.get("/disk/status")
async def disk_status(_=Depends(get_current_admin)):
    """Wie voll ist es, wie gross ist die Datenbank, was sind die Schwellen?"""
    return await diskguard.status()


@router.post("/disk/cleanup")
async def disk_cleanup(request: Request, force: bool = Query(
                           False, description="Auch unterhalb der Vorwarnstufe aufräumen"),
                       admin=Depends(get_current_admin)):
    """Stößt einen Aufräumlauf von Hand an.

    Derselbe Ablauf, den der Wächter von selbst fährt — nur eben jetzt. Der
    Bericht sagt hinterher, was getan wurde und was bewusst *nicht* getan
    wurde.
    """
    return await diskguard.cleanup(
        reason=f"von {admin.username} angestoßen", force=force, actor=admin.username)

@router.delete("/logs/all", response_model=RetentionResponse)
async def delete_all_logs(
    confirm: str = Query(..., min_length=1),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """
    Loescht saemtliche Log-Eintraege via TRUNCATE und gibt Speicher sofort frei.
    Erfordert Bestaetigung "DELETE_ALL_LOGS".

    Das ist der Knopf, den der Plattenwaechter NICHT mehr von selbst drueckt.
    Von Hand gibt es ihn weiterhin - aber mit Bestaetigungswort und mit einem
    Eintrag im Systemtagebuch, der sagt, wer wann wie viel geloescht hat.
    """
    if confirm != "DELETE_ALL_LOGS":
        raise HTTPException(status_code=400, detail="Bestaetigung fehlt oder ist falsch")

    total_logs = (await db.execute(text(
        "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE relname = 'logs'"
    ))).scalar() or 0

    # TRUNCATE innerhalb der laufenden Transaktion ausfuehren
    await db.execute(text("TRUNCATE TABLE logs RESTART IDENTITY"))
    await db.commit()

    await journal.record(
        db, category="retention", event="logs.truncated", level="critical",
        message=f"ALLE Logzeilen gelöscht (rund {total_logs}). Von Hand ausgelöst.",
        actor=admin.username,
        source_ip=real_client_ip(request) if request else "",
        detail={"estimated_rows": int(total_logs)})

    return RetentionResponse(
        deleted_count=total_logs,
        message=f"{total_logs} Logs geloescht (TRUNCATE)" if total_logs else "Keine Logs vorhanden"
    )

@router.post("/logs/compact")
async def compact_logs(
    confirm: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """
    Schreibt die Tabelle `logs` neu und gibt belegten Plattenplatz an das
    Betriebssystem zurueck (VACUUM FULL). Exklusive Sperre - waehrenddessen
    nimmt der Server keine Logzeilen an.

    Wichtig, und genau daran ist die frueher automatische Fassung gescheitert:
    VACUUM FULL braucht noch einmal so viel freien Platz, wie die Tabelle gross
    ist. Ist der nicht da, wird hier nicht blind losgelegt, sondern ein
    gewoehnliches VACUUM gefahren und gesagt, warum.
    """
    if confirm != "COMPACT_LOGS":
        raise HTTPException(status_code=400, detail="Bestaetigung fehlt oder ist falsch")

    outcome = await diskguard.maybe_vacuum_full(db)
    if outcome.get("skipped"):
        return {"message": outcome["reason"], "full": False, "skipped": True}
    if not outcome.get("full"):
        raise HTTPException(status_code=500,
                            detail=outcome.get("reason") or "VACUUM FULL fehlgeschlagen")
    return {"message": "VACUUM FULL logs abgeschlossen", "full": True, "skipped": False}

@router.post("/reboot")
async def reboot_system(request: Request = None, db: AsyncSession = Depends(get_db),
                        admin=Depends(get_current_admin)):
    await journal.record(
        db, category="system", event="host.reboot", level="critical",
        message="Neustart des Servers ausgelöst.",
        actor=admin.username,
        source_ip=real_client_ip(request) if request else "")
    if _try_sysrq_reboot():
        return {"message": "SysRq-Reboot wurde ausgeloest", "method": "sysrq-trigger"}

    cmd = _choose_reboot_command()
    if not cmd:
        raise HTTPException(status_code=500, detail="Kein Neustart-Kommando gefunden")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Reboot fehlgeschlagen: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=2)
        if process.returncode not in (0, None):
            error_output = (stderr or stdout or b"").decode().strip()
            detail = error_output or f"Exit-Code: {process.returncode}"
            raise HTTPException(status_code=500, detail=f"Neustart-Kommando fehlgeschlagen: {detail}")
    except asyncio.TimeoutError:
        # Kommando laeuft noch (System faehrt vermutlich herunter) - trotzdem Erfolg melden
        pass

    return {"message": "Systemneustart wurde ausgeloest", "command": " ".join(cmd)}

