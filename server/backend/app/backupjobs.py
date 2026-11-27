# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Backup-Bereich: Orte, Auftraege, Laeufe; verschluesselt
#               (AES-256-GCM, Strom-Format RIMPBAK1), Zeitplan, Aufbewahrung
# ==============================================================================
"""
Das Backup-System von RIMPERIO.

Drei Begriffe:

* **Ort** (``BackupTarget``) - WOHIN gesichert wird: ein Ordner am
  RIMPERIO-Server, eine SMB-Freigabe (typisch: das Synology-NAS), SFTP, FTP(S).
  Zugangsdaten liegen in Vaultwarden bzw. lokal verschluesselt (app/vault.py).
* **Auftrag** (``BackupJob``) - WAS gesichert wird, WANN und WOHIN:

  ==========  ===============================================================
  rimperio    RIMPERIO selbst (Datenbank-Bereiche wie unter *Sicherung*)
  files       Ordner auf einem Rechner mit Agent (Linux, Windows, Raspberry Pi)
  nas_config  Konfiguration eines Synology-NAS (.dss)
  nas_task    Hyper-Backup-Aufgabe auf dem NAS anstossen und ueberwachen
  pve         Proxmox-VM/-Container per vzdump auf einen Proxmox-Speicher
  ==========  ===============================================================

* **Lauf** (``BackupRun``) - ein Ergebnis mit Ort, Groesse, Pruefsumme, Protokoll.

Verschluesselung
----------------
Alles, was RIMPERIO selbst ablegt, ist verschluesselt (abschaltbar je
Auftrag). Format ``RIMPBAK1`` - ein Strom, damit auch grosse Rechner-
Sicherungen nicht in den Speicher muessen::

    "RIMPBAK1" | u32 Laenge | Kopf (JSON: kdf, salt, Iterationen, Inhalt …)
    je Block:  u32 Laenge | AES-256-GCM(Block)    (Nonce = Basis | Blocknummer,
                                                  AAD = Kopf-Hash | Nr | letzter?)

Der Schluessel kommt aus einem Passwort (PBKDF2-SHA256, 310 000 Runden), das in
Vaultwarden liegt ("RIMPERIO · Backup-Schlüssel"). Wer RIMPERIO verliert, kann mit
diesem Passwort und ``docs/backup/rimperio-decrypt.py`` jede Datei selbst
entschluesseln. Abschneiden oder Vertauschen von Bloecken faellt beim
Entschluesseln auf.

Rechner-Sicherungen laufen so: RIMPERIO legt einen Lauf mit Einmal-Schluessel
an und schickt dem Agent ein kurzes Skript. Das packt die Ordner (Linux: ueber
das Hilfsskript mit root-Rechten, Windows: tar.exe) und schickt den Strom per
HTTPS an ``PUT /api/backup-upload/{lauf}``. Der Server verschluesselt beim
Empfang und legt die Datei am Ort ab. Der Rechner bekommt nie Zugang zum Ort.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import posixpath
import secrets
import shutil
import struct
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import journal, vault
from .models import (Agent, BackupJob, BackupRun, BackupTarget, Command, NasDevice, PveHost, PveLink,
                     Setting, StoredSecret)

logger = logging.getLogger("rimperio.backupjobs")

MAGIC = b"RIMPBAK1"
CHUNK = 1024 * 1024
KDF_ITER = 310_000
LOCAL_ROOT = Path(os.getenv("RIMPERIO_BACKUP_DIR", "/backups")) / "jobs"
WORK_DIR = Path(os.getenv("RIMPERIO_BACKUP_WORK", tempfile.gettempdir())) / "rimperio-backup"
DEFAULT_KEY_SETTING = "backup_default_key"
UPLOAD_WAIT = timedelta(hours=12)

KINDS = {
    "rimperio": "RIMPERIO-Server",
    "files": "Ordner auf einem Rechner",
    "nas_config": "NAS-Konfiguration",
    "nas_task": "Hyper Backup auf dem NAS",
    "pve": "Proxmox-VM/-Container (vzdump)",
}
TARGET_KINDS = {"local": "Ordner am RIMPERIO-Server", "smb": "SMB-Freigabe (NAS, Windows)",
                "sftp": "SFTP", "ftp": "FTP", "ftps": "FTPS"}
DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21, "smb": 445}


class BackupError(Exception):
    pass


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# =============================================================================
# Verschluesselung (Strom)
# =============================================================================
def _derive(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


class Encryptor:
    """Schreibt das RIMPBAK1-Format in eine Datei, Block fuer Block."""

    def __init__(self, out: BinaryIO, passphrase: str, meta: dict) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        salt = secrets.token_bytes(16)
        self.base_nonce = secrets.token_bytes(8)
        header = {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-HMAC-SHA256", "iter": KDF_ITER,
                  "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(self.base_nonce).decode(),
                  "chunk": CHUNK, **meta}
        self.header = json.dumps(header, sort_keys=True).encode("utf-8")
        self.hh = hashlib.sha256(self.header).digest()[:16]
        self.aes = AESGCM(_derive(passphrase, salt, KDF_ITER))
        self.out = out
        self.buf = b""
        self.n = 0
        self.plain = 0
        out.write(MAGIC + struct.pack(">I", len(self.header)) + self.header)

    def _emit(self, data: bytes, final: bool) -> None:
        nonce = self.base_nonce + struct.pack(">I", self.n)
        aad = self.hh + struct.pack(">I", self.n) + (b"\x01" if final else b"\x00")
        ct = self.aes.encrypt(nonce, data, aad)
        self.out.write(struct.pack(">I", len(ct)) + ct)
        self.n += 1

    def write(self, data: bytes) -> None:
        self.plain += len(data)
        self.buf += data
        while len(self.buf) > CHUNK:
            block, self.buf = self.buf[:CHUNK], self.buf[CHUNK:]
            self._emit(block, False)

    def close(self) -> None:
        self._emit(self.buf, True)
        self.buf = b""


def decrypt_stream(src: BinaryIO, dst: BinaryIO, passphrase: str) -> dict:
    """Entschluesselt RIMPBAK1. Wirft BackupError bei falschem Passwort/Beschaedigung."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if src.read(8) != MAGIC:
        raise BackupError("Keine RIMPERIO-Sicherungsdatei (RIMPBAK1).")
    (hlen,) = struct.unpack(">I", src.read(4))
    raw = src.read(hlen)
    header = json.loads(raw)
    aes = AESGCM(_derive(passphrase, base64.b64decode(header["salt"]), int(header["iter"])))
    base_nonce = base64.b64decode(header["nonce"])
    hh = hashlib.sha256(raw).digest()[:16]
    n = 0
    while True:
        lb = src.read(4)
        if len(lb) < 4:
            raise BackupError("Datei abgeschnitten – letzter Block fehlt.")
        (clen,) = struct.unpack(">I", lb)
        ct = src.read(clen)
        nonce = base_nonce + struct.pack(">I", n)
        for final in (b"\x00", b"\x01"):
            try:
                plain = aes.decrypt(nonce, ct, hh + struct.pack(">I", n) + final)
                break
            except InvalidTag:
                plain = None
        if plain is None:
            raise BackupError("Falsches Passwort oder beschädigte Datei." if n == 0 else
                              f"Block {n} beschädigt.")
        dst.write(plain)
        n += 1
        if final == b"\x01":
            if src.read(1):
                raise BackupError("Daten hinter dem letzten Block – Datei manipuliert?")
            return header


def read_header(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != MAGIC:
                return None
            (hlen,) = struct.unpack(">I", fh.read(4))
            return json.loads(fh.read(hlen))
    except (OSError, ValueError):
        return None


# =============================================================================
# Orte
# =============================================================================
def _safe_part(text: str) -> str:
    out = "".join(c if (c.isascii() and c.isalnum()) or c in "-_." else "-" for c in (text or ""))
    return out.strip("-.")[:80] or "auftrag"


class Store:
    """Ablage an einem Ort. Blockierend - mit asyncio.to_thread benutzen."""

    def __init__(self, target: BackupTarget, cred: Optional[dict]) -> None:
        self.t = target
        self.user = (cred or {}).get("username") or ""
        self.password = (cred or {}).get("password") or ""
        self.port = target.port or DEFAULT_PORTS.get(target.kind)

    # --- Pfade -----------------------------------------------------------
    def _local_dir(self, sub: str) -> Path:
        d = self._local_base() / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _remote(self, sub: str) -> str:
        return posixpath.join("/" + (self.t.path or "").strip("/"), sub) if (self.t.path or "").strip("/") else sub

    def _local_base(self) -> Path:
        if self.t.path and self.t.path.startswith("/"):
            return Path(self.t.path)
        return LOCAL_ROOT / _safe_part(self.t.name)

    def describe(self, sub: str, name: str) -> str:
        k = self.t.kind
        if k == "local":
            return str(self._local_base() / sub / name).rstrip("/")
        if k == "smb":
            bs = "\\"
            share = (self.t.share or "").strip("/" + bs)
            rest = bs.join(p for p in ((self.t.path or "").strip("/" + bs).replace("/", bs), sub, name) if p)
            return bs * 2 + self.t.host + bs + share + (bs + rest if rest else "")
        return f"{k}://{self.t.host}{':' + str(self.port) if self.port else ''}/{self._remote(sub).lstrip('/')}/{name}"

    # --- SMB -------------------------------------------------------------
    def _smb_kw(self) -> dict:
        return {"username": self.user or None, "password": self.password or None, "port": int(self.port or 445)}

    def _smb_dir(self, sub: str) -> str:
        import smbclient
        kw = self._smb_kw()
        share = (self.t.share or "").strip("/\\")
        if not share:
            raise BackupError("Für SMB fehlt die Freigabe.")
        cur = rf"\\{self.t.host}\{share}"
        parts = [p for p in ((self.t.path or "").replace("/", "\\").split("\\") + sub.split("/")) if p]
        for p in parts:
            cur = f"{cur}\\{p}"
            try:
                smbclient.stat(cur, **kw)
            except Exception:
                smbclient.mkdir(cur, **kw)
        return cur

    # --- SFTP / FTP ------------------------------------------------------
    def _sftp(self):
        import paramiko
        tr = paramiko.Transport((self.t.host, int(self.port or 22)))
        tr.connect(username=self.user, password=self.password)
        return tr, paramiko.SFTPClient.from_transport(tr)

    def _ftp(self):
        import ftplib
        import ssl
        if self.t.kind == "ftps":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ftp = ftplib.FTP_TLS(context=ctx)
        else:
            ftp = ftplib.FTP()
        ftp.connect(self.t.host, int(self.port or 21), timeout=30)
        ftp.login(self.user or "anonymous", self.password)
        if self.t.kind == "ftps":
            ftp.prot_p()
        return ftp

    def _mkdirs(self, mk, stat, path: str) -> None:
        cur = "/" if path.startswith("/") else ""
        for part in path.strip("/").split("/"):
            if not part:
                continue
            cur = posixpath.join(cur, part)
            try:
                stat(cur)
            except Exception:
                try:
                    mk(cur)
                except Exception:
                    pass

    # --- Schnittstelle ---------------------------------------------------
    def put(self, local: Path, sub: str, name: str) -> str:
        k = self.t.kind
        if k == "local":
            dest = self._local_dir(sub) / name
            shutil.copyfile(local, dest)
            return str(dest)
        if k == "smb":
            import smbclient
            d = self._smb_dir(sub)
            with open(local, "rb") as src, smbclient.open_file(f"{d}\\{name}", mode="wb", **self._smb_kw()) as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            return f"{d}\\{name}"
        if k == "sftp":
            tr, s = self._sftp()
            try:
                d = self._remote(sub)
                self._mkdirs(s.mkdir, s.stat, d)
                s.put(str(local), posixpath.join(d, name))
                return self.describe(sub, name)
            finally:
                tr.close()
        if k in ("ftp", "ftps"):
            ftp = self._ftp()
            try:
                d = self._remote(sub)
                self._mkdirs(ftp.mkd, ftp.cwd, d)
                ftp.cwd(d or "/")
                with open(local, "rb") as fh:
                    ftp.storbinary(f"STOR {name}", fh)
                return self.describe(sub, name)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
        raise BackupError(f"Unbekannte Art von Ort: {k}")

    def get(self, sub: str, name: str, local: Path) -> None:
        k = self.t.kind
        if k == "local":
            shutil.copyfile(self._local_dir(sub) / name, local)
        elif k == "smb":
            import smbclient
            d = self._smb_dir(sub)
            with smbclient.open_file(f"{d}\\{name}", mode="rb", **self._smb_kw()) as src, open(local, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
        elif k == "sftp":
            tr, s = self._sftp()
            try:
                s.get(posixpath.join(self._remote(sub), name), str(local))
            finally:
                tr.close()
        elif k in ("ftp", "ftps"):
            ftp = self._ftp()
            try:
                ftp.cwd(self._remote(sub) or "/")
                with open(local, "wb") as fh:
                    ftp.retrbinary(f"RETR {name}", fh.write)
            finally:
                ftp.close()

    def delete(self, sub: str, name: str) -> None:
        k = self.t.kind
        if k == "local":
            (self._local_dir(sub) / name).unlink(missing_ok=True)
        elif k == "smb":
            import smbclient
            smbclient.remove(f"{self._smb_dir(sub)}\\{name}", **self._smb_kw())
        elif k == "sftp":
            tr, s = self._sftp()
            try:
                s.remove(posixpath.join(self._remote(sub), name))
            finally:
                tr.close()
        elif k in ("ftp", "ftps"):
            ftp = self._ftp()
            try:
                ftp.cwd(self._remote(sub) or "/")
                ftp.delete(name)
            finally:
                ftp.close()

    def test(self) -> dict:
        """Testdatei schreiben, lesen, loeschen; freien Platz ermitteln (wo moeglich)."""
        name = f".rimperio-test-{secrets.token_hex(4)}"
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = WORK_DIR / name
        tmp.write_bytes(b"RIMPERIO Verbindungstest\n")
        back = WORK_DIR / (name + ".back")
        try:
            where = self.put(tmp, "", name)
            self.get("", name, back)
            ok = back.read_bytes() == tmp.read_bytes()
            self.delete("", name)
        finally:
            tmp.unlink(missing_ok=True)
            back.unlink(missing_ok=True)
        free = None
        if self.t.kind == "local":
            try:
                free = shutil.disk_usage(self._local_dir("")).free
            except OSError:
                pass
        return {"ok": ok, "where": where.rsplit(name, 1)[0], "free_bytes": free}


async def store_for(db: AsyncSession, target: BackupTarget) -> Store:
    cred = None
    if target.secret_id:
        cred = await vault.get(db, target.secret_id)
    return Store(target, cred)


def public_target(t: BackupTarget, secret: Optional[StoredSecret] = None) -> dict:
    return {"id": t.id, "name": t.name, "kind": t.kind, "kind_label": TARGET_KINDS.get(t.kind, t.kind),
            "host": t.host, "port": t.port, "share": t.share, "path": t.path, "nas_id": t.nas_id,
            "secret_id": t.secret_id, "username": secret.username if secret else "",
            "secret_backend": secret.backend if secret else None, "enabled": t.enabled,
            "last_ok_at": _iso(t.last_ok_at), "last_error": t.last_error, "free_bytes": t.free_bytes,
            "where": Store(t, None).describe("", "").rstrip("/\\") if t.kind != "smb" or t.share else ""}


# =============================================================================
# Schluessel
# =============================================================================
async def default_key(db: AsyncSession, create: bool = True) -> Optional[int]:
    row = (await db.execute(select(Setting).where(Setting.key == DEFAULT_KEY_SETTING))).scalar_one_or_none()
    sid = (row.value or {}).get("secret_id") if row and isinstance(row.value, dict) else None
    if sid and await db.get(StoredSecret, sid):
        return sid
    if not create:
        return None
    pw = secrets.token_urlsafe(32)
    secret = await vault.put(db, secret_id=None, kind="backup_key", name="Backup-Schlüssel", username="rimperio-backup",
                             password=pw, notes="Passwort für alle verschlüsselten RIMPERIO-Sicherungen (Format RIMPBAK1). "
                                                "Ohne dieses Passwort lassen sich die Sicherungen nicht zurückholen – "
                                                "nicht löschen! Entschlüsseln ohne RIMPERIO: docs/backup/rimperio-decrypt.py")
    if row:
        row.value = {"secret_id": secret.id}
    else:
        db.add(Setting(key=DEFAULT_KEY_SETTING, value={"secret_id": secret.id}, description="Backup-Schlüssel"))
    await db.commit()
    return secret.id


async def passphrase_for(db: AsyncSession, job: BackupJob) -> str:
    sid = job.key_secret_id or await default_key(db)
    data = await vault.get(db, sid)
    if not data.get("password"):
        raise BackupError("Der Backup-Schlüssel ist leer.")
    return data["password"]


# =============================================================================
# Zeitplan
# =============================================================================
def next_run(schedule: dict, after: datetime) -> Optional[datetime]:
    """Naechster Termin in UTC. None = nur von Hand."""
    from zoneinfo import ZoneInfo
    typ = (schedule or {}).get("type") or "daily"
    if typ == "manual":
        return None
    if typ == "hourly":
        hours = max(1, int(schedule.get("every_hours") or 6))
        return after + timedelta(hours=hours)
    try:
        tz = ZoneInfo(schedule.get("tz") or "Europe/Berlin")
    except Exception:                                               # defensiv
        tz = ZoneInfo("UTC")
    try:
        hh, mm = (int(x) for x in str(schedule.get("time") or "02:30").split(":")[:2])
    except ValueError:
        hh, mm = 2, 30
    days = schedule.get("days") or ([0, 1, 2, 3, 4, 5, 6] if typ == "daily" else [6])
    local = after.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    for add in range(0, 8):
        cand = (local + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand > local and cand.weekday() in days:
            return cand.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return None


def expected_hours(job: BackupJob) -> Optional[float]:
    if job.max_age_hours:
        return float(job.max_age_hours)
    typ = (job.schedule or {}).get("type") or "daily"
    if typ == "manual":
        return None
    if typ == "hourly":
        return max(1, int(job.schedule.get("every_hours") or 6)) * 2.5
    days = (job.schedule or {}).get("days") or []
    if typ == "weekly" or (days and len(days) < 7):
        return 8 * 24
    return 30.0


def public_job(job: BackupJob, target: Optional[BackupTarget] = None, last: Optional[BackupRun] = None,
               source_label: str = "") -> dict:
    now = datetime.utcnow()
    exp = expected_hours(job)
    overdue = bool(job.enabled and exp and (not job.last_ok_at or (now - job.last_ok_at).total_seconds() > exp * 3600)
                   and job.created_at and (now - job.created_at).total_seconds() > exp * 3600)
    return {"id": job.id, "name": job.name, "kind": job.kind, "kind_label": KINDS.get(job.kind, job.kind),
            "source": job.source or {}, "source_label": source_label, "target_id": job.target_id,
            "target": {"id": target.id, "name": target.name, "kind": target.kind} if target else None,
            "encrypt": job.encrypt, "key_secret_id": job.key_secret_id, "schedule": job.schedule or {},
            "keep_last": job.keep_last, "keep_days": job.keep_days, "max_age_hours": job.max_age_hours,
            "enabled": job.enabled, "note": job.note, "last_run_at": _iso(job.last_run_at),
            "last_ok_at": _iso(job.last_ok_at), "last_status": job.last_status, "next_run_at": _iso(job.next_run_at),
            "overdue": overdue, "last_run": public_run(last) if last else None,
            "created_by": job.created_by, "created_at": _iso(job.created_at)}


def public_run(r: BackupRun) -> dict:
    return {"id": r.id, "job_id": r.job_id, "status": r.status, "trigger": r.trigger,
            "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at), "file_name": r.file_name,
            "location": r.location, "size_bytes": r.size_bytes, "plain_bytes": r.plain_bytes, "sha256": r.sha256,
            "encrypted": r.encrypted, "detail": r.detail or {}, "log": r.log or [], "error": r.error,
            "created_by": r.created_by,
            "duration_s": round((r.finished_at - r.started_at).total_seconds()) if r.finished_at and r.started_at else None}


async def describe_source(db: AsyncSession, job: BackupJob) -> str:
    src = job.source or {}
    if job.kind == "rimperio":
        return "Bereiche: " + (", ".join(src.get("scopes") or []) or "Standard")
    if job.kind == "files":
        a = (await db.execute(select(Agent).where(Agent.agent_uid == src.get("agent_uid")))).scalars().first()
        return f"{a.hostname if a else src.get('agent_uid')}: " + ", ".join(src.get("paths") or [])
    if job.kind in ("nas_config", "nas_task"):
        n = await db.get(NasDevice, src.get("nas_id")) if src.get("nas_id") else None
        name = n.name if n else f"NAS {src.get('nas_id')}"
        return name + (f" · Aufgabe {src.get('task_name') or src.get('task_id')}" if job.kind == "nas_task" else "")
    if job.kind == "pve":
        h = await db.get(PveHost, src.get("host_id")) if src.get("host_id") else None
        return f"{h.name if h else 'Proxmox'} · {src.get('guest_type', 'qemu')} {src.get('vmid')} ({src.get('node')})" + \
            (f" → {src.get('storage')}" if src.get("storage") else "")
    return ""


# =============================================================================
# Laeufe
# =============================================================================
_active: Dict[int, asyncio.Task] = {}


def running(job_id: int) -> bool:
    t = _active.get(job_id)
    return bool(t and not t.done())


def _log(run: BackupRun, text: str) -> None:
    entries = list(run.log or [])
    entries.append({"at": _iso(datetime.utcnow()), "text": str(text)[:2000]})
    run.log = entries[-200:]


def _file_name(job: BackupJob, ext: str, encrypted: bool) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"{_safe_part(job.name)}-{stamp}.{ext}" + (".rimpbak" if encrypted else "")


def _sub(job: BackupJob) -> str:
    return f"{job.id:04d}-{_safe_part(job.name)}"


async def start(job_id: int, actor: str, trigger: str = "manual") -> int:
    """Startet einen Lauf im Hintergrund. Gibt die Lauf-ID zurueck."""
    from .database import async_session
    if running(job_id):
        raise BackupError("Dieser Auftrag läuft gerade.")
    async with async_session() as db:
        job = await db.get(BackupJob, job_id)
        if job is None:
            raise BackupError("Unbekannter Auftrag.")
        waiting = (await db.execute(select(BackupRun).where(BackupRun.job_id == job_id,
                                                            BackupRun.status == "waiting"))).scalars().first()
        if waiting:
            raise BackupError("Ein Lauf wartet noch auf die Daten vom Rechner.")
        run = BackupRun(job_id=job.id, status="running", trigger=trigger, created_by=actor, log=[], detail={})
        db.add(run)
        job.last_run_at = datetime.utcnow()
        job.last_status = "running"
        job.next_run_at = next_run(job.schedule or {}, datetime.utcnow())
        await db.commit()
        await db.refresh(run)
        run_id = run.id
    task = asyncio.create_task(_execute(job_id, run_id))
    _active[job_id] = task
    return run_id


async def _execute(job_id: int, run_id: int) -> None:
    from .database import async_session
    async with async_session() as db:
        job = await db.get(BackupJob, job_id)
        run = await db.get(BackupRun, run_id)
        try:
            handler = {"rimperio": _run_rimperio, "files": _run_files, "nas_config": _run_nas_config,
                       "nas_task": _run_nas_task, "pve": _run_pve}.get(job.kind)
            if handler is None:
                raise BackupError(f"Unbekannte Art: {job.kind}")
            await handler(db, job, run)
            if run.status == "running":
                run.status = "done"
        except (BackupError, vault.VaultError, OSError, ValueError) as exc:
            run.status, run.error = "error", str(exc)
            _log(run, f"Fehler: {exc}")
        except Exception as exc:                                    # defensiv
            logger.exception("Backup-Lauf %s fehlgeschlagen", run_id)
            run.status, run.error = "error", str(exc) or exc.__class__.__name__
            _log(run, f"Fehler: {run.error}")
        if run.status != "waiting":
            await _finish(db, job, run)
        else:
            await db.commit()


async def _finish(db: AsyncSession, job: BackupJob, run: BackupRun) -> None:
    from . import alerts
    run.finished_at = run.finished_at or datetime.utcnow()
    job.last_status = run.status
    if run.status == "done":
        job.last_ok_at = run.finished_at
    await db.commit()
    if run.status == "done":
        try:
            removed = await prune(db, job)
            if removed:
                _log(run, f"Aufbewahrung: {removed} alte Sicherung(en) entfernt")
                await db.commit()
        except Exception as exc:                                    # defensiv
            _log(run, f"Aufbewahrung: {exc}")
            await db.commit()
        alerts.forget(f"backup-over:{job.id}")
        await alerts.dispatch("backup_done", f"RIMPERIO: Sicherung „{job.name}“ erledigt",
                              f"{run.location or ''} · {_fmt_size(run.size_bytes)}", level="info",
                              key=f"backup-run:{run.id}", detail={"link": "/backups/runs", "job_id": job.id})
    else:
        await alerts.dispatch("backup_failed", f"RIMPERIO: Sicherung „{job.name}“ fehlgeschlagen",
                              run.error or "", level="warning", key=f"backup-fail:{job.id}",
                              detail={"link": "/backups/runs", "job_id": job.id})
    journal.fire(category="backup", event=f"backupjob.{run.status}", actor=run.created_by or "system",
                 level="info" if run.status == "done" else "warning", ok=run.status == "done",
                 message=f"Sicherung „{job.name}“: {'erledigt' if run.status == 'done' else run.error} "
                         f"{run.location or ''}".strip(), detail={"job_id": job.id, "run_id": run.id})


def _fmt_size(n: Optional[float]) -> str:
    if not n:
        return "–"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


async def _target(db: AsyncSession, job: BackupJob) -> BackupTarget:
    t = await db.get(BackupTarget, job.target_id) if job.target_id else None
    if t is None:
        raise BackupError("Für diesen Auftrag ist kein Ort gewählt.")
    if not t.enabled:
        raise BackupError(f"Der Ort „{t.name}“ ist abgeschaltet.")
    return t


async def _store_bytes_or_file(db: AsyncSession, job: BackupJob, run: BackupRun, *, src: Optional[Path] = None,
                               data: Optional[bytes] = None, ext: str, content: str) -> None:
    """Verschluesseln (falls gewuenscht), am Ort ablegen, Lauf ausfuellen."""
    target = await _target(db, job)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    name = _file_name(job, ext, job.encrypt)
    tmp = WORK_DIR / f"{run.id}-{name}"
    try:
        if job.encrypt:
            pw = await passphrase_for(db, job)

            def enc() -> int:
                with open(tmp, "wb") as out:
                    e = Encryptor(out, pw, {"content": content, "name": name, "job": job.name,
                                            "created": _iso(datetime.utcnow()), "ext": ext})
                    if data is not None:
                        e.write(data)
                    else:
                        with open(src, "rb") as fh:
                            while block := fh.read(CHUNK):
                                e.write(block)
                    e.close()
                    return e.plain
            plain = await asyncio.to_thread(enc)
        else:
            if data is not None:
                tmp.write_bytes(data)
            else:
                shutil.copyfile(src, tmp)
            plain = tmp.stat().st_size
        digest = await asyncio.to_thread(_sha256, tmp)
        store = await store_for(db, target)
        _log(run, f"Übertrage nach „{target.name}“ …")
        where = await asyncio.to_thread(store.put, tmp, _sub(job), name)
        run.file_name, run.location = name, where
        run.size_bytes, run.plain_bytes = float(tmp.stat().st_size), float(plain)
        run.sha256, run.encrypted = digest, bool(job.encrypt)
        run.detail = {**(run.detail or {}), "target_id": target.id, "sub": _sub(job), "content": content}
        target.last_ok_at, target.last_error = datetime.utcnow(), None
        _log(run, f"Abgelegt: {where} ({_fmt_size(run.size_bytes)}{', verschlüsselt' if job.encrypt else ''})")
    except Exception as exc:
        target.last_error = str(exc)[:500]
        raise
    finally:
        tmp.unlink(missing_ok=True)
        await db.commit()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(CHUNK):
            h.update(block)
    return h.hexdigest()


# --- RIMPERIO ---------------------------------------------------------------
async def _run_rimperio(db: AsyncSession, job: BackupJob, run: BackupRun) -> None:
    from . import backup as backup_module
    scopes = (job.source or {}).get("scopes") or None
    _log(run, "Sicherung von RIMPERIO wird erstellt …")
    manifest = await backup_module.create_backup(db, scopes=scopes, note=f"Auftrag {job.name}",
                                                 trigger="backupjob", created_by=run.created_by or "Zeitplan")
    path = backup_module.backup_path(manifest["name"])
    try:
        run.detail = {"scopes": manifest.get("scopes"), "counts": manifest.get("counts"),
                      "server_version": manifest.get("server_version")}
        await _store_bytes_or_file(db, job, run, src=path, ext="zip", content="rimperio-backup")
    finally:
        # Die lokale Kopie unter /backups bleibt nur, wenn der Ort selbst lokal ist.
        try:
            path.unlink()
        except OSError:
            pass


# --- NAS-Konfiguration ------------------------------------------------------
async def _run_nas_config(db: AsyncSession, job: BackupJob, run: BackupRun) -> None:
    from . import nas as nas_module
    nas = await db.get(NasDevice, (job.source or {}).get("nas_id"))
    if nas is None:
        raise BackupError("Das NAS gibt es nicht mehr.")
    _log(run, f"Konfiguration von „{nas.name}“ wird geholt …")
    try:
        data = await nas_module.config_backup(db, nas)
    except nas_module.NasError as exc:
        raise BackupError(str(exc)) from exc
    run.detail = {"nas_id": nas.id, "firmware": nas.firmware, "model": nas.model}
    await _store_bytes_or_file(db, job, run, data=data, ext="dss", content="synology-dss")


async def run_nas_config_before_update(db: AsyncSession, nas: NasDevice) -> Optional[str]:
    """Vor einem NAS-Update: vorhandenen Konfigurations-Auftrag laufen lassen (und abwarten)."""
    jobs = (await db.execute(select(BackupJob).where(BackupJob.kind == "nas_config",
                                                     BackupJob.enabled.is_(True)))).scalars().all()
    job = next((j for j in jobs if (j.source or {}).get("nas_id") == nas.id and j.target_id), None)
    if job is None:
        return None
    run_id = await start(job.id, "vor NAS-Update", trigger="schedule")
    task = _active.get(job.id)
    if task:
        await asyncio.wait_for(task, timeout=600)
    run = await db.get(BackupRun, run_id)
    await db.refresh(run)
    if run.status != "done":
        raise BackupError(run.error or "Konfigurationssicherung fehlgeschlagen")
    return run.location


# --- Hyper Backup -----------------------------------------------------------
async def _run_nas_task(db: AsyncSession, job: BackupJob, run: BackupRun) -> None:
    from . import nas as nas_module
    src = job.source or {}
    nas = await db.get(NasDevice, src.get("nas_id"))
    if nas is None:
        raise BackupError("Das NAS gibt es nicht mehr.")
    try:
        before = await nas_module.hyperbackup_status(db, nas, src.get("task_id"))
        _log(run, f"Hyper Backup „{before.get('name') or src.get('task_name')}“ wird gestartet "
                  f"(letzter Lauf: {before.get('last_time') or '–'}, {before.get('last_result') or '–'})")
        await nas_module.hyperbackup_run(db, nas, src.get("task_id"))
        deadline = datetime.utcnow() + timedelta(hours=int(src.get("timeout_hours") or 12))
        await asyncio.sleep(30)
        while datetime.utcnow() < deadline:
            st = await nas_module.hyperbackup_status(db, nas, src.get("task_id"))
            state = str(st.get("raw_state") or st.get("state") or "").lower()
            if state not in ("backingup", "preparing", "waiting", "running", "resuming", "detecting") and \
                    st.get("last_time") != before.get("last_time"):
                res = str(st.get("last_result") or "").lower()
                run.location = f"Hyper Backup auf {nas.name}: {st.get('target') or st.get('name')}"
                run.detail = {"nas_id": nas.id, "task": st}
                _log(run, f"Fertig: {st.get('last_result')} ({st.get('last_time')})")
                if res not in ("done", "success", "partial", "partially_done"):
                    raise BackupError(f"Hyper Backup meldet: {st.get('last_result')}")
                return
            await asyncio.sleep(60)
        raise BackupError("Hyper Backup ist nach der Wartezeit nicht fertig.")
    except nas_module.NasError as exc:
        raise BackupError(str(exc)) from exc


# --- Proxmox ----------------------------------------------------------------
async def _run_pve(db: AsyncSession, job: BackupJob, run: BackupRun) -> None:
    from . import pve
    src = job.source or {}
    host = await db.get(PveHost, src.get("host_id"))
    if host is None:
        raise BackupError("Der Proxmox-Host gibt es nicht mehr.")
    link = PveLink(node=src.get("node"), vmid=int(src.get("vmid")), guest_type=src.get("guest_type") or "qemu",
                   host_id=host.id, agent_id=0)
    storage = src.get("storage") or host.default_storage or ""
    started = datetime.utcnow()
    try:
        upid = await pve.vzdump(host, link, storage, f"RIMPERIO {job.name}")
        _log(run, f"vzdump gestartet ({upid})")
        node = pve.node_of_upid(upid, link.node)
        for _ in range(int(src.get("timeout_hours") or 6) * 120):
            st = await pve.task_status(host, node, upid)
            if st["done"]:
                if not st["ok"]:
                    raise BackupError(f"vzdump: {st['exitstatus']}")
                break
            await asyncio.sleep(30)
        else:
            raise BackupError("vzdump ist nach der Wartezeit nicht fertig.")
        volid = await pve.find_backup(host, {"node": link.node, "vmid": link.vmid}, storage, started) if storage else None
        run.location = f"{host.name}: {volid or storage or 'Standard-Speicher'}"
        run.file_name = volid or ""
        run.detail = {"host_id": host.id, "node": link.node, "vmid": link.vmid, "storage": storage, "volid": volid}
        _log(run, f"Fertig: {run.location}")
    except pve.PveError as exc:
        raise BackupError(str(exc)) from exc


# --- Ordner auf einem Rechner ------------------------------------------------
def _agent_script(platform: str, run_id: int, token: str, paths: List[str], excludes: List[str],
                  server_hint: str) -> Tuple[str, str]:
    """(shell, Skript) fuer den Agent."""
    if platform == "windows":
        quoted = " ".join("'" + p.replace("'", "''") + "'" for p in paths)
        excl = " ".join("'--exclude=" + e.replace("'", "''") + "'" for e in excludes)
        script = f"""$ErrorActionPreference = 'Stop'
$cfg = Get-Content -Raw 'C:\\ProgramData\\RIMPERIO\\agent.json' | ConvertFrom-Json
$server = if ($cfg.server_url) {{ $cfg.server_url.TrimEnd('/') }} else {{ '{server_hint}' }}
$tmp = Join-Path $env:TEMP 'rimperio-backup-{run_id}.tar.gz'
$paths = @({quoted})
$targs = @('-czf', $tmp) + @({excl}) + $paths
& tar.exe @targs
if ($LASTEXITCODE -gt 1) {{ throw "tar.exe beendet mit $LASTEXITCODE" }}
$k = @(); if ($cfg.insecure) {{ $k = @('-k') }}
& curl.exe -fsS @k -T $tmp -H 'X-Upload-Token: {token}' -H 'Content-Type: application/octet-stream' "$server/api/backup-upload/{run_id}"
$code = $LASTEXITCODE
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
if ($code -ne 0) {{ throw "Upload fehlgeschlagen (curl $code)" }}
Write-Output 'Sicherung übertragen.'
"""
        return "powershell", script
    q = " ".join("'" + p.replace("'", "'\\''") + "'" for p in paths)
    ex = " ".join("--exclude='" + e.replace("'", "'\\''") + "'" for e in excludes)
    script = f"""set -o pipefail
CFG=/opt/RMM/rimperio/etc/agent.json
SERVER=$(sed -n 's/.*"server_url"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$CFG" 2>/dev/null | head -n1)
SERVER=${{SERVER%/}}; [ -n "$SERVER" ] || SERVER='{server_hint}'
K=""; grep -q '"insecure"[[:space:]]*:[[:space:]]*true' "$CFG" 2>/dev/null && K="-k"
H=/opt/RMM/rimperio/lib/rimperio-helper
if [ "$(id -u)" -eq 0 ]; then
  PACK() {{ tar -czf - --ignore-failed-read --warning=no-file-changed {ex} -- {q}; }}
elif sudo -n "$H" backup-tar --check >/dev/null 2>&1; then
  PACK() {{ sudo -n "$H" backup-tar {" ".join("'--exclude=" + e.replace("'", "'\\''") + "'" for e in excludes)} {q}; }}
else
  echo "Hinweis: ohne root-Rechte – gesichert wird, was das Dienstkonto lesen darf (Agent neu installieren für das Hilfsskript mit backup-tar)." >&2
  PACK() {{ tar -czf - --ignore-failed-read --warning=no-file-changed {ex} -- {q}; }}
fi
PACK | curl -fsS $K -T - -H 'X-Upload-Token: {token}' -H 'Content-Type: application/octet-stream' "$SERVER/api/backup-upload/{run_id}"
rc=("${{PIPESTATUS[@]}}")
[ "${{rc[1]}}" -eq 0 ] || {{ echo "Upload fehlgeschlagen (curl ${{rc[1]}})" >&2; exit 3; }}
[ "${{rc[0]}}" -le 1 ] || {{ echo "tar beendet mit ${{rc[0]}}" >&2; exit 2; }}
echo "Sicherung übertragen."
"""
    return "bash", script


async def _run_files(db: AsyncSession, job: BackupJob, run: BackupRun) -> None:
    from . import public_url
    src = job.source or {}
    agent = (await db.execute(select(Agent).where(Agent.agent_uid == src.get("agent_uid")))).scalars().first()
    if agent is None:
        raise BackupError("Der Rechner ist nicht (mehr) als Agent eingebunden.")
    paths = [p.strip() for p in src.get("paths") or [] if p and p.strip()]
    if not paths:
        raise BackupError("Keine Ordner angegeben.")
    for p in paths:
        if "\n" in p or "\x00" in p:
            raise BackupError(f"Ungültiger Pfad: {p!r}")
    token = secrets.token_urlsafe(32)
    run.upload_token_hash = hashlib.sha256(token.encode()).hexdigest()
    platform = (agent.platform or "linux").lower()
    shell, script = _agent_script(platform, run.id, token, paths, [e for e in src.get("excludes") or [] if e],
                                  public_url.configured() or "")
    cmd = Command(agent_id=agent.id, shell=shell, command=script, timeout=int(src.get("timeout_min") or 240) * 60,
                  kind="shell", source=f"Backup: {job.name}", status="pending", created_by=run.created_by or "Zeitplan")
    db.add(cmd)
    await db.flush()
    run.command_id = cmd.id
    run.status = "waiting"
    run.detail = {"agent_uid": agent.agent_uid, "hostname": agent.hostname, "paths": paths}
    _log(run, f"Auftrag an {agent.hostname} geschickt – wartet auf die Daten.")
    await db.commit()
    try:
        from . import agentwake
        agentwake.notify(agent.id)
    except Exception:                                               # defensiv - der Agent holt sonst beim naechsten Check-in ab
        pass


async def receive_upload(db: AsyncSession, run: BackupRun, chunks: AsyncIterator[bytes]) -> None:
    """Strom vom Agent: verschluesseln, ablegen, Lauf abschliessen."""
    job = await db.get(BackupJob, run.job_id)
    target = await _target(db, job)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    name = _file_name(job, "tar.gz", job.encrypt)
    tmp = WORK_DIR / f"{run.id}-{name}"
    run.upload_token_hash = ""          # Einmal-Schluessel: verbraucht
    run.status = "running"
    _log(run, "Daten kommen an …")
    await db.commit()
    try:
        pw = await passphrase_for(db, job) if job.encrypt else ""
        with open(tmp, "wb") as out:
            enc = Encryptor(out, pw, {"content": "tar.gz", "name": name, "job": job.name,
                                      "created": _iso(datetime.utcnow()), "ext": "tar.gz"}) if job.encrypt else None
            plain = 0
            async for chunk in chunks:
                if enc:
                    await asyncio.to_thread(enc.write, chunk)
                else:
                    out.write(chunk)
                plain += len(chunk)
            if enc:
                enc.close()
        if plain < 20:
            raise BackupError("Vom Rechner kamen keine Daten an.")
        digest = await asyncio.to_thread(_sha256, tmp)
        store = await store_for(db, target)
        where = await asyncio.to_thread(store.put, tmp, _sub(job), name)
        run.file_name, run.location = name, where
        run.size_bytes, run.plain_bytes, run.sha256 = float(tmp.stat().st_size), float(plain), digest
        run.encrypted = bool(job.encrypt)
        run.detail = {**(run.detail or {}), "target_id": target.id, "sub": _sub(job), "content": "tar.gz"}
        target.last_ok_at, target.last_error = datetime.utcnow(), None
        _log(run, f"Abgelegt: {where} ({_fmt_size(run.size_bytes)})")
        run.status = "done"
    except Exception as exc:
        run.status, run.error = "error", str(exc) or exc.__class__.__name__
        _log(run, f"Fehler: {run.error}")
        raise
    finally:
        tmp.unlink(missing_ok=True)
        await _finish(db, job, run)


async def check_waiting(db: AsyncSession) -> None:
    """Rechner-Laeufe: Befehl gescheitert oder zu lange ohne Daten -> Fehler."""
    rows = (await db.execute(select(BackupRun).where(BackupRun.status == "waiting"))).scalars().all()
    for run in rows:
        cmd = await db.get(Command, run.command_id) if run.command_id else None
        reason = None
        if cmd and cmd.status in ("error", "timeout"):
            reason = f"Rechner meldet: {(cmd.stderr or cmd.stdout or cmd.status)[-600:]}"
        elif cmd and cmd.status == "done" and cmd.finished_at and datetime.utcnow() - cmd.finished_at > timedelta(minutes=5):
            reason = "Befehl beendet, aber keine Daten angekommen: " + (cmd.stderr or cmd.stdout or "")[-600:]
        elif datetime.utcnow() - (run.started_at or datetime.utcnow()) > UPLOAD_WAIT:
            reason = "Rechner hat sich nicht gemeldet (offline?)."
        if reason:
            run.status, run.error, run.upload_token_hash = "error", reason, ""
            _log(run, reason)
            job = await db.get(BackupJob, run.job_id)
            await _finish(db, job, run)


# =============================================================================
# Aufbewahrung, Wiederherstellen
# =============================================================================
async def delete_run_file(db: AsyncSession, run: BackupRun) -> None:
    det = run.detail or {}
    if not run.file_name or not det.get("target_id"):
        run.status = "deleted"
        return
    target = await db.get(BackupTarget, det["target_id"])
    if target is None:
        run.status = "deleted"
        return
    store = await store_for(db, target)
    await asyncio.to_thread(store.delete, det.get("sub") or "", run.file_name)
    run.status = "deleted"


async def prune(db: AsyncSession, job: BackupJob) -> int:
    if job.kind in ("nas_task", "pve"):
        return 0                                    # Aufbewahrung regelt das Zielsystem
    runs = (await db.execute(select(BackupRun).where(BackupRun.job_id == job.id, BackupRun.status == "done")
                             .order_by(desc(BackupRun.started_at)))).scalars().all()
    keep = max(1, job.keep_last or 1)
    cutoff = datetime.utcnow() - timedelta(days=job.keep_days) if job.keep_days else None
    removed = 0
    for i, r in enumerate(runs):
        if i < keep:
            continue
        if cutoff and r.started_at and r.started_at > cutoff:
            continue
        try:
            await delete_run_file(db, r)
            removed += 1
        except Exception as exc:                                    # defensiv
            r.error = f"Löschen am Ort: {exc}"
    await db.commit()
    return removed


async def fetch(db: AsyncSession, run: BackupRun, decrypt: bool = True) -> Tuple[Path, str]:
    """Datei vom Ort holen (und entschluesseln). Gibt (Pfad, Dateiname) zurueck; Aufrufer loescht."""
    det = run.detail or {}
    target = await db.get(BackupTarget, det.get("target_id")) if det.get("target_id") else None
    if target is None or not run.file_name:
        raise BackupError("Zu diesem Lauf liegt keine Datei bei RIMPERIO vor.")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    raw = WORK_DIR / f"dl-{run.id}-{secrets.token_hex(4)}"
    store = await store_for(db, target)
    await asyncio.to_thread(store.get, det.get("sub") or "", run.file_name, raw)
    if run.sha256 and await asyncio.to_thread(_sha256, raw) != run.sha256:
        raw.unlink(missing_ok=True)
        raise BackupError("Prüfsumme stimmt nicht – die Datei am Ort wurde verändert oder ist beschädigt.")
    if not (decrypt and run.encrypted):
        return raw, run.file_name
    job = await db.get(BackupJob, run.job_id)
    pw = await passphrase_for(db, job)
    plain = WORK_DIR / f"plain-{run.id}-{secrets.token_hex(4)}"

    def dec() -> None:
        with open(raw, "rb") as src, open(plain, "wb") as dst:
            decrypt_stream(src, dst, pw)
    try:
        await asyncio.to_thread(dec)
    finally:
        raw.unlink(missing_ok=True)
    name = run.file_name[:-len(".rimpbak")] if run.file_name.endswith(".rimpbak") else run.file_name
    return plain, name


async def verify(db: AsyncSession, run: BackupRun) -> dict:
    """Probe-Wiederherstellung: holen, Pruefsumme, entschluesseln - ohne etwas zu ersetzen."""
    path, name = await fetch(db, run, decrypt=True)
    try:
        size = path.stat().st_size
        detail = {"name": name, "plain_bytes": size, "checked_at": _iso(datetime.utcnow())}
        if name.endswith(".tar.gz"):
            import tarfile
            def count() -> int:
                with tarfile.open(path, "r:gz") as tf:
                    return sum(1 for _ in tf)
            detail["entries"] = await asyncio.to_thread(count)
        elif name.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(path) as zf:
                detail["entries"] = len(zf.namelist())
                bad = zf.testzip()
                if bad:
                    raise BackupError(f"ZIP beschädigt bei {bad}")
        run.detail = {**(run.detail or {}), "verified": detail}
        await db.commit()
        return detail
    finally:
        path.unlink(missing_ok=True)


async def to_restore(db: AsyncSession, run: BackupRun) -> str:
    """RIMPERIO-Sicherung entschluesselt unter *Sicherung* bereitlegen."""
    from . import backup as backup_module
    job = await db.get(BackupJob, run.job_id)
    if job.kind != "rimperio":
        raise BackupError("Nur Sicherungen von RIMPERIO selbst lassen sich hier zurückspielen.")
    path, _ = await fetch(db, run, decrypt=True)
    try:
        info = await asyncio.to_thread(backup_module.store_uploaded, path.read_bytes(), "")
    finally:
        path.unlink(missing_ok=True)
    return info.get("name", "")


# =============================================================================
# Takt
# =============================================================================
_overdue_sent: set = set()


async def tick() -> None:
    from . import alerts
    from .database import async_session
    async with async_session() as db:
        await check_waiting(db)
        now = datetime.utcnow()
        jobs = (await db.execute(select(BackupJob).where(BackupJob.enabled.is_(True)))).scalars().all()
        for job in jobs:
            if job.next_run_at is None and (job.schedule or {}).get("type", "daily") != "manual":
                job.next_run_at = next_run(job.schedule or {}, now)
                await db.commit()
            if job.next_run_at and job.next_run_at <= now and not running(job.id):
                try:
                    await start(job.id, "Zeitplan", trigger="schedule")
                except BackupError as exc:
                    logger.info("Auftrag %s nicht gestartet: %s", job.name, exc)
                    job.next_run_at = next_run(job.schedule or {}, now)
                    await db.commit()
            exp = expected_hours(job)
            overdue = bool(exp and job.created_at and (now - job.created_at).total_seconds() > exp * 3600
                           and (not job.last_ok_at or (now - job.last_ok_at).total_seconds() > exp * 3600))
            if overdue and job.id not in _overdue_sent:
                # Einmal melden, bis wieder eine Sicherung klappt - nicht stuendlich.
                _overdue_sent.add(job.id)
                await alerts.dispatch(
                    "backup_overdue", f"RIMPERIO: Sicherung „{job.name}“ überfällig",
                    f"Letzte erfolgreiche Sicherung: {_iso(job.last_ok_at) or 'noch nie'}",
                    level="warning", key=f"backup-over:{job.id}", detail={"link": "/backups/jobs"})
            elif not overdue:
                _overdue_sent.discard(job.id)


async def ensure_schema() -> None:
    from .database import Base, engine
    from .models import BACKUP_TABLES, VAULT_TABLES
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=VAULT_TABLES + BACKUP_TABLES))
        logger.info("Backup-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("Backup-Schema nicht angelegt: %s", exc)
    # Laeufe, die beim letzten Herunterfahren mitten drin waren, sind verloren.
    try:
        from .database import async_session
        async with async_session() as db:
            rows = (await db.execute(select(BackupRun).where(BackupRun.status == "running"))).scalars().all()
            for r in rows:
                r.status, r.error, r.finished_at = "error", "Abgebrochen (RIMPERIO wurde neu gestartet).", datetime.utcnow()
            await db.commit()
    except Exception:                                               # defensiv
        pass


async def loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await tick()
        except Exception as exc:                                    # defensiv
            logger.error("Backup-Takt: %s", exc)
        await asyncio.sleep(60)
