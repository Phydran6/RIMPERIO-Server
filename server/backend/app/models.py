# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.31.23.30.00
# Beschreibung: RIMPERIO - SQLAlchemy Models
# ==============================================================================

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100))
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user")
    is_active = Column(Boolean, default=True)
    # Woher das Konto kommt: "local" (Passwort in dieser Datenbank) oder "ldap"
    # (Anmeldung gegen das Verzeichnis, lokaler Hash ist unbrauchbar).
    auth_source = Column(String(20), default="local", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # MFA / TOTP
    mfa_enabled = Column(Boolean, default=False, nullable=False)
    mfa_secret = Column(String(64), nullable=True)                # Base32, erst nach erfolgreichem Verify gesetzt
    mfa_failed_count = Column(Integer, default=0, nullable=False)
    mfa_locked_until = Column(DateTime, nullable=True)
    app_login_tokens = relationship("AppLoginToken", back_populates="user", passive_deletes=True)
    mfa_backup_codes = relationship("MFABackupCode", back_populates="user", passive_deletes=True)
    webauthn_credentials = relationship("WebAuthnCredential", back_populates="user", passive_deletes=True)


class WebAuthnCredential(Base):
    """Ein registrierter Passkey (Sicherheitsschlüssel, Windows Hello, Face ID …).

    credential_id und public_key sind base64url-Text: so lassen sie sich ohne
    Sonderbehandlung speichern und mit dem vergleichen, was der Browser schickt.
    sign_count wächst bei jeder Nutzung und entlarvt geklonte Schlüssel.
    """
    __tablename__ = "webauthn_credentials"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    credential_id = Column(String(512), unique=True, nullable=False)
    public_key = Column(Text, nullable=False)
    sign_count = Column(Integer, default=0, nullable=False)
    name = Column(String(100))
    transports = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    user = relationship("User", back_populates="webauthn_credentials")


class MFABackupCode(Base):
    """Einmal-Backup-Code für TOTP-Recovery. code_hash = bcrypt."""
    __tablename__ = "mfa_backup_codes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code_hash = Column(String(255), nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="mfa_backup_codes")

class Agent(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45))
    mac_address = Column(String(17))
    device_type = Column(String(50), default="unknown")
    last_seen = Column(DateTime, default=datetime.utcnow)
    first_seen = Column(DateTime, default=datetime.utcnow)
    extra_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Retention-Policy pro Gerät (NULL = kein Limit)
    retention_max_logs = Column(Integer, nullable=True)
    retention_days = Column(Integer, nullable=True)
    logs = relationship("Log", back_populates="agent", passive_deletes=True)

    # --- RMM: nur gefüllt, wenn auf dem Gerät der RIMPERIO-Agent läuft ---------
    # Ein Gerät ist eine Zeile — egal ob es Logs schickt, den RMM-Agent trägt
    # oder beides. Die Spalten kommen per ALTER TABLE dazu (siehe rmm.py).
    agent_uid = Column(String(64), unique=True, nullable=True)      # öffentliche RMM-Kennung
    rmm_token_hash = Column(String(64), nullable=True)              # SHA-256 des Agent-Tokens
    site = Column(String(128), nullable=True)
    description = Column(String(255), nullable=True)
    os = Column(String(128), nullable=True)
    platform = Column(String(32), nullable=True)                    # windows | linux | darwin
    arch = Column(String(32), nullable=True)
    agent_version = Column(String(64), nullable=True)
    public_ip = Column(String(64), nullable=True)
    enrolled_at = Column(DateTime, nullable=True)
    rmm_last_seen = Column(DateTime, nullable=True)                 # letzter Check-in
    boot_time = Column(DateTime, nullable=True)
    last_cpu = Column(Float, nullable=True)
    last_mem = Column(Float, nullable=True)
    last_disk = Column(Float, nullable=True)
    inventory = Column(JSON, nullable=True)

class Log(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"))
    hostname = Column(String(255))
    ip_address = Column(String(45))
    timestamp = Column(DateTime, default=datetime.utcnow)
    facility = Column(Integer)
    level = Column(String(20))
    source = Column(String(100))
    message = Column(Text)
    raw_message = Column(Text)
    extra_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Duplikat-Erkennung beim HTTPS-Ingest (SHA256 aus Host/Zeit/Ereignis/Text).
    # NULL = keine Pruefung (z.B. Syslog), Unique-Index greift nur auf NOT NULL.
    dedup_key = Column(String(64), nullable=True)
    agent = relationship("Agent", back_populates="logs")

class Webhook(Base):
    __tablename__ = "webhooks"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    description = Column(Text)
    filters = Column(JSON, default=dict)
    max_results = Column(Integer, default=100)
    include_raw = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    call_count = Column(Integer, default=0)
    last_called_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AgentToken(Base):
    """Ein Zugangsschluessel fuer die Maschinen-Schnittstelle.

    Drei Arten, und die Art entscheidet, was der Schluessel darf (siehe
    app/tokens.py):

    * ``global``  - der Generalschluessel des Administrators. Darf alles:
                    anmelden, liefern, abmelden. Genau einer davon, und er
                    liegt nur als Pruefsumme in der Datenbank.
    * ``agent``   - gehoert genau einem Geraet. Darf nur fuer dieses Geraet
                    liefern und sich selbst abmelden. Wird beim Anmelden
                    erzeugt und beim Deinstallieren wieder entwertet.
    * ``enroll``  - eine Einladung: kurzlebig, zaehlbar, darf ausschliesslich
                    einen Geraete-Schluessel anfordern. Damit muss der
                    Generalschluessel nicht mehr auf jeden Rechner kopiert
                    werden.

    ``token`` (Klartext) gibt es nur noch fuer Schluessel aus der Zeit vor
    dieser Fassung. Neue Schluessel werden *nie* im Klartext gespeichert -
    sie werden einmal angezeigt und danach nur noch als ``token_hash``
    (SHA-256) wiedererkannt.
    """
    __tablename__ = "agent_tokens"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    # Altbestand: Klartext. Neue Schluessel lassen die Spalte leer.
    token = Column(String(64), unique=True, nullable=True)
    token_hash = Column(String(64), unique=True, nullable=True)
    # Die ersten Zeichen, damit man einen Schluessel in der Liste wiedererkennt.
    prefix = Column(String(24))
    kind = Column(String(20), default="agent", nullable=False)
    device_type = Column(String(50))
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
    # Nur fuer 'enroll': wie oft die Einladung noch gilt.
    max_uses = Column(Integer, nullable=True)
    use_count = Column(Integer, default=0, nullable=False)
    # Absenderbeschraenkung, z.B. "10.0.0.0/8,192.168.1.5/32". Leer = ueberall.
    allowed_cidrs = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(45), nullable=True)
    created_by = Column(String(50), nullable=True)
    note = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SystemEvent(Base):
    """Was RIMPERIO selbst getan hat - das Systemtagebuch.

    Der Grundsatz dahinter: es darf auf diesem Server nichts passieren, das
    hinterher niemand mehr nachvollziehen kann. Jeder Eingriff (Update,
    Sicherung, Aufraeumen, Terminal, Anmeldung, Container) schreibt hier eine
    Zeile - getrennt von den Logs der ueberwachten Geraete, damit ein
    Aufraeumlauf die eigene Spur nicht mitloescht.
    """
    __tablename__ = "system_events"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # 'update', 'backup', 'retention', 'auth', 'shell', 'container', 'settings' ...
    category = Column(String(40), nullable=False)
    # 'debug' | 'info' | 'notice' | 'warning' | 'error' | 'critical'
    level = Column(String(20), default="info", nullable=False)
    # Kurzer Maschinenname des Vorgangs, z.B. 'update.apply.started'
    event = Column(String(80), nullable=False)
    # Ein Satz in Klartext - das, was in der Oberflaeche steht.
    message = Column(Text, nullable=False)
    actor = Column(String(100))              # wer: Benutzername, 'system', Token-Name
    source_ip = Column(String(45))
    target = Column(String(200))             # worauf: Container, Sicherung, Geraet ...
    ok = Column(Boolean, default=True, nullable=False)
    duration_ms = Column(Integer, nullable=True)
    detail = Column(JSON, default=dict)      # alles Weitere, maschinenlesbar

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(JSON, nullable=False)
    description = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppLoginToken(Base):
    """Kurzlebiger Einmal-Token für die App-Authentifizierung via QR-Code."""
    __tablename__ = "app_login_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="app_login_tokens")


class DiscoveredDevice(Base):
    """Ein System, das die Netzwerksuche gefunden hat (app/discovery.py).

    Bewusst getrennt von `Agent`: ein Agent *liefert* Logs, ein gefundenes
    System *existiert* nur. Ob beides dasselbe Geraet ist, wird beim Lesen
    ueber MAC, IP und Hostname entschieden.
    """
    __tablename__ = "discovered_devices"
    id = Column(Integer, primary_key=True)
    ip_address = Column(String(45), unique=True, nullable=False)
    mac_address = Column(String(17))
    hostname = Column(String(255))
    kind = Column(String(30), default="unknown", nullable=False)   # linux, windows, network, ...
    os_guess = Column(String(120))
    open_ports = Column(JSON, default=list)
    network = Column(String(100))           # in welchem Bereich gefunden
    same_subnet = Column(Boolean)           # im selben Subnetz wie der Server
    ignored = Column(Boolean, default=False, nullable=False)
    note = Column(Text)
    details = Column(JSON, default=dict)    # SSH-Kennung, SNMP, wie gefunden
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)


# ==============================================================================
# RMM — Fernwartung über den RIMPERIO-Agent
# ==============================================================================
# Alle Tabellen hängen an agents.id. Angelegt werden sie beim Start (rmm.py),
# damit auch bestehende Installationen sie ohne Handarbeit bekommen.

class Telemetry(Base):
    """Messwerte je Check-in (CPU, RAM, Platten, Top-Prozesse)."""
    __tablename__ = "telemetry"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=False)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    cpu_percent = Column(Float, default=0.0)
    mem_percent = Column(Float, default=0.0)
    mem_used_mb = Column(Float, default=0.0)
    mem_total_mb = Column(Float, default=0.0)
    uptime_seconds = Column(Integer, default=0)
    disk = Column(JSON, nullable=True)
    top_procs = Column(JSON, nullable=True)


class Command(Base):
    """Ein Auftrag an den Agent: Shell-Befehl oder eingebaute Aktion
    (patch_scan | patch_apply | software_inventory).
    pending → der Agent holt ihn ab → running → done | error | timeout"""
    __tablename__ = "commands"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=False)
    shell = Column(String(16), default="powershell")
    command = Column(Text, nullable=False)
    timeout = Column(Integer, default=300)
    kind = Column(String(24), default="shell")
    source = Column(String(128), default="")          # z. B. Skriptname
    status = Column(String(16), default="pending", index=True)
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    picked_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)


class RmmAlert(Base):
    """Ein Zustand, der Aufmerksamkeit braucht (offline, Schwelle, Check).
    Bleibt aktiv, bis er sich auflöst oder jemand ihn quittiert."""
    __tablename__ = "rmm_alerts"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=True)
    kind = Column(String(32), nullable=False)          # offline | cpu | mem | disk | check:<id>
    severity = Column(String(16), default="warning")   # info | warning | critical
    message = Column(String(512), nullable=False)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class Script(Base):
    """Wiederverwendbares Skript. Ein Lauf erzeugt daraus einen Command."""
    __tablename__ = "scripts"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(String(512), default="")
    shell = Column(String(16), default="powershell")
    body = Column(Text, nullable=False)
    default_timeout = Column(Integer, default=300)
    platform = Column(String(16), default="any")      # windows | linux | any
    category = Column(String(64), default="Allgemein")
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Check(Base):
    """Wiederkehrende Prüfung, die der Agent selbst ausführt.
    Gilt global, je Standort oder für ein einzelnes Gerät."""
    __tablename__ = "checks"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(24), nullable=False)          # cpu | mem | disk | service | port | ping | script
    params = Column(JSON, default=dict)
    interval_sec = Column(Integer, default=300)
    scope_type = Column(String(16), default="global")  # global | site | agent
    scope_value = Column(String(128), default="")
    severity = Column(String(16), default="warning")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CheckResult(Base):
    """Letztes Ergebnis einer Prüfung je Gerät (wird überschrieben, nicht angehäuft)."""
    __tablename__ = "check_results"
    __table_args__ = (UniqueConstraint("check_id", "agent_id", name="uq_check_agent"),)
    id = Column(Integer, primary_key=True)
    check_id = Column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), index=True, nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=False)
    status = Column(String(16), default="unknown")     # pass | fail | unknown
    value = Column(String(255), default="")
    output = Column(Text, nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)


class PatchReport(Base):
    """Letzter Patch-Stand eines Geräts."""
    __tablename__ = "patch_reports"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    updates = Column(JSON, default=list)               # [{id, title, severity, kb, size}]
    count = Column(Integer, default=0)
    scanned_at = Column(DateTime, nullable=True)
    last_apply_at = Column(DateTime, nullable=True)
    last_apply_output = Column(Text, nullable=True)


RMM_TABLES = [Telemetry.__table__, Command.__table__, RmmAlert.__table__, Script.__table__,
              Check.__table__, CheckResult.__table__, PatchReport.__table__]
