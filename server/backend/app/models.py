# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.31.23.30.00
# Beschreibung: RIMPERIO - SQLAlchemy Models
# ==============================================================================

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON
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
