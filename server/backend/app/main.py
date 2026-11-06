# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.09.15.20.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - FastAPI Hauptanwendung
# ==============================================================================

from datetime import datetime, timedelta, timezone
from typing import Optional
import hashlib
# secrets wird nicht mehr gebraucht: Schluessel wuerfelt app/tokens.py
import logging
import asyncio
import os
from fastapi import FastAPI, HTTPException, Query, Header, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, desc, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from . import archiving
from . import discovery
from . import diskguard
from . import fritzbox
from . import journal
from . import shell
from . import tokens
from . import updater
from . import watchdog
from .config import settings, validate_security_settings
from .database import get_db, async_session, engine
from .limiter import limiter, agent_key, client_ip as real_client_ip
from .models import Webhook, Log, Agent, AgentToken
from sqlalchemy import func
from .schemas import LogResponse, LogDetailResponse, LogIngestRequest, LogIngestResponse
from .routes import (auth_router, mfa_router, health_router, users_router, agents_router,
                     agent_tokens_router, logs_router, webhooks_router, settings_router,
                     database_router, ldap_router, archiving_router, passkey_router,
                     diagnostics_router, updates_router, backup_router, mobile_router,
                     ai_router, stacks_router, mail_router, shell_router,
                     journal_router, containers_router, sso_router, about_router,
                     discovery_router,
                     alerts_router,
                     caddy as caddy_router, network as network_router)
from .branding import branding_router

# =============================================================================
# Security-Headers Middleware
# =============================================================================
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Ein Reverse Proxy mit TLS davor - dann soll der Browser gar nicht erst
        # versuchen, unverschluesselt anzuklopfen. Nur setzen, wenn die Anfrage
        # wirklich ueber HTTPS kam: sonst sperrt man sich auf einer reinen
        # HTTP-Installation selbst aus.
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if forwarded_proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


# =============================================================================
# App-Instanz
# =============================================================================
app = FastAPI(
    title="RIMPERIO",
    description="Zentraler Log-Server",
    version=settings.app_version,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    # Swagger/ReDoc nur mit Auth erreichbar machen würde redoc_url/docs_url=None erfordern
    # Für interne Tools lassen wir es offen, aber per Caddy-Auth absicherbar
)

# Rate-Limiter am App registrieren
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# GZip für alle Responses > 1 KB
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS. Frueher stand hier als Vorgabe ein "*" - jede fremde Seite durfte damit
# Antworten dieser API lesen. Gebraucht wird das nicht: Oberflaeche und API
# liegen hinter demselben Caddy, also derselben Herkunft. Ohne Eintrag in
# CORS_ORIGINS wird die Middleware deshalb gar nicht erst eingehaengt - das ist
# die engste und zugleich die richtige Einstellung fuer den Regelfall.
_cors_origins = settings.cors_origins_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,      # Bearer-Token in Header, kein Cookie-basiertes Auth
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

# Security-Headers zuletzt (werden nach CORS-Middleware eingefügt)
app.add_middleware(SecurityHeadersMiddleware)

# =============================================================================
# Routes registrieren
# =============================================================================
app.include_router(auth_router)
app.include_router(mfa_router)
app.include_router(health_router)
app.include_router(users_router)
app.include_router(agents_router)
app.include_router(agent_tokens_router)
app.include_router(logs_router)
app.include_router(webhooks_router)
app.include_router(branding_router)
app.include_router(settings_router)
app.include_router(database_router)
app.include_router(ldap_router)
app.include_router(archiving_router)
app.include_router(passkey_router)
app.include_router(diagnostics_router)
app.include_router(updates_router)
app.include_router(backup_router)
app.include_router(mobile_router)
app.include_router(ai_router)
app.include_router(stacks_router)
app.include_router(mail_router)
app.include_router(shell_router)
app.include_router(journal_router)
app.include_router(containers_router)
app.include_router(sso_router)
app.include_router(about_router)
app.include_router(discovery_router)
app.include_router(alerts_router)
app.include_router(caddy_router.router)
app.include_router(network_router.router)


# =============================================================================
# Startup Events
# =============================================================================
@app.on_event("startup")
async def startup_security_check():
    validate_security_settings()


@app.on_event("startup")
async def ensure_token_schema():
    """Bringt die Schluesseltabelle auf den Stand dieser Fassung.

    Was dabei passiert und warum es nichts kaputt macht: die neuen Spalten
    kommen dazu, und fuer jeden vorhandenen Klartext-Schluessel wird der
    Abdruck nachgetragen. Der Klartext bleibt zunaechst stehen - sonst waere
    ein Schluessel, den jemand noch nirgends notiert hat, unwiederbringlich
    weg. Die Oberflaeche zeigt solche Schluessel als "ungeschuetzt" an und
    bietet einen Knopf zum Ueberfuehren.

    Bestehende Agents merken davon nichts: ihr Schluessel gilt unveraendert.
    """
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            for statement in (
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS device_type VARCHAR(50)",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS token_hash VARCHAR(64)",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS prefix VARCHAR(24)",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'agent'",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS agent_id INTEGER",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS max_uses INTEGER",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS allowed_cidrs TEXT",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS last_used_ip VARCHAR(45)",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS created_by VARCHAR(50)",
                "ALTER TABLE agent_tokens ADD COLUMN IF NOT EXISTS note TEXT",
                # Neue Schluessel speichern keinen Klartext mehr - die Spalte
                # muss also leer bleiben duerfen.
                "ALTER TABLE agent_tokens ALTER COLUMN token DROP NOT NULL",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_hash ON agent_tokens(token_hash) "
                "WHERE token_hash IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS idx_agent_tokens_agent ON agent_tokens(agent_id)",
            ):
                try:
                    await conn.exec_driver_sql(statement)
                except Exception as exc:
                    logger.debug("Migration übersprungen (%s): %s", statement[:60], exc)
        logger.info("agent_tokens schema ready")
    except Exception as exc:
        logger.warning("agent_tokens migration skipped: %s", exc)

    # Abdruecke fuer den Altbestand nachtragen und den Generalschluessel
    # kennzeichnen. Danach funktioniert die Pruefung fuer alle gleich.
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(AgentToken).where(AgentToken.token.is_not(None))
            )).scalars().all()
            for row in rows:
                if not row.token_hash:
                    row.token_hash = tokens.digest(row.token)
                if not row.prefix:
                    row.prefix = row.token[:10]
                if row.name == tokens.GLOBAL_TOKEN_NAME:
                    row.kind = "global"
                elif not row.kind:
                    row.kind = "agent"
            if rows:
                await session.commit()
                logger.warning(
                    "%s Schlüssel aus einer früheren Fassung gefunden. Sie gelten weiter, "
                    "liegen aber noch im Klartext in der Datenbank — unter "
                    "Sicherheit → Schlüssel lassen sie sich überführen.", len(rows))

            _, plain = await tokens.ensure_global(session, created_by="system")
            await session.commit()
            if plain:
                # Der einzige Moment, in dem ein Schluessel je im Protokoll steht:
                # bei der Erstinstallation. Sonst kaeme niemand an den ersten
                # Schluessel heran.
                logger.warning(
                    "Generalschlüssel angelegt (nur jetzt sichtbar): %s", plain)
    except Exception as exc:
        logger.warning("Schlüssel-Migration übersprungen: %s", exc)


@app.on_event("startup")
async def ensure_system_events_table():
    """Das Systemtagebuch - eigene Tabelle, damit ein Aufraeumlauf sie nicht trifft."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS system_events (
                    id SERIAL PRIMARY KEY,
                    at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    category VARCHAR(40) NOT NULL,
                    level VARCHAR(20) NOT NULL DEFAULT 'info',
                    event VARCHAR(80) NOT NULL,
                    message TEXT NOT NULL,
                    actor VARCHAR(100),
                    source_ip VARCHAR(45),
                    target VARCHAR(200),
                    ok BOOLEAN NOT NULL DEFAULT TRUE,
                    duration_ms INTEGER,
                    detail JSONB DEFAULT '{}'
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_system_events_at ON system_events(at DESC)")
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_system_events_category ON system_events(category)")
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_system_events_level ON system_events(level)")
        logger.info("system_events ready")
    except Exception as exc:
        logger.warning("system_events migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_agent_retention_columns():
    """Fügt retention_max_logs / retention_days zu agents hinzu falls noch nicht vorhanden."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS retention_max_logs INTEGER"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS retention_days INTEGER"
            )
        logger.info("agents retention columns ready")
    except Exception as exc:
        logger.warning("agents retention migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_webauthn_table():
    """Tabelle für Passkeys (WebAuthn) anlegen, falls noch nicht vorhanden."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS webauthn_credentials (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    credential_id VARCHAR(512) UNIQUE NOT NULL,
                    public_key TEXT NOT NULL,
                    sign_count INTEGER NOT NULL DEFAULT 0,
                    name VARCHAR(100),
                    transports VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(user_id)"
            )
        logger.info("webauthn_credentials ready")
    except Exception as exc:
        logger.warning("webauthn table migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_auth_source_column():
    """Kennzeichnet, ob ein Konto lokal ist oder aus dem Verzeichnis (LDAP) kommt."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(20) NOT NULL DEFAULT 'local'"
            )
        logger.info("users.auth_source ready")
    except Exception as exc:
        logger.warning("users.auth_source migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_mfa_schema():
    """Fügt MFA-Spalten zu users hinzu und legt mfa_backup_codes-Tabelle an (Migration)."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(64)"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_failed_count INTEGER NOT NULL DEFAULT 0"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_locked_until TIMESTAMP"
            )
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS mfa_backup_codes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    code_hash VARCHAR(255) NOT NULL,
                    used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_mfa_backup_codes_user_id ON mfa_backup_codes(user_id)"
            )
        logger.info("MFA schema ready")
    except Exception as exc:
        logger.warning("MFA schema migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_app_login_tokens_table():
    """Erstellt die app_login_tokens-Tabelle falls sie noch nicht existiert (Migration)."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS app_login_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_app_login_tokens_token ON app_login_tokens(token)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_app_login_tokens_user_id ON app_login_tokens(user_id)"
            )
        logger.info("app_login_tokens table ready")
    except Exception as exc:
        logger.warning("app_login_tokens migration skipped: %s", exc)


async def _drop_invalid_index(conn, name: str) -> None:
    """Raeumt einen halb gebauten Index weg.

    Bricht `CREATE INDEX CONCURRENTLY` ab (Container neu gestartet, Update-
    Rueckfall, Platte voll), bleibt ein als ungueltig markierter Index liegen.
    `IF NOT EXISTS` ueberspringt ihn danach fuer immer - und beim Dedup-Index
    findet `ON CONFLICT (dedup_key)` keinen gueltigen Index mehr: dann scheitert
    JEDE Lieferung mit Zeitstempel. Also vorher pruefen und neu anfangen.
    """
    # `name` ist immer eine feste Konstante aus diesem Modul, nie eine Eingabe.
    invalid = (await conn.exec_driver_sql(
        "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        f"WHERE c.relname = '{name}' AND NOT i.indisvalid"
    )).first()
    if invalid:
        logging.getLogger("rimperio.startup").warning(
            "Index %s ist ungueltig (abgebrochener Aufbau) - wird neu gebaut", name)
        await conn.exec_driver_sql(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


@app.on_event("startup")
async def ensure_log_indexes():
    """Index fuer den Facility-Filter der Log-Ansicht (Migration fuer Bestands-DBs).

    CONCURRENTLY, damit der Aufbau auf einer grossen logs-Tabelle keine Schreib-
    zugriffe blockiert. Faellt der Aufbau aus, bleibt nur der Filter langsamer.
    """
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await _drop_invalid_index(conn, "idx_logs_facility")
            await conn.exec_driver_sql(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_facility ON logs(facility)"
            )
        logger.info("logs indexes ready")
    except Exception as exc:
        logger.warning("logs index migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_log_dedup_key():
    """Spalte + Unique-Index fuer die Duplikat-Erkennung beim HTTPS-Ingest.

    Der Index ist partiell (nur WHERE dedup_key IS NOT NULL), damit die bestehenden
    Millionen Syslog-Zeilen ohne Schluessel unberuehrt bleiben. CONCURRENTLY, damit
    der Aufbau keine Schreibzugriffe blockiert.
    """
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(
                "ALTER TABLE logs ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(64)"
            )
            await _drop_invalid_index(conn, "idx_logs_dedup_key")
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_dedup_key "
                "ON logs(dedup_key) WHERE dedup_key IS NOT NULL"
            )
        logger.info("logs dedup_key ready")
    except Exception as exc:
        logger.warning("logs dedup_key migration skipped: %s", exc)


@app.on_event("startup")
async def ensure_discovery_table():
    """Tabelle fuer die Netzwerksuche (app/discovery.py) - Migration fuer Bestands-DBs."""
    logger = logging.getLogger("rimperio.startup")
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS discovered_devices (
                    id SERIAL PRIMARY KEY,
                    ip_address VARCHAR(45) NOT NULL UNIQUE,
                    mac_address VARCHAR(17),
                    hostname VARCHAR(255),
                    kind VARCHAR(30) NOT NULL DEFAULT 'unknown',
                    os_guess VARCHAR(120),
                    open_ports JSONB DEFAULT '[]',
                    network VARCHAR(100),
                    same_subnet BOOLEAN,
                    ignored BOOLEAN NOT NULL DEFAULT FALSE,
                    note TEXT,
                    details JSONB DEFAULT '{}',
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_discovered_devices_mac ON discovered_devices(mac_address)")
        logger.info("discovered_devices ready")
    except Exception as exc:
        logger.warning("discovered_devices migration skipped: %s", exc)


@app.on_event("startup")
async def apply_saved_caddy_config():
    """Laedt eine gespeicherte Caddyfile (falls vorhanden) nach dem Start."""
    try:
        await caddy_router.ensure_caddy_config_on_startup()
    except Exception as exc:
        logging.getLogger("rimperio.startup").warning("Caddy-Config beim Start nicht geladen: %s", exc)


@app.on_event("startup")
async def apply_saved_dns_config():
    """Wendet eine gespeicherte DNS-Konfiguration (falls vorhanden) nach dem Start erneut an."""
    try:
        await network_router.ensure_dns_config_on_startup()
    except Exception as exc:
        logging.getLogger("rimperio.startup").warning("DNS-Config beim Start nicht geladen: %s", exc)


# =============================================================================
# Öffentlicher Webhook Endpoint
# =============================================================================
@app.get("/api/webhook/{webhook_id}/call", tags=["Webhooks"])
@limiter.limit("60/minute")
async def call_webhook(
    request: Request,
    webhook_id: int,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Öffentlicher Webhook-Endpoint für n8n/externe Tools. Auth via Token-Parameter.

    Mit Bremse: der Endpunkt ist ohne Anmeldung erreichbar und liefert Logdaten.
    60 Aufrufe pro Minute reichen für Abholdienste wie n8n und begrenzen zugleich,
    was ein Unbefugter mit geratenen Tokens anrichten kann.
    """
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.token == token, Webhook.is_active == True)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=401, detail="Ungültiger Webhook oder Token")

    filters = webhook.filters or {}
    query = select(Log)

    if filters.get("hostname"):
        query = query.where(Log.hostname.ilike(f"%{filters['hostname']}%"))
    if filters.get("source"):
        query = query.where(Log.source.ilike(f"%{filters['source']}%"))
    if filters.get("level"):
        query = query.where(Log.level.in_(filters["level"]))

    query = query.order_by(desc(Log.timestamp)).limit(webhook.max_results)
    result = await db.execute(query)
    logs = result.scalars().all()

    webhook.call_count += 1
    webhook.last_called_at = datetime.utcnow()
    await db.commit()

    if webhook.include_raw:
        return [LogDetailResponse(id=l.id, hostname=l.hostname, ip_address=l.ip_address, timestamp=l.timestamp,
                level=l.level, source=l.source, message=l.message, agent_id=l.agent_id, facility=l.facility,
                raw_message=l.raw_message, extra_data=l.extra_data or {}) for l in logs]
    return [LogResponse(id=l.id, hostname=l.hostname, ip_address=l.ip_address, timestamp=l.timestamp,
            level=l.level, source=l.source, message=l.message) for l in logs]


# =============================================================================
# Installer vom Server ausliefern
# =============================================================================
@app.get("/api/agents/installer/linux", tags=["Agent Ingest"])
@limiter.limit("30/minute")
async def serve_linux_installer(request: Request):
    """Liefert agent/logship/install-linux.sh aus - fuer Systeme, die GitHub nicht erreichen.

    So funktioniert der Einzeiler auch ohne Internet: erst GitHub probieren, sonst
    diesen Server. Gelesen wird die Datei vom Host (der Backend-Container hat die
    agents/ nicht im Image). Oeffentlich wie das Rohskript auf GitHub - sie
    enthaelt keine Geheimnisse, der Schluessel kommt erst beim Aufruf dazu.
    """
    from . import hostexec

    paths = hostexec.host_paths()
    content = await hostexec.read_host_file(f"{paths['install_dir']}/agent/logship/install-linux.sh")
    if not content or "RIMPERIO Linux Agent" not in content:
        raise HTTPException(status_code=404, detail="Installer auf diesem Server nicht verfügbar.")
    return Response(content=content, media_type="text/x-shellscript")


# =============================================================================
# Agent Ingest Endpoint
# =============================================================================
def _to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Zeitstempel mit Zeitzone auf UTC ohne tzinfo bringen (die DB speichert naiv/UTC)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# So viele Zeilen gehen hoechstens in EINE INSERT-Anweisung. asyncpg erlaubt
# pro Anweisung maximal 32767 Parameter, eine Logzeile braucht 12. Ohne
# Stueckelung scheiterte jede Lieferung ab etwa 2700 Eintraegen (erlaubt sind
# 5000) mit einem 500 - und ein Sammler, der bei Fehler wiederholt, schickte
# dieselbe Lieferung endlos erneut.
INGEST_INSERT_CHUNK = 1000

# Abgewiesene Lieferungen: hoechstens ein Tagebucheintrag je Absender/Geraet/
# Grund alle 10 Minuten. Ein Agent mit zurueckgezogenem Schluessel versucht es
# alle paar Sekunden - frueher war das jedes Mal ein Schreibzugriff plus eine
# Meldung in jedes offene Fenster.
REJECTION_LOG_EVERY = 600
_rejections: dict = {}


def _rejection_due(key: tuple) -> Optional[int]:
    """None = noch in der Sperrfrist (zaehlt nur mit). Sonst: wie viele seit
    dem letzten Eintrag unterdrueckt wurden."""
    now = datetime.utcnow()
    last, count = _rejections.get(key, (None, 0))
    if last is not None and (now - last).total_seconds() < REJECTION_LOG_EVERY:
        _rejections[key] = (last, count + 1)
        return None
    if len(_rejections) > 10_000:
        # Obergrenze: wechselnde Absender sollen den Speicher nicht fuellen.
        _rejections.clear()
    _rejections[key] = (now, 0)
    return count


# Gleiche Meldungen innerhalb dieses Zeitfensters (Sekunden) werden zu einer
# Zeile mit Anzahl zusammengefasst. 0 = aus.
REPEAT_WINDOW = max(0, int(os.getenv("LOG_REPEAT_WINDOW", "60")))

# Breite der Spalte logs.source. Laengere Angaben werden gekuerzt statt die
# ganze Lieferung abzulehnen (siehe LogIngestEntry.source).
SOURCE_MAX_LENGTH = 100


def _strip_nul(value: Optional[str]) -> Optional[str]:
    """NUL-Zeichen entfernen.

    PostgreSQL nimmt in Text- und JSONB-Spalten kein NUL an. Ein einziges
    solches Zeichen (kommt in Windows-Ereignistexten und binaeren
    journald-Meldungen vor) liess sonst die ganze Lieferung mit 500 scheitern,
    und der Agent schickte sie immer wieder.
    """
    if value is None:
        return None
    return value.replace("\x00", "")


def _dedup_key(hostname: str, timestamp: datetime, event_id: Optional[int], message: str) -> str:
    """Fingerabdruck eines Ereignisses. Gleiche Box + Zeit + Ereignis + Text = gleicher Eintrag."""
    raw = f"{hostname}|{timestamp.isoformat()}|{'' if event_id is None else event_id}|{message}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _agent_type_fallback(token_type: Optional[str], user_agent: Optional[str]) -> str:
    """Geraeteart, wenn der Agent selbst keine mitschickt (Agents vor 2026.09.15).

    Reihenfolge: Typ des Tokens, dann der User-Agent (der Linux-Agent sendet per
    Python-urllib, der Windows-Agent per PowerShell), sonst "unknown". Frueher
    stand hier stur "windows_agent" - ein Linux-Agent mit dem global-agent-Token
    tauchte dadurch als Windows-Agent auf.
    """
    mapped = {"linux": "linux_agent", "windows": "windows_agent"}.get(token_type or "")
    if mapped:
        return mapped
    ua = (user_agent or "").lower()
    if "powershell" in ua:
        return "windows_agent"
    if "python-urllib" in ua:
        return "linux_agent"
    return "unknown"


@app.post("/api/agents/ingest", response_model=LogIngestResponse, tags=["Agent Ingest"])
@limiter.limit("600/minute", key_func=agent_key)
async def ingest_logs(
    data: LogIngestRequest,
    request: Request,
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    """Empfängt Logs von authentifizierten Agents via HTTPS.

    Sammler (z.B. n8n, das die FRITZ!Box abfragt) duerfen hostname, ip_address und
    device_type des gemeldeten Geraets mitschicken - dann taucht die Box mit eigener
    IP und eigenem Hostnamen auf und nicht mit der IP des Sammlers.

    Eintraege mit eigenem Zeitstempel bekommen einen dedup_key: wiederholte Lieferungen
    derselben Ereignisse (die FRITZ!Box liefert immer ihren kompletten Puffer) werden
    von der Datenbank verworfen statt doppelt gespeichert.
    """
    # Platte fast voll: nicht annehmen statt die Datenbank zu ersticken. Der
    # Linux-Agent wartet bei 503 und schickt spaeter genau dieses Paket noch
    # einmal - es geht also nichts verloren, es kommt nur spaeter an.
    if diskguard.ingest_blocked():
        raise HTTPException(
            status_code=503,
            detail="Platte fast voll - Annahme pausiert, bitte später erneut senden.",
            headers={"Retry-After": "300"})

    source_ip = real_client_ip(request)
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer Token erforderlich")

    agent_token, deny_reason = await tokens.resolve(db, authorization[7:], source_ip)
    if not agent_token:
        # Nach aussen immer dieselbe Antwort - von dort soll nicht erkennbar
        # sein, ob ein Schluessel unbekannt, abgelaufen oder nur aus dem
        # falschen Netz vorgelegt wurde. Der Grund steht im Systemtagebuch.
        suppressed = _rejection_due(("rejected", source_ip, data.hostname, deny_reason))
        if suppressed is not None:
            await journal.record(
                db, category="token", event="ingest.rejected", level="warning",
                message=(f"Lieferung von '{data.hostname}' abgewiesen: {deny_reason}"
                         + (f" (dazu {suppressed} weitere seit dem letzten Eintrag)" if suppressed else "")),
                actor="agent", source_ip=source_ip, target=data.hostname, ok=False)
        raise HTTPException(status_code=401, detail="Ungültiger Agent-Token")

    device_ip = (data.ip_address or "").strip() or source_ip
    proxy_ip = request.client.host if request.client else None
    device_type = data.device_type or _agent_type_fallback(
        agent_token.device_type, request.headers.get("user-agent"))

    result = await db.execute(
        select(Agent).where(Agent.hostname == data.hostname, Agent.ip_address == device_ip)
    )
    agent = result.scalars().first()

    if agent is None and proxy_ip and proxy_ip != device_ip:
        # Altbestand: bis 2026.09.15 wurde bei HTTPS-Agents die IP von Caddy
        # gespeichert statt der Geraete-IP. Solche Eintraege uebernehmen (IP und
        # Typ korrigieren), statt eine zweite Karte fuer dasselbe Geraet anzulegen.
        result = await db.execute(
            select(Agent).where(Agent.hostname == data.hostname, Agent.ip_address == proxy_ip)
        )
        agent = result.scalars().first()
        if agent:
            agent.ip_address = device_ip
            if device_type != "unknown":
                agent.device_type = device_type

    if agent:
        agent.last_seen = datetime.utcnow()
        if data.device_type:
            agent.device_type = data.device_type
        elif agent.device_type in (None, "unknown"):
            agent.device_type = device_type
    else:
        agent = Agent(
            hostname=data.hostname, ip_address=device_ip, device_type=device_type,
            extra_data={"auth": "token", "token_name": agent_token.name})
        db.add(agent)
        await db.flush()

    # Darf dieser Schluessel fuer dieses Geraet liefern? Der Generalschluessel
    # darf fuer alle (das braucht z.B. ein Sammler, der die FRITZ!Box abfragt).
    # Ein Geraeteschluessel darf nur fuer sein eigenes - sonst koennte ein
    # uebernommener Arbeitsplatzrechner Logzeilen im Namen des
    # Domaenencontrollers erfinden.
    allowed, why = tokens.may_ingest(agent_token, data.hostname, agent.id)
    if not allowed:
        # Den Namen VOR dem Rollback merken: rollback() laesst alle geladenen
        # Objekte verfallen, und das Nachladen von agent_token.name wirft in
        # der async-Session (MissingGreenlet). Aus dem 403 wurde so ein 500,
        # und der Tagebucheintrag fehlte.
        token_name = agent_token.name
        await db.rollback()
        suppressed = _rejection_due(("denied", token_name, data.hostname, why))
        if suppressed is not None:
            await journal.record(
                db, category="token", event="ingest.denied", level="warning",
                message=(f"'{token_name}' wollte für '{data.hostname}' liefern: {why}"
                         + (f" (dazu {suppressed} weitere seit dem letzten Eintrag)" if suppressed else "")),
                actor=token_name, source_ip=source_ip, target=data.hostname, ok=False)
        raise HTTPException(status_code=403, detail=why)

    # Erste Lieferung eines frisch angemeldeten Geraeteschluessels: ab jetzt
    # gehoert er zu diesem Geraet und zu keinem anderen mehr.
    tokens.bind_to_agent(agent_token, agent.id)
    # Sparsam: hoechstens einmal pro Minute. Bei jeder einzelnen Lieferung eine
    # Zeile in agent_tokens zu schreiben, waere eine Schreiboperation, die
    # niemandem nuetzt - fuer "wird dieser Schluessel noch benutzt?" reicht
    # Minutengenauigkeit.
    tokens.touch(agent_token, source_ip)

    is_fritzbox = (data.device_type or "").lower() == "fritzbox"
    now = datetime.utcnow()
    unique_rows = {}   # dedup_key -> Zeile, entfernt Doppelte schon innerhalb einer Lieferung
    plain_rows = []    # ohne Zeitstempel: keine Duplikatpruefung moeglich
    repeats = {}       # (Stufe, Quelle, Text) -> erste Zeile dieser Wiederholung

    for event in data.events:
        message = _strip_nul(event.message)
        level = _strip_nul(event.level or "").strip().lower() or None
        source = _strip_nul(event.source or "").strip()[:SOURCE_MAX_LENGTH] or None
        if is_fritzbox and (level is None or source is None):
            mapped_level, mapped_source = fritzbox.classify(event.event_id, event.group, event.message)
            level = level or mapped_level
            source = source or mapped_source

        # Wiederholungen zusammenfassen statt verwerfen: dieselbe Meldung
        # mehrfach innerhalb von REPEAT_WINDOW wird EINE Zeile mit Anzahl und
        # letztem Zeitpunkt ("5.000x zwischen 19:27 und 19:28"). Die
        # Information bleibt vollstaendig, der Platz nicht. Die FRITZ!Box ist
        # ausgenommen: sie liefert ihren Puffer immer wieder komplett, und
        # dort muss jede Zeile ihren eigenen Duplikat-Schluessel behalten.
        ts = _to_naive_utc(event.timestamp)
        collapse = REPEAT_WINDOW > 0 and not is_fritzbox
        if collapse:
            group_key = (level, source, message)
            first = repeats.get(group_key)
            moment = ts or now
            if first is not None and abs((moment - first["timestamp"]).total_seconds()) <= REPEAT_WINDOW:
                extra_first = first["extra_data"]
                extra_first["repeat"] = extra_first.get("repeat", 1) + 1
                extra_first["repeat_last"] = moment.isoformat()
                continue

        extra = {"ingested_via": "https"}
        if event.event_id is not None:
            extra["event_id"] = event.event_id
        if event.group:
            extra["group"] = _strip_nul(event.group)

        row = {
            "agent_id": agent.id,
            "hostname": data.hostname,
            "ip_address": device_ip,
            "timestamp": ts or now,
            "facility": 1 if event.facility is None else event.facility,
            "level": level or "info",
            "source": source or "unknown",
            "message": message,
            "raw_message": message,
            "extra_data": extra,
            "created_at": now,
            "dedup_key": None,
        }
        if ts is None:
            plain_rows.append(row)
        else:
            key = _dedup_key(data.hostname, ts, event.event_id, message)
            row["dedup_key"] = key
            unique_rows[key] = row
        if collapse:
            repeats[(level, source, message)] = row

    rows = plain_rows + list(unique_rows.values())
    inserted = 0
    for start in range(0, len(rows), INGEST_INSERT_CHUNK):
        stmt = (
            pg_insert(Log).values(rows[start:start + INGEST_INSERT_CHUNK])
            .on_conflict_do_nothing(
                index_elements=[Log.dedup_key],
                index_where=Log.dedup_key.is_not(None),
            )
            .returning(Log.id)
        )
        inserted += len((await db.execute(stmt)).scalars().all())

    await db.commit()
    # agent_id mitgeben: damit weiss der Agent, welcher Eintrag auf dem Server
    # ihm gehoert, und kann sich beim Deinstallieren gezielt abmelden.
    return LogIngestResponse(accepted=inserted, duplicates=len(data.events) - inserted,
                             agent_id=agent.id)


@app.get("/api")
async def root():
    return {"name": "RIMPERIO", "version": settings.app_version, "docs": "/api/docs"}


# =============================================================================
# Plattenwaechter
# =============================================================================
# Die Logik dazu steht in app/diskguard.py. Hier bleibt nur der Anschluss -
# das Modul laesst sich so einzeln lesen und einzeln pruefen.
#
# Was sich gegenueber frueher geaendert hat, in einem Satz: es wird aufgeraeumt,
# BEVOR es eng wird, in Haeppchen statt in einem Rutsch, und ein "alle Logs
# weg" gibt es nicht mehr von selbst. Begruendung ausfuehrlich im Modul.


# =============================================================================
# Per-Agent Retention Background-Task (stündlich)
# =============================================================================
AGENT_RETENTION_INTERVAL = int(os.getenv("AGENT_RETENTION_INTERVAL", "3600"))


async def _enforce_agent_retention(session, agent, logger: logging.Logger) -> int:
    """Retention-Policy für einen Agent durchsetzen. Gibt Anzahl gelöschter Logs zurück."""
    deleted = 0
    # Haeppchenweise, jedes einzeln festgeschrieben - wie im Plattenwaechter.
    # Frueher ein einziges DELETE: bei Millionen Zeilen eine stundenlang
    # offene Transaktion, die das Transaktionslog aufblaeht und die Platte
    # waehrend des Aufraeumens erst einmal VOLLER macht.
    chunk = diskguard.BATCH_SIZE

    if agent.retention_days:
        cutoff = datetime.utcnow() - timedelta(days=agent.retention_days)
        n = 0
        for _ in range(diskguard.MAX_BATCHES):
            oldest = (
                select(Log.id)
                .where(Log.agent_id == agent.id, Log.timestamp < cutoff)
                .limit(chunk)
                .scalar_subquery()
            )
            r = await session.execute(delete(Log).where(Log.id.in_(oldest)))
            await session.commit()
            step = r.rowcount or 0
            n += step
            if step < chunk:
                break
        deleted += n
        if n:
            logger.info("Agent %s (%s): %s Logs älter als %s Tage gelöscht",
                        agent.id, agent.hostname, n, agent.retention_days)

    if agent.retention_max_logs:
        count = (await session.execute(
            select(func.count(Log.id)).where(Log.agent_id == agent.id)
        )).scalar() or 0
        excess = count - agent.retention_max_logs
        n = 0
        while excess > 0 and n < chunk * diskguard.MAX_BATCHES:
            subq = (
                select(Log.id)
                .where(Log.agent_id == agent.id)
                .order_by(Log.timestamp.asc())
                .limit(min(excess, chunk))
                .scalar_subquery()
            )
            r = await session.execute(delete(Log).where(Log.id.in_(subq)))
            await session.commit()
            step = r.rowcount or 0
            if not step:
                break
            n += step
            excess -= step
        deleted += n
        if n:
            logger.info("Agent %s (%s): %s älteste Logs gelöscht (Limit: %s)",
                        agent.id, agent.hostname, n, agent.retention_max_logs)

    return deleted


async def agent_retention_task():
    """Stündliche Überprüfung und Durchsetzung der per-Agent-Retention-Policies."""
    logger = logging.getLogger("rimperio.agent_retention")
    while True:
        await asyncio.sleep(AGENT_RETENTION_INTERVAL)
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Agent).where(
                        (Agent.retention_max_logs != None) |  # noqa: E711
                        (Agent.retention_days != None)         # noqa: E711
                    )
                )
                agents = result.scalars().all()
                if not agents:
                    continue
                total_deleted = 0
                for agent in agents:
                    total_deleted += await _enforce_agent_retention(session, agent, logger)
                if total_deleted:
                    await session.commit()
                    logger.info("Agent-Retention: insgesamt %s Logs gelöscht", total_deleted)
                    await journal.record(
                        session, category="retention", event="agent.retention.auto",
                        level="notice",
                        message=(f"Geräte-Aufbewahrung: {total_deleted} Logzeilen von "
                                 f"{len(agents)} Gerät(en) entfernt."),
                        actor="system",
                        detail={"deleted": total_deleted, "agents": len(agents)})
        except Exception as exc:
            logger.error("Agent-Retention-Task Fehler: %s", exc)


async def archiving_task():
    """Stündlicher Blick auf die Uhr: läuft die Archivierung zur eingestellten Stunde.

    Bewusst kein Cron: der Container soll ohne Zusatzdienst auskommen. Damit ein
    Lauf nicht mehrfach startet (der Task wacht jede Stunde auf), merkt sich die
    Funktion den zuletzt archivierten Tag.
    """
    logger = logging.getLogger("rimperio.archiving")
    last_run_day = None

    while True:
        await asyncio.sleep(600)  # alle 10 Minuten prüfen, ob die Stunde erreicht ist
        try:
            async with async_session() as session:
                config = await archiving.load_config(session)
                hour = int(config.get("schedule_hour", -1))
                if not config.get("enabled") or hour < 0:
                    continue

                now = datetime.utcnow()
                if now.hour != hour or last_run_day == now.date():
                    continue

                last_run_day = now.date()
                logger.info("Archivierung startet (Zeitplan %s Uhr)", hour)
                result = await archiving.run_archiving(session, config, triggered_by="Zeitplan")
                logger.info("Archivierung beendet: %s", result.get("message"))
        except Exception as exc:
            logger.error("Archivierungs-Task Fehler: %s", exc)


async def housekeeping_task():
    """Taeglich aufraeumen, was sonst still mitwaechst.

    Bisher gab es das nur unter Druck - erst wenn die Platte voll lief. Das ist
    die falsche Reihenfolge: Hausarbeit macht man, wenn Zeit dafuer ist.
    """
    logger = logging.getLogger("rimperio.housekeeping")
    # Erster Lauf kurz nach dem Start, nicht erst nach 24 h: ein Server, der
    # oefter als einmal am Tag neu startet (Updates, Absturz), kam sonst nie
    # dazu - das Tagebuch wuchs dann ohne Grenze.
    await asyncio.sleep(600)
    while True:
        try:
            async with async_session() as session:
                removed = await journal.prune(session)
                if removed:
                    logger.info("Systemtagebuch: %s alte Einträge entfernt", removed)
        except Exception as exc:
            logger.warning("Hausarbeit fehlgeschlagen: %s", exc)
        await asyncio.sleep(24 * 3600)


# Hintergrundaufgaben werden hier festgehalten. Ohne eine Referenz kann der
# Garbage Collector eine laufende Aufgabe einsammeln - ein Fehler, der sich
# erst Wochen spaeter als "der Waechter laeuft nicht mehr" zeigt.
_background_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.on_event("startup")
async def start_background_tasks():
    # Raeumt auf, BEVOR die Platte voll ist (app/diskguard.py).
    _spawn(diskguard.watch_task())
    # Merkt, wenn RIMPERIO selbst nicht mehr sammelt, und meldet es nach aussen
    # (app/watchdog.py + app/alerts.py).
    _spawn(watchdog.watch_task())
    _spawn(agent_retention_task())
    _spawn(archiving_task())
    _spawn(housekeeping_task())
    # Haelt Ausschau nach einem neuen Stand auf GitHub und meldet ihn sofort an
    # alle offenen Oberflaechen (siehe app/updater.py -> watch_task).
    _spawn(updater.watch_task())
    # Sucht das Netz nach Systemen ab: kurz nach dem ersten Start, danach im
    # eingestellten Takt (app/discovery.py, abschaltbar in der Oberflaeche).
    _spawn(discovery.watch_task())

    await journal.write(
        category="system", event="server.started",
        message=f"RIMPERIO {settings.app_version} ist gestartet.",
        actor="system",
        detail={"version": settings.app_version,
                "webshell": shell.ENABLED,
                "disk_check_seconds": diskguard.CHECK_INTERVAL})


@app.on_event("shutdown")
async def close_open_shells():
    """Offene Terminal-Sitzungen beenden, damit keine Root-Shell verwaist zurueckbleibt."""
    closed = shell.shutdown_all()
    if closed:
        logging.getLogger("rimperio.shutdown").warning(
            "%s offene Terminal-Sitzung(en) beim Herunterfahren beendet", closed)
    try:
        await journal.write(category="system", event="server.stopped",
                            message="RIMPERIO fährt herunter.", actor="system",
                            detail={"closed_shells": closed})
    except Exception:                                               # defensiv
        pass
