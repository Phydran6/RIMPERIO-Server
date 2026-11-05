"""
Datei:        schemas.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Die Form aller Daten, die über die API hinein- und hinausgehen
              (Pydantic v2). Ein Vertrag zwischen Server, Oberfläche und Agent.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- Anmeldung / Benutzer --------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_active: bool
    last_login: datetime | None = None


class TokenResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    role: str = "operator"


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=256)


# --- Agent-Anmeldung (Enrollment) -----------------------------------------
class EnrollRequest(BaseModel):
    enroll_secret: str
    hostname: str
    os: str = ""
    platform: str = ""
    arch: str = ""
    version: str = ""
    local_ip: str | None = None
    site: str = "Standard"
    description: str = ""


class EnrollResponse(BaseModel):
    agent_uid: str
    agent_token: str
    checkin_interval: int


# --- Telemetrie / Check-in -------------------------------------------------
class DiskIn(BaseModel):
    mount: str
    percent: float = 0.0
    used_gb: float = 0.0
    total_gb: float = 0.0


class ProcIn(BaseModel):
    name: str
    cpu: float = 0.0
    mem: float = 0.0


class TelemetryIn(BaseModel):
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    mem_used_mb: float = 0.0
    mem_total_mb: float = 0.0
    uptime_seconds: int = 0
    boot_time: datetime | None = None
    disk: list[DiskIn] = []
    top_procs: list[ProcIn] = []


class CheckinRequest(BaseModel):
    version: str | None = None
    local_ip: str | None = None
    public_ip: str | None = None
    telemetry: TelemetryIn | None = None


class PendingCommand(BaseModel):
    id: int
    shell: str
    command: str
    timeout: int
    kind: str = "shell"  # shell | patch_scan | patch_apply | software_inventory


class CheckSpec(BaseModel):
    """Eine Prüfung, die der Agent selbst im eigenen Takt ausführt."""
    id: int
    type: str
    params: dict = {}
    interval: int = 300


class CheckinResponse(BaseModel):
    interval: int
    offline_after: int
    commands: list[PendingCommand] = []
    checks: list[CheckSpec] = []


class CheckResultIn(BaseModel):
    check_id: int
    status: str = "unknown"  # pass | fail | unknown
    value: str = ""
    output: str | None = None


class CommandResultIn(BaseModel):
    id: int
    status: str = "done"  # done|error|timeout
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


# --- Befehle (von der Oberfläche) -----------------------------------------
class CommandCreate(BaseModel):
    shell: str = "powershell"
    command: str = Field(min_length=1)
    timeout: int = Field(default=300, ge=1, le=86400)


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_id: int
    shell: str
    command: str
    timeout: int
    status: str
    created_by: str
    created_at: datetime
    picked_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


# --- Agents (Ausgabe an die Oberfläche) -----------------------------------
class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_uid: str
    hostname: str
    site: str
    description: str
    os: str
    platform: str
    arch: str
    version: str
    public_ip: str | None = None
    local_ip: str | None = None
    enrolled_at: datetime
    last_seen: datetime | None = None
    last_cpu: float | None = None
    last_mem: float | None = None
    last_disk: float | None = None
    online: bool = False


class AgentDetailOut(AgentOut):
    boot_time: datetime | None = None
    inventory: dict | None = None


class AgentUpdate(BaseModel):
    site: str | None = None
    description: str | None = None


# --- Alarme ----------------------------------------------------------------
class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_id: int | None = None
    kind: str
    severity: str
    message: str
    active: bool
    created_at: datetime
    resolved_at: datetime | None = None


# --- Dashboard -------------------------------------------------------------
class DashboardSummary(BaseModel):
    agents_total: int
    agents_online: int
    agents_offline: int
    alerts_active: int
    commands_pending: int
    by_platform: dict[str, int]
    by_site: dict[str, int]


# --- Einstellungen ---------------------------------------------------------
class ThresholdSettings(BaseModel):
    cpu_percent: float = 90.0
    mem_percent: float = 90.0
    disk_percent: float = 90.0
    offline_after: int = 300


class SettingsOut(BaseModel):
    thresholds: ThresholdSettings
    checkin_interval: int
    version: str


# --- Skript-Bibliothek -----------------------------------------------------
class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    shell: str = "powershell"
    body: str = Field(min_length=1)
    default_timeout: int = Field(default=300, ge=1, le=86400)
    platform: str = "any"
    category: str = "Allgemein"


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    shell: str
    body: str
    default_timeout: int
    platform: str
    category: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class RunScript(BaseModel):
    timeout: int | None = None  # überschreibt den Standard des Skripts


# --- Checks ----------------------------------------------------------------
class CheckIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str  # cpu|mem|disk|service|port|ping|script
    params: dict = {}
    interval_sec: int = Field(default=300, ge=30, le=86400)
    scope_type: str = "global"  # global|site|agent
    scope_value: str = ""
    severity: str = "warning"
    enabled: bool = True


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    params: dict
    interval_sec: int
    scope_type: str
    scope_value: str
    severity: str
    enabled: bool
    created_at: datetime


class CheckResultOut(BaseModel):
    check_id: int
    agent_uid: str
    hostname: str
    name: str
    type: str
    status: str
    value: str
    output: str | None = None
    ts: datetime


# --- Webhooks --------------------------------------------------------------
class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1)
    secret: str = ""
    events: list[str] = []
    enabled: bool = True


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime
    last_status: int | None = None
    last_error: str | None = None
    last_sent: datetime | None = None


# --- Integrations-Token ----------------------------------------------------
class IntegrationTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    role: str = "operator"


class IntegrationTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    role: str
    created_at: datetime
    last_used: datetime | None = None


class IntegrationTokenCreated(IntegrationTokenOut):
    token: str  # nur einmalig bei der Erstellung


# --- Patch -----------------------------------------------------------------
class PatchReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    count: int
    updates: list = []
    scanned_at: datetime | None = None
    last_apply_at: datetime | None = None
    last_apply_output: str | None = None
