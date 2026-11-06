# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.16.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Archivierung alter Logs auf externe Ziele (FTP/SFTP/SMB)
# ==============================================================================
"""
Schreibt Logs, die älter als N Tage sind, als gepackte NDJSON-Datei weg und
lädt sie auf ein externes Ziel: FTP(S), SFTP, SMB-Freigabe oder einen
eingebundenen Ordner. Auf Wunsch werden die weggeschriebenen Zeilen danach aus
der Datenbank gelöscht — aber wirklich nur dann, wenn die Datei nachweislich
angekommen ist.

Ablauf eines Laufs:
  1. Stichtag berechnen (jetzt - `age_days`).
  2. Logs älter als der Stichtag blockweise lesen und als `.ndjson.gz` schreiben
     (eine JSON-Zeile pro Eintrag, gut für Wiedereinspielung und grep).
  3. Datei auf das Ziel übertragen.
  4. Optional: genau diese Zeilen löschen (über die gemerkten IDs, nicht über
     den Zeitstempel — sonst könnten inzwischen eingetroffene Zeilen mit
     älterem Datum ungesichert verschwinden).

Die Zugangsdaten liegen in der `settings`-Tabelle; nach außen werden sie nie
zurückgegeben (siehe `public_config`).
"""

import asyncio
import gzip
import json
import logging
import os
import posixpath
import tempfile
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Log, Setting

logger = logging.getLogger("rimperio.archiving")

SETTING_KEY = "archiving"
HISTORY_KEY = "archiving_history"
HISTORY_LIMIT = 20

# Wie viele Zeilen auf einmal aus der Datenbank geholt werden.
BATCH_SIZE = 5000

DEFAULT_CONFIG = {
    "enabled": False,
    # ftp | ftps | sftp | smb | local
    "protocol": "sftp",
    "host": "",
    "port": 0,                 # 0 = Standardport des Protokolls
    "username": "",
    "password": "",
    "remote_path": "/rimperio",  # Zielordner (bei SMB: Pfad innerhalb der Freigabe)
    "share": "",               # nur SMB: Name der Freigabe
    "domain": "",              # nur SMB: Windows-Domäne (optional)
    # Logs, die älter sind als das, werden archiviert.
    "age_days": 90,
    # Nach erfolgreicher Übertragung aus der Datenbank löschen.
    "delete_after": False,
    # Zeitplan: täglich zu dieser Stunde (Serverzeit). -1 = nur von Hand.
    "schedule_hour": 3,
    "verify_cert": True,
}

SECRET_FIELDS = ("password",)

DEFAULT_PORTS = {"ftp": 21, "ftps": 21, "sftp": 22, "smb": 445}


# =============================================================================
# Konfiguration
# =============================================================================

async def load_config(db: AsyncSession) -> dict:
    result = await db.execute(select(Setting).where(Setting.key == SETTING_KEY))
    row = result.scalar_one_or_none()
    config = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        config.update(row.value)
    return config


async def save_config(db: AsyncSession, config: dict) -> None:
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = config
    else:
        db.add(Setting(key=SETTING_KEY, value=config, description="Archivierung alter Logs"))
    await db.commit()


def public_config(config: dict) -> dict:
    safe = {k: v for k, v in config.items() if k not in SECRET_FIELDS}
    safe["password_set"] = bool(config.get("password"))
    return safe


async def load_history(db: AsyncSession) -> list:
    row = (await db.execute(select(Setting).where(Setting.key == HISTORY_KEY))).scalar_one_or_none()
    if row and isinstance(row.value, list):
        return row.value
    return []


async def add_history(db: AsyncSession, entry: dict) -> None:
    """Schreibt einen Lauf in die Historie (neueste zuerst, begrenzt)."""
    # Kopie: load_history gibt die Liste der geladenen Zeile zurueck. An Ort und
    # Stelle geaendert, sieht SQLAlchemy beim Zuweisen keinen Unterschied und
    # schreibt nichts - die Historie blieb dann beim ersten Eintrag stehen.
    history = list(await load_history(db))
    history.insert(0, entry)
    history = history[:HISTORY_LIMIT]

    row = (await db.execute(select(Setting).where(Setting.key == HISTORY_KEY))).scalar_one_or_none()
    if row:
        row.value = history
    else:
        db.add(Setting(key=HISTORY_KEY, value=history, description="Letzte Archivierungsläufe"))
    await db.commit()


# =============================================================================
# Übertragung
# =============================================================================

def _port(config: dict) -> int:
    port = int(config.get("port") or 0)
    return port or DEFAULT_PORTS.get(config.get("protocol", ""), 0)


def _upload_ftp(config: dict, local_path: str, filename: str) -> str:
    """FTP bzw. FTPS. Bei "ftps" wird die Verbindung verschlüsselt."""
    import ftplib
    import ssl

    remote_dir = config.get("remote_path") or "/"
    secure = config.get("protocol") == "ftps"

    if secure:
        context = ssl.create_default_context()
        if not config.get("verify_cert", True):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        ftp = ftplib.FTP_TLS(context=context)
    else:
        ftp = ftplib.FTP()

    ftp.connect(config["host"], _port(config), timeout=30)
    ftp.login(config.get("username") or "anonymous", config.get("password") or "")
    if secure:
        ftp.prot_p()  # auch die Datenverbindung verschlüsseln, nicht nur den Login

    try:
        _ftp_makedirs(ftp, remote_dir)
        ftp.cwd(remote_dir)
        with open(local_path, "rb") as handle:
            ftp.storbinary(f"STOR {filename}", handle)
        return posixpath.join(remote_dir, filename)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def _ftp_makedirs(ftp, path: str) -> None:
    """Legt den Zielordner an, falls er fehlt (Ebene für Ebene)."""
    current = "/" if path.startswith("/") else ""
    for part in path.strip("/").split("/"):
        if not part:
            continue
        current = posixpath.join(current, part)
        try:
            ftp.mkd(current)
        except Exception:
            pass  # existiert bereits - der Wechsel unten deckt Fehler auf


def _upload_sftp(config: dict, local_path: str, filename: str) -> str:
    import paramiko

    remote_dir = config.get("remote_path") or "."
    transport = paramiko.Transport((config["host"], _port(config)))
    try:
        transport.connect(username=config.get("username"), password=config.get("password"))
        sftp = paramiko.SFTPClient.from_transport(transport)

        # Ordner Ebene für Ebene anlegen
        current = "/" if remote_dir.startswith("/") else ""
        for part in remote_dir.strip("/").split("/"):
            if not part:
                continue
            current = posixpath.join(current, part)
            try:
                sftp.stat(current)
            except IOError:
                sftp.mkdir(current)

        target = posixpath.join(remote_dir, filename)
        sftp.put(local_path, target)
        sftp.close()
        return target
    finally:
        transport.close()


def _upload_smb(config: dict, local_path: str, filename: str) -> str:
    """SMB-Freigabe (Windows-Server, NAS)."""
    import smbclient

    share = (config.get("share") or "").strip("/\\")
    if not share:
        raise ValueError("Für SMB muss eine Freigabe angegeben werden")

    smbclient.ClientConfig(
        username=config.get("username") or None,
        password=config.get("password") or None,
    )

    base = rf"\\{config['host']}\{share}"
    sub = (config.get("remote_path") or "").strip("/\\").replace("/", "\\")
    remote_dir = f"{base}\\{sub}" if sub else base

    # Ordner Ebene für Ebene anlegen
    current = base
    for part in sub.split("\\") if sub else []:
        if not part:
            continue
        current = f"{current}\\{part}"
        try:
            smbclient.stat(current)
        except Exception:
            smbclient.mkdir(current)

    target = f"{remote_dir}\\{filename}"
    with open(local_path, "rb") as source, smbclient.open_file(target, mode="wb") as dest:
        while chunk := source.read(1024 * 1024):
            dest.write(chunk)
    return target


def _upload_local(config: dict, local_path: str, filename: str) -> str:
    """Eingebundener Ordner (z.B. ein per Docker gemountetes Netzlaufwerk)."""
    import shutil

    remote_dir = config.get("remote_path") or "/archive"
    os.makedirs(remote_dir, exist_ok=True)
    target = os.path.join(remote_dir, filename)
    shutil.copyfile(local_path, target)
    return target


UPLOADERS = {
    "ftp": _upload_ftp,
    "ftps": _upload_ftp,
    "sftp": _upload_sftp,
    "smb": _upload_smb,
    "local": _upload_local,
}


def upload_sync(config: dict, local_path: str, filename: str) -> str:
    """Überträgt eine Datei; gibt den Zielpfad zurück. Blockierend."""
    protocol = config.get("protocol", "")
    uploader = UPLOADERS.get(protocol)
    if not uploader:
        raise ValueError(f"Unbekanntes Protokoll: {protocol}")
    if protocol != "local" and not config.get("host"):
        raise ValueError("Kein Zielserver angegeben")
    return uploader(config, local_path, filename)


async def test_target(config: dict) -> dict:
    """Legt eine kleine Testdatei am Ziel ab - beweist Erreichbarkeit und Schreibrecht."""
    filename = f"rimperio-test-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write("RIMPERIO Verbindungstest\n")
        temp_path = handle.name

    try:
        target = await asyncio.to_thread(upload_sync, config, temp_path, filename)
        return {"success": True, "message": f"Testdatei abgelegt: {target}"}
    except ImportError as exc:
        return {"success": False, "message": f"Benötigtes Paket fehlt im Backend: {exc}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
    finally:
        os.unlink(temp_path)


# =============================================================================
# Lauf
# =============================================================================

async def _write_archive(db: AsyncSession, cutoff: datetime, temp_path: str) -> tuple:
    """Schreibt alle Logs älter als `cutoff` nach `temp_path`. Gibt (Anzahl, IDs) zurück."""
    written = 0
    ids: list = []
    last_id = 0

    with gzip.open(temp_path, "wt", encoding="utf-8") as out:
        while True:
            rows = (await db.execute(
                select(Log)
                .where(Log.timestamp < cutoff, Log.id > last_id)
                .order_by(Log.id)
                .limit(BATCH_SIZE)
            )).scalars().all()

            if not rows:
                break

            for row in rows:
                out.write(json.dumps({
                    "id": row.id,
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                    "hostname": row.hostname,
                    "ip_address": row.ip_address,
                    "facility": row.facility,
                    "level": row.level,
                    "source": row.source,
                    "message": row.message,
                    "raw_message": row.raw_message,
                    "extra_data": row.extra_data,
                }, ensure_ascii=False) + "\n")
                ids.append(row.id)

            written += len(rows)
            last_id = rows[-1].id

    return written, ids


async def run_archiving(db: AsyncSession, config: dict, triggered_by: str = "manuell") -> dict:
    """Führt einen Archivierungslauf aus und schreibt das Ergebnis in die Historie."""
    started = datetime.utcnow()
    age_days = int(config.get("age_days") or 0)
    if age_days <= 0:
        result = {"success": False, "message": "Ungültiges Alter (age_days muss > 0 sein)"}
        await add_history(db, {**result, "at": started.isoformat(), "triggered_by": triggered_by})
        return result

    cutoff = started - timedelta(days=age_days)
    filename = (
        f"rimperio-logs-bis-{cutoff.strftime('%Y%m%d')}"
        f"-{started.strftime('%Y%m%d%H%M%S')}.ndjson.gz"
    )

    handle = tempfile.NamedTemporaryFile(suffix=".ndjson.gz", delete=False)
    temp_path = handle.name
    handle.close()

    try:
        written, ids = await _write_archive(db, cutoff, temp_path)

        if not written:
            result = {
                "success": True,
                "message": f"Nichts zu archivieren (keine Logs älter als {age_days} Tage)",
                "archived": 0,
                "deleted": 0,
            }
            await add_history(db, {**result, "at": started.isoformat(), "triggered_by": triggered_by})
            return result

        size = os.path.getsize(temp_path)
        target = await asyncio.to_thread(upload_sync, config, temp_path, filename)

        deleted = 0
        if config.get("delete_after"):
            # Bewusst über die gemerkten IDs: zwischenzeitlich eingetroffene
            # Zeilen mit altem Zeitstempel wären sonst nicht in der Datei,
            # würden aber gelöscht.
            for start in range(0, len(ids), BATCH_SIZE):
                chunk = ids[start:start + BATCH_SIZE]
                await db.execute(delete(Log).where(Log.id.in_(chunk)))
                deleted += len(chunk)
            await db.commit()

        result = {
            "success": True,
            "message": f"{written:,} Logs archiviert nach {target}".replace(",", "."),
            "archived": written,
            "deleted": deleted,
            "file": filename,
            "target": target,
            "bytes": size,
        }
    except ImportError as exc:
        result = {"success": False, "message": f"Benötigtes Paket fehlt im Backend: {exc}"}
    except Exception as exc:
        logger.error("Archivierung fehlgeschlagen: %s", exc)
        result = {"success": False, "message": str(exc)}
        # Nach einem Datenbankfehler ist die Transaktion verdorben. Ohne
        # Rollback scheitert gleich darauf auch add_history - der
        # Fehlschlag stuende dann nirgends in der Historie.
        try:
            await db.rollback()
        except Exception as rollback_exc:                           # defensiv
            logger.warning("Rollback nach Archivierungsfehler: %s", rollback_exc)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    result["duration_seconds"] = round((datetime.utcnow() - started).total_seconds(), 1)
    await add_history(db, {**result, "at": started.isoformat(), "triggered_by": triggered_by})
    return result
