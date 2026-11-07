# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Sicherung und Wiederherstellung (ZIP, granular, optional
#               verschluesselt, mit Versionspruefung beim Zurueckspielen)
# ==============================================================================
"""
Sicherung und Wiederherstellung.

Aufbau einer Sicherungsdatei (immer eine ganz normale ZIP-Datei):

    rimperio-backup-20260909-120000.zip
      manifest.json      <- IMMER unverschluesselt lesbar
      payload.zip        <- die eigentlichen Daten (unverschluesselt)
        oder
      payload.bin        <- dieselbe payload.zip, AES-256-GCM verschluesselt

Warum das Manifest aussen und im Klartext liegt: beim Zurueckspielen muss man
*vor* der Passwortabfrage sehen koennen, von welchem Server und welcher Version
die Sicherung stammt. Sonst faellt ein Versionskonflikt erst auf, wenn die Daten
schon halb in der Datenbank stehen. Im Manifest steht deshalb kein Inhalt,
sondern nur: Version, Commit, Zeitpunkt, Umfang, Zeilenzahlen, Pruefsummen.

Der Umfang ist granular: jeder Bereich (Logs, Benutzer, Geraete, ...) wird
einzeln gesichert und laesst sich einzeln zurueckspielen. Man kann also die
Logs eines alten Servers behalten und nur die Benutzer zurueckholen.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete as sa_delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings

logger = logging.getLogger("rimperio.backup")

# Wo die Sicherungen liegen. Eigenes Docker-Volume, damit sie einen Neubau der
# Container ueberstehen (siehe docker-compose.yml -> backup_data).
BACKUP_DIR = Path(os.getenv("RIMPERIO_BACKUP_DIR", "/backups"))

# Verzeichnis mit Branding-Konfiguration und hochgeladenen Dateien.
DATA_DIR = Path(os.getenv("RIMPERIO_DATA_DIR", "/app/data"))

# Formatversion der Sicherungsdatei. Wird erhoeht, wenn sich der Aufbau der
# Datei so aendert, dass ein aelterer Server sie nicht mehr lesen kann.
FORMAT_VERSION = 1

# Wie viele Zeilen auf einmal aus der Datenbank geholt werden. Die logs-Tabelle
# hat Millionen Zeilen - alles auf einmal zu laden sprengt den Speicher.
CHUNK = 5000

NAME_PATTERN = re.compile(r"^rimperio-backup-\d{8}-\d{6}(-[a-z0-9]{6})?\.zip$")


# =============================================================================
# Umfang: was gehoert zu welchem Bereich
# =============================================================================
# Reihenfolge ist wichtig: beim Zurueckspielen muessen Tabellen, auf die andere
# per Fremdschluessel zeigen, zuerst da sein (agents vor logs, users vor mfa_*).
SCOPES: Dict[str, dict] = {
    "settings": {
        "label": "Einstellungen",
        "label_en": "Settings",
        "hint": "Allgemeine Einstellungen, Aufbewahrung, Archivierung, KI, Mail.",
        "tables": ["settings"],
        "order": 10,
    },
    "branding": {
        "label": "Erscheinungsbild",
        "label_en": "Appearance",
        "hint": "Farben, Texte, hochgeladenes Logo und Favicon.",
        "tables": [],
        "files": ["branding_config.json", "assets"],
        "order": 20,
    },
    "users": {
        "label": "Benutzer & Anmeldung",
        "label_en": "Users & sign-in",
        "hint": "Konten, Rollen, MFA-Geheimnisse, Passkeys, Backup-Codes.",
        "tables": ["users", "mfa_backup_codes", "webauthn_credentials"],
        "order": 30,
        "sensitive": True,
    },
    "agents": {
        "label": "Geräte & Agent-Token",
        "label_en": "Devices & agent tokens",
        "hint": "Bekannte Geräte samt Aufbewahrungsregeln und die Agent-Token.",
        "tables": ["agents", "agent_tokens"],
        "order": 40,
        "sensitive": True,
    },
    "webhooks": {
        "label": "Webhooks",
        "label_en": "Webhooks",
        "hint": "Webhook-Definitionen inklusive Token.",
        "tables": ["webhooks"],
        "order": 50,
        "sensitive": True,
    },
    "rmm": {
        "label": "Fernwartung (RMM)",
        "label_en": "Remote management (RMM)",
        "hint": "Skripte, Checks, Patch-Stand, Befehlsverlauf und Alarme der RMM-Agents.",
        "tables": ["scripts", "checks", "check_results", "patch_reports", "commands", "rmm_alerts"],
        "order": 55,
    },
    "patching": {
        "label": "Patchmanagement",
        "label_en": "Patch management",
        "hint": "Richtlinien, Update-Stand, Läufe, Proxmox-Anbindung, Update-Quellen und Eingang.",
        "tables": ["patch_policies", "patch_items", "patch_runs", "patch_knowledge", "pve_hosts",
                   "pve_links", "update_sources", "advisories", "notifications"],
        "order": 56,
        "sensitive": True,
    },
    "snmp": {
        "label": "SNMP-Überwachung",
        "label_en": "SNMP monitoring",
        "hint": "Geräte, Zugangsdaten, eigene Vorlagen und letzte Messwerte (ohne Verlauf).",
        "tables": ["snmp_credentials", "snmp_templates", "snmp_devices", "snmp_metrics"],
        "order": 57,
        "sensitive": True,
    },
    "logs": {
        "label": "Logdaten",
        "label_en": "Log data",
        "hint": "Die eigentlichen Logzeilen. Mit Abstand der größte Teil.",
        "tables": ["logs"],
        "order": 60,
    },
}

# Bereiche, die standardmaessig angehakt sind, wenn vor einem Eingriff gefragt wird.
DEFAULT_SCOPES = ["settings", "branding", "users", "agents", "webhooks", "rmm", "patching", "snmp"]


def scope_catalog() -> List[dict]:
    """Der Umfang als Liste fuer die Oberflaeche."""
    return [
        {
            "id": key,
            "label": spec["label"],
            "label_en": spec["label_en"],
            "hint": spec["hint"],
            "sensitive": bool(spec.get("sensitive")),
            "default": key in DEFAULT_SCOPES,
        }
        for key, spec in sorted(SCOPES.items(), key=lambda kv: kv[1]["order"])
    ]


def normalize_scopes(scopes: Optional[Iterable[str]]) -> List[str]:
    """Unbekannte Bereiche verwerfen und in die richtige Reihenfolge bringen."""
    wanted = set(scopes or [])
    return [k for k, _ in sorted(SCOPES.items(), key=lambda kv: kv[1]["order"]) if k in wanted]


# =============================================================================
# Versionen vergleichen
# =============================================================================
def parse_version(value: str) -> Tuple[int, ...]:
    """'2026.08.14.14.00.00' -> (2026, 8, 14, 14, 0, 0). Unlesbares -> ()."""
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(p) for p in parts[:6]) if parts else ()


def compare_versions(backup_version: str, server_version: str) -> dict:
    """Passt die Sicherung zu diesem Server?

    Bewusst nicht nur ja/nein: eine aeltere Sicherung ist voellig normal (genau
    dafuer gibt es Sicherungen), eine *neuere* dagegen ist ein Warnsignal - der
    Server ist dann aelter als die Daten, die man ihm geben will, und kann
    Spalten enthalten, die er nicht kennt.
    """
    a, b = parse_version(backup_version), parse_version(server_version)
    if not a or not b:
        return {
            "level": "warn",
            "match": False,
            "message": ("Die Version der Sicherung oder des Servers ist nicht lesbar. "
                        "Das Zurückspielen ist möglich, aber ungeprüft."),
        }
    if a == b:
        return {"level": "ok", "match": True,
                "message": "Sicherung und Server haben dieselbe Version."}
    if a < b:
        return {
            "level": "info", "match": False,
            "message": (f"Die Sicherung ({backup_version}) ist älter als der Server "
                        f"({server_version}). Das ist der Normalfall: fehlende Spalten "
                        f"füllt der Server beim Start mit seinen Standardwerten."),
        }
    return {
        "level": "blocking", "match": False,
        "message": (f"Die Sicherung ({backup_version}) ist NEUER als dieser Server "
                    f"({server_version}). Sie kann Daten enthalten, die dieser Stand nicht "
                    f"kennt. Erst den Server aktualisieren — oder das Zurückspielen "
                    f"ausdrücklich erzwingen."),
    }


# =============================================================================
# Verschluesselung (AES-256-GCM, Schluessel aus dem Passwort via PBKDF2)
# =============================================================================
KDF_ITERATIONS = 210_000


def _derive_key(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def encrypt_bytes(plain: bytes, passphrase: str) -> Tuple[bytes, dict]:
    """Verschluesselt und gibt (Chiffrat, Parameter fuers Manifest) zurueck."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(passphrase, salt)
    cipher = AESGCM(key).encrypt(nonce, plain, None)
    return cipher, {
        "algorithm": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": KDF_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
    }


def decrypt_bytes(cipher: bytes, passphrase: str, params: dict) -> bytes:
    """Entschluesselt. Falsches Passwort -> ValueError mit klarer Meldung."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        salt = base64.b64decode(params["salt"])
        nonce = base64.b64decode(params["nonce"])
        iterations = int(params.get("iterations", KDF_ITERATIONS))
    except Exception as exc:                                        # defensiv
        raise ValueError(f"Verschlüsselungsangaben unlesbar: {exc}") from exc

    key = _derive_key(passphrase, salt, iterations)
    try:
        return AESGCM(key).decrypt(nonce, cipher, None)
    except InvalidTag as exc:
        raise ValueError("Falsches Passwort oder beschädigte Sicherungsdatei.") from exc


# =============================================================================
# Datenbank -> JSON
# =============================================================================
def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        # Postgres-Schreibweise fuer bytea: so laesst es sich beim Zurueckspielen
        # ohne Sonderbehandlung wieder einlesen.
        return "\\x" + bytes(value).hex()
    return str(value)


async def _table_columns(session: AsyncSession, table: str) -> List[Tuple[str, str]]:
    """Spalten der Tabelle mitsamt Typ, wie sie *hier* wirklich existieren.

    Nicht aus den Modellen, sondern aus der Datenbank: eine Bestandsdatenbank
    kann Spalten haben (oder ihr koennen welche fehlen), die das Modell anders
    sieht. So bleibt der Abzug ehrlich.

    Der Typ wird gebraucht, weil beim Zurueckspielen jeder Wert als Text
    ankommt (in JSON gibt es kein Datum). Postgres bekommt deshalb ein
    ausdrueckliches CAST und macht das Umwandeln selbst - zuverlaessiger, als
    es in Python nachzubauen.
    """
    result = await session.execute(
        text("SELECT column_name, data_type, udt_name FROM information_schema.columns "
             "WHERE table_schema = current_schema() AND table_name = :t "
             "ORDER BY ordinal_position"),
        {"t": table},
    )
    columns: List[Tuple[str, str]] = []
    for name, data_type, udt_name in result.fetchall():
        # 'USER-DEFINED' und 'ARRAY' sagen nichts - der echte Name steht in udt_name
        # ('_text' ist dabei Postgres' interne Schreibweise fuer text[]).
        cast = udt_name if data_type in ("USER-DEFINED", "ARRAY") else data_type
        columns.append((name, cast))
    return columns


def _column_names(columns: List[Tuple[str, str]]) -> List[str]:
    return [name for name, _ in columns]


async def _dump_table(session: AsyncSession, table: str, sink: io.BytesIO) -> int:
    """Schreibt eine Tabelle als JSON-Lines (eine Zeile = ein Datensatz)."""
    columns = _column_names(await _table_columns(session, table))
    if not columns:
        return 0

    quoted = ", ".join(f'"{c}"' for c in columns)
    order = '"id"' if "id" in columns else quoted
    written = 0
    offset = 0

    while True:
        rows = (await session.execute(
            text(f'SELECT {quoted} FROM "{table}" ORDER BY {order} LIMIT :lim OFFSET :off'),
            {"lim": CHUNK, "off": offset},
        )).fetchall()
        if not rows:
            break
        for row in rows:
            record = dict(zip(columns, row))
            sink.write(json.dumps(record, default=_json_default, ensure_ascii=False).encode("utf-8"))
            sink.write(b"\n")
            written += 1
        offset += len(rows)
        if len(rows) < CHUNK:
            break

    return written


async def _restore_table(session: AsyncSession, table: str, payload: bytes,
                         mode: str) -> dict:
    """Spielt eine Tabelle aus JSON-Lines zurueck.

    mode="replace": Tabelle vorher leeren. mode="merge": vorhandene Datensaetze
    mit gleicher id bleiben unangetastet (ON CONFLICT DO NOTHING).
    """
    columns = await _table_columns(session, table)
    if not columns:
        return {"table": table, "inserted": 0, "skipped": 0,
                "note": "Tabelle existiert auf diesem Server nicht."}

    types = dict(columns)
    known = set(types)
    if mode == "replace":
        # TRUNCATE ... CASCADE waere schneller, riss aber auch Tabellen mit,
        # die gar nicht Teil der Auswahl sind. DELETE bleibt beim Gewaehlten.
        await session.execute(text(f'DELETE FROM "{table}"'))

    inserted = 0
    skipped = 0
    dropped_columns: set = set()
    batch: List[dict] = []

    async def flush(rows: List[dict]) -> None:
        nonlocal inserted
        if not rows:
            return
        cols = sorted({c for row in rows for c in row})
        # Jeder Wert kommt als Text an - JSON kennt weder Datum noch Zeitstempel.
        # Der doppelte CAST ist Absicht: der innere legt den Parametertyp auf
        # text fest (sonst leitet asyncpg ihn aus dem Ziel ab und verlangt ein
        # datetime-Objekt), der aeussere laesst Postgres daraus den richtigen Typ
        # machen. Damit muss die Typumwandlung nicht in Python nachgebaut werden.
        placeholders = ", ".join(f'CAST(CAST(:{c} AS text) AS {types[c]})' for c in cols)
        quoted = ", ".join(f'"{c}"' for c in cols)
        conflict = ' ON CONFLICT DO NOTHING' if "id" in known else ""
        # Fehlende Schluessel je Zeile auffuellen, sonst beschwert sich der Treiber.
        normalized = [{c: row.get(c) for c in cols} for row in rows]
        await session.execute(
            text(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}){conflict}'),
            normalized,
        )
        inserted += len(normalized)

    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(record, dict):
            skipped += 1
            continue

        clean = {}
        for key, value in record.items():
            if key not in known:
                dropped_columns.add(key)
                continue
            if value is None:
                clean[key] = None
                continue
            # Alles als Text uebergeben - das CAST oben macht daraus den
            # richtigen Typ. Verschachteltes JSON wird dafuer wieder zu Text.
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            elif not isinstance(value, str):
                value = str(value)
            clean[key] = value
        batch.append(clean)

        if len(batch) >= CHUNK:
            await flush(batch)
            batch = []

    await flush(batch)

    # Sequenzen nachziehen: sonst vergibt die naechste Einfuegung eine id, die
    # es schon gibt, und der Server wirft Unique-Fehler.
    if "id" in known:
        try:
            await session.execute(text(
                f"SELECT setval(pg_get_serial_sequence('\"{table}\"', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM \"{table}\"), 1), true)"
            ))
        except Exception as exc:                                    # defensiv
            logger.warning("Sequenz für %s nicht angepasst: %s", table, exc)

    result = {"table": table, "inserted": inserted, "skipped": skipped}
    if dropped_columns:
        result["dropped_columns"] = sorted(dropped_columns)
    return result


# =============================================================================
# Dateien (Branding-Konfiguration und Uploads)
# =============================================================================
def _collect_files(names: Iterable[str]) -> List[Tuple[str, bytes]]:
    """Sammelt Dateien aus dem Datenverzeichnis als (relativer Pfad, Inhalt)."""
    collected: List[Tuple[str, bytes]] = []
    for name in names:
        source = DATA_DIR / name
        if source.is_file():
            collected.append((name, source.read_bytes()))
        elif source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    collected.append((str(path.relative_to(DATA_DIR)), path.read_bytes()))
    return collected


def _write_files(entries: Iterable[Tuple[str, bytes]]) -> int:
    """Schreibt Dateien ins Datenverzeichnis zurueck - nur innerhalb davon."""
    written = 0
    root = DATA_DIR.resolve()
    for relative, content in entries:
        target = (DATA_DIR / relative).resolve()
        # Ein praeparierter Pfad wie "../../etc/passwd" darf nicht aus dem
        # Datenverzeichnis herausfuehren.
        if root not in target.parents and target != root:
            logger.warning("Datei aus der Sicherung übersprungen (Pfad außerhalb): %s", relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written += 1
    return written


# =============================================================================
# Sicherung erstellen
# =============================================================================
async def create_backup(
    session: AsyncSession,
    scopes: Optional[Iterable[str]] = None,
    passphrase: str = "",
    note: str = "",
    trigger: str = "manual",
    created_by: str = "",
) -> dict:
    """Erstellt eine Sicherung und gibt ihr Manifest zurueck."""
    selected = normalize_scopes(scopes if scopes is not None else DEFAULT_SCOPES)
    if not selected:
        raise ValueError("Es wurde kein Bereich zum Sichern ausgewählt.")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()

    payload_buffer = io.BytesIO()
    counts: Dict[str, Dict[str, int]] = {}
    file_count = 0

    with zipfile.ZipFile(payload_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as payload:
        for scope in selected:
            spec = SCOPES[scope]
            scope_counts: Dict[str, int] = {}

            for table in spec.get("tables", []):
                sink = io.BytesIO()
                rows = await _dump_table(session, table, sink)
                scope_counts[table] = rows
                # Die Logzeilen komprimieren sich noch einmal deutlich besser,
                # wenn sie vor dem Packen durch gzip laufen.
                payload.writestr(f"data/{scope}/{table}.jsonl.gz",
                                 gzip.compress(sink.getvalue(), compresslevel=6))

            for relative, content in _collect_files(spec.get("files", [])):
                payload.writestr(f"files/{scope}/{relative}", content)
                file_count += 1

            counts[scope] = scope_counts

        payload.writestr("payload-info.json", json.dumps(
            {"scopes": selected, "counts": counts, "files": file_count}, indent=2))

    payload_bytes = payload_buffer.getvalue()
    checksum = hashlib.sha256(payload_bytes).hexdigest()

    encryption: Optional[dict] = None
    if passphrase:
        payload_bytes, encryption = encrypt_bytes(payload_bytes, passphrase)

    now = datetime.now(timezone.utc)
    name = f"rimperio-backup-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.zip"
    target = BACKUP_DIR / name

    manifest = {
        "format_version": FORMAT_VERSION,
        "name": name,
        "created_at": now.isoformat(),
        "server_version": settings.app_version,
        "note": (note or "").strip()[:500],
        "trigger": trigger,
        "created_by": created_by,
        "scopes": selected,
        "counts": counts,
        "files": file_count,
        "encrypted": bool(encryption),
        "encryption": encryption,
        # Pruefsumme des *unverschluesselten* Inhalts: so laesst sich nach dem
        # Entschluesseln pruefen, ob die Datei unterwegs beschaedigt wurde.
        "payload_sha256": checksum,
        "payload_bytes": len(payload_bytes),
    }

    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as container:
        container.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        container.writestr("payload.bin" if encryption else "payload.zip", payload_bytes)

    manifest["size_bytes"] = target.stat().st_size
    manifest["duration_seconds"] = round(time.time() - started, 1)
    logger.warning("Sicherung erstellt: %s (%s Bereiche, %.1f MB)",
                   name, len(selected), manifest["size_bytes"] / 1024 / 1024)
    return manifest


# =============================================================================
# Sicherungen auflisten / lesen / loeschen
# =============================================================================
def _safe_path(name: str) -> Path:
    """Schuetzt vor Pfadangaben wie '../../etc/passwd'."""
    if not NAME_PATTERN.match(name or ""):
        raise ValueError("Unbekannter Sicherungsname.")
    return BACKUP_DIR / name


def read_manifest(path: Path) -> dict:
    """Liest das Manifest aus einer Sicherungsdatei."""
    with zipfile.ZipFile(path) as container:
        if "manifest.json" not in container.namelist():
            raise ValueError("Das ist keine RIMPERIO-Sicherung (manifest.json fehlt).")
        manifest = json.loads(container.read("manifest.json").decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Manifest hat ein unerwartetes Format.")
    manifest["size_bytes"] = path.stat().st_size
    return manifest


def list_backups() -> List[dict]:
    """Alle Sicherungen, neueste zuerst. Unlesbare werden mit Fehler gelistet."""
    if not BACKUP_DIR.is_dir():
        return []
    entries: List[dict] = []
    for path in sorted(BACKUP_DIR.glob("rimperio-backup-*.zip"), reverse=True):
        try:
            manifest = read_manifest(path)
            manifest["compatibility"] = compare_versions(
                manifest.get("server_version", ""), settings.app_version)
            entries.append(manifest)
        except Exception as exc:
            entries.append({
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "error": f"Nicht lesbar: {exc}",
            })
    return entries


def delete_backup(name: str) -> bool:
    path = _safe_path(name)
    if not path.is_file():
        return False
    path.unlink()
    logger.warning("Sicherung gelöscht: %s", name)
    return True


def backup_path(name: str) -> Path:
    path = _safe_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"Sicherung '{name}' gibt es nicht.")
    return path


def store_uploaded(content: bytes, original_name: str = "") -> dict:
    """Legt eine hochgeladene Sicherung ab und gibt ihr Manifest zurueck."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        manifest = read_manifest_from_bytes(content)
    except Exception as exc:
        raise ValueError(f"Die Datei ist keine gültige RIMPERIO-Sicherung: {exc}") from exc

    name = manifest.get("name") or ""
    if not NAME_PATTERN.match(name):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"rimperio-backup-{stamp}-{secrets.token_hex(3)}.zip"
    target = BACKUP_DIR / name
    if target.exists():
        stem = target.stem
        target = BACKUP_DIR / f"{stem[:-7] if len(stem) > 7 else stem}-{secrets.token_hex(3)}.zip"
    target.write_bytes(content)

    result = read_manifest(target)
    result["uploaded_from"] = original_name
    result["compatibility"] = compare_versions(result.get("server_version", ""),
                                               settings.app_version)
    logger.warning("Sicherung hochgeladen: %s (%s)", target.name, original_name or "ohne Namen")
    return result


def read_manifest_from_bytes(content: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(content)) as container:
        if "manifest.json" not in container.namelist():
            raise ValueError("manifest.json fehlt")
        return json.loads(container.read("manifest.json").decode("utf-8"))


def inspect(name: str) -> dict:
    """Vorschau: was steckt drin, und passt es zu diesem Server?"""
    manifest = read_manifest(backup_path(name))
    manifest["compatibility"] = compare_versions(
        manifest.get("server_version", ""), settings.app_version)
    manifest["scope_details"] = [
        {
            "id": scope,
            "label": SCOPES.get(scope, {}).get("label", scope),
            "label_en": SCOPES.get(scope, {}).get("label_en", scope),
            "rows": sum((manifest.get("counts", {}).get(scope) or {}).values()),
            "known": scope in SCOPES,
        }
        for scope in manifest.get("scopes", [])
    ]
    return manifest


# =============================================================================
# Zurueckspielen
# =============================================================================
async def restore_backup(
    session: AsyncSession,
    name: str,
    scopes: Optional[Iterable[str]] = None,
    passphrase: str = "",
    mode: str = "replace",
    force_version_mismatch: bool = False,
) -> dict:
    """Spielt ausgewaehlte Bereiche einer Sicherung zurueck.

    mode="replace" ersetzt den Inhalt der betroffenen Tabellen,
    mode="merge" ergaenzt nur, was noch fehlt.
    """
    if mode not in ("replace", "merge"):
        raise ValueError("Unbekannter Modus (erlaubt: replace, merge).")

    path = backup_path(name)
    manifest = read_manifest(path)
    compatibility = compare_versions(manifest.get("server_version", ""), settings.app_version)

    if compatibility["level"] == "blocking" and not force_version_mismatch:
        raise ValueError(compatibility["message"])

    if manifest.get("format_version", 1) > FORMAT_VERSION and not force_version_mismatch:
        raise ValueError(
            f"Das Dateiformat der Sicherung (v{manifest.get('format_version')}) ist neuer als "
            f"das, was dieser Server lesen kann (v{FORMAT_VERSION}). Erst aktualisieren."
        )

    with zipfile.ZipFile(path) as container:
        names = container.namelist()
        if manifest.get("encrypted"):
            if not passphrase:
                raise ValueError("Diese Sicherung ist verschlüsselt — Passwort erforderlich.")
            if "payload.bin" not in names:
                raise ValueError("Verschlüsselte Sicherung ohne payload.bin.")
            payload_bytes = decrypt_bytes(container.read("payload.bin"), passphrase,
                                          manifest.get("encryption") or {})
        else:
            if "payload.zip" not in names:
                raise ValueError("Sicherung ohne payload.zip.")
            payload_bytes = container.read("payload.zip")

    expected = manifest.get("payload_sha256")
    if expected and hashlib.sha256(payload_bytes).hexdigest() != expected:
        raise ValueError("Prüfsumme stimmt nicht — die Sicherungsdatei ist beschädigt.")

    available = [s for s in manifest.get("scopes", []) if s in SCOPES]
    selected = normalize_scopes(scopes if scopes is not None else available)
    selected = [s for s in selected if s in available]
    if not selected:
        raise ValueError("Keiner der gewählten Bereiche steckt in dieser Sicherung.")

    report: List[dict] = []
    restored_files = 0

    with zipfile.ZipFile(io.BytesIO(payload_bytes)) as payload:
        entries = payload.namelist()

        # Tabellen zuerst in der definierten Reihenfolge, danach die Dateien.
        for scope in selected:
            for table in SCOPES[scope].get("tables", []):
                entry = f"data/{scope}/{table}.jsonl.gz"
                if entry not in entries:
                    report.append({"table": table, "inserted": 0, "skipped": 0,
                                   "note": "In der Sicherung nicht enthalten."})
                    continue
                raw = gzip.decompress(payload.read(entry))
                report.append(await _restore_table(session, table, raw, mode))

        for scope in selected:
            prefix = f"files/{scope}/"
            files = [(e[len(prefix):], payload.read(e)) for e in entries if e.startswith(prefix)]
            restored_files += _write_files(files)

    await session.commit()

    result = {
        "restored": True,
        "backup": name,
        "mode": mode,
        "scopes": selected,
        "tables": report,
        "files": restored_files,
        "compatibility": compatibility,
        "backup_version": manifest.get("server_version"),
        "server_version": settings.app_version,
    }
    logger.warning("Sicherung zurückgespielt: %s (%s, Modus %s)", name, ", ".join(selected), mode)
    return result


# =============================================================================
# Aufraeumen
# =============================================================================
def prune(keep: int = 0) -> int:
    """Alte Sicherungen loeschen. keep=0 nimmt RIMPERIO_KEEP_BACKUPS (Standard 10)."""
    keep = keep or int(os.getenv("RIMPERIO_KEEP_BACKUPS", "10"))
    if keep <= 0 or not BACKUP_DIR.is_dir():
        return 0
    files = sorted(BACKUP_DIR.glob("rimperio-backup-*.zip"), reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Alte Sicherung %s nicht gelöscht: %s", path.name, exc)
    if removed:
        logger.info("%s alte Sicherungen entfernt (behalten: %s)", removed, keep)
    return removed


def disk_usage() -> dict:
    """Wie viel Platz belegen die Sicherungen, wie viel ist noch frei?"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in BACKUP_DIR.glob("rimperio-backup-*.zip"))
    total, _, free = shutil.disk_usage(BACKUP_DIR)
    return {
        "directory": str(BACKUP_DIR),
        "backup_bytes": used,
        "free_bytes": free,
        "total_bytes": total,
        "count": len(list(BACKUP_DIR.glob("rimperio-backup-*.zip"))),
    }
