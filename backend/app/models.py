"""
Datei:        models.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Datenmodelle. Benutzer, Agents, Telemetrie (Zeitreihe),
              Befehle an Agents, Alarme und ein Schlüssel/Wert-Speicher für
              Laufzeiteinstellungen. Alles, was RIMPERIO über die verwalteten
              Rechner und über sich selbst weiß.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Rollen: admin (alles), operator (Agents steuern), readonly (nur sehen)
    role: Mapped[str] = mapped_column(String(16), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Öffentliche, dauerhafte Kennung des Agents (im URL/der Oberfläche sichtbar).
    agent_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # SHA-256 des Agent-Tokens. Das Klartext-Token sieht der Server nur einmal.
    token_hash: Mapped[str] = mapped_column(String(64))

    hostname: Mapped[str] = mapped_column(String(255), index=True)
    site: Mapped[str] = mapped_column(String(128), default="Standard")
    description: Mapped[str] = mapped_column(String(255), default="")

    os: Mapped[str] = mapped_column(String(128), default="")
    platform: Mapped[str] = mapped_column(String(32), default="")  # windows|linux|darwin
    arch: Mapped[str] = mapped_column(String(32), default="")
    version: Mapped[str] = mapped_column(String(64), default="")

    public_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    local_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Letzte Momentaufnahme — für Listen und Dashboard ohne Zeitreihen-Abfrage.
    last_cpu: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_mem: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_disk: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Vollständige letzte Inventur (OS-Details, Hardware, Software, Dienste).
    inventory: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    telemetry: Mapped[list["Telemetry"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    commands: Mapped[list["Command"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    mem_percent: Mapped[float] = mapped_column(Float, default=0.0)
    mem_used_mb: Mapped[float] = mapped_column(Float, default=0.0)
    mem_total_mb: Mapped[float] = mapped_column(Float, default=0.0)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0)
    disk: Mapped[list | None] = mapped_column(JSON, nullable=True)
    top_procs: Mapped[list | None] = mapped_column(JSON, nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="telemetry")


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)

    shell: Mapped[str] = mapped_column(String(16), default="powershell")  # powershell|cmd|bash|sh
    command: Mapped[str] = mapped_column(Text)
    timeout: Mapped[int] = mapped_column(Integer, default=300)
    # shell → normaler Befehl; sonst eine eingebaute Aktion des Agents:
    # patch_scan | patch_apply | software_inventory
    kind: Mapped[str] = mapped_column(String(24), default="shell")
    # Herkunft, falls aus der Skript-Bibliothek gestartet (nur Anzeige).
    source: Mapped[str] = mapped_column(String(128), default="")

    # pending → der Agent holt ihn ab → running → done/error/timeout
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="commands")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # offline|cpu|mem|disk|custom
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # info|warning|critical
    message: Mapped[str] = mapped_column(String(512))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="alerts")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Script(Base):
    """Wiederverwendbares Skript in der Bibliothek. Ein Lauf erzeugt daraus einen
    Command am Agent."""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    shell: Mapped[str] = mapped_column(String(16), default="powershell")
    body: Mapped[str] = mapped_column(Text)
    default_timeout: Mapped[int] = mapped_column(Integer, default=300)
    # Plattformhinweis für die Oberfläche: windows | linux | any
    platform: Mapped[str] = mapped_column(String(16), default="any")
    category: Mapped[str] = mapped_column(String(64), default="Allgemein")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Check(Base):
    """Eine wiederkehrende Überwachung. Läuft auf dem Agent und meldet pass/fail.
    Gilt global, je Standort oder für einen einzelnen Agent."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # cpu | mem | disk | service | port | ping | script
    type: Mapped[str] = mapped_column(String(24))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    # global | site | agent
    scope_type: Mapped[str] = mapped_column(String(16), default="global")
    scope_value: Mapped[str] = mapped_column(String(128), default="")
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CheckResult(Base):
    """Letztes Ergebnis einer Prüfung je Agent (wird aktualisiert, nicht angehäuft)."""

    __tablename__ = "check_results"
    __table_args__ = (UniqueConstraint("check_id", "agent_id", name="uq_check_agent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # pass|fail|unknown
    value: Mapped[str] = mapped_column(String(255), default="")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Webhook(Base):
    """Ausgehender Webhook. Bei bestimmten Ereignissen wird eine signierte
    JSON-Nachricht an die URL geschickt — etwa an n8n."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(1024))
    secret: Mapped[str] = mapped_column(String(128), default="")
    events: Mapped[list] = mapped_column(JSON, default=list)  # z. B. ["alert.raised", ...]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_sent: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationToken(Base):
    """Langlebiges Token für Automatisierungen (n8n, KI). Spricht die API im Namen
    einer Rolle an — der Klartext wird nur bei der Erstellung gezeigt."""

    __tablename__ = "integration_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PatchReport(Base):
    """Letzter Patch-Stand eines Agents."""

    __tablename__ = "patch_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True
    )
    updates: Mapped[list] = mapped_column(JSON, default=list)  # [{id,title,severity,kb,size}]
    count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_apply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_apply_output: Mapped[str | None] = mapped_column(Text, nullable=True)
