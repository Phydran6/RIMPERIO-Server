# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.02.13.30.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Pydantic Schemas
# ==============================================================================

import re
from datetime import datetime
from typing import Optional, List, Any, Dict, Union, Literal
from pydantic import BaseModel, Field, field_validator

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# -----------------------------------------------------------------------------
# MFA / TOTP
# -----------------------------------------------------------------------------
class LoginMFARequired(BaseModel):
    """Antwort auf /api/auth/login wenn der User MFA aktiviert hat."""
    mfa_required: Literal[True] = True
    mfa_token: str
    expires_in_seconds: int


class MFALoginRequest(BaseModel):
    mfa_token: str = Field(..., min_length=10)
    code: str = Field(..., min_length=6, max_length=16)  # 6-stellig TOTP oder 10-stelliger Backup-Code


class MFASetupResponse(BaseModel):
    secret: str               # Base32 zum manuellen Eintippen
    otpauth_uri: str          # otpauth://totp/...
    qr_image: str             # data:image/png;base64,...


class MFAVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8)  # frisch eingerichteter TOTP


class MFAVerifyResponse(BaseModel):
    enabled: bool = True
    backup_codes: List[str]   # Klartext, EINMALIG nach Setup zurückgegeben


class MFADisableRequest(BaseModel):
    password: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=16)


class MFARegenerateRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=16)


class MFARegenerateResponse(BaseModel):
    backup_codes: List[str]


class MFAStatusResponse(BaseModel):
    enabled: bool
    backup_codes_remaining: int = 0
    locked_until: Optional[datetime] = None

class TokenData(BaseModel):
    username: Optional[str] = None

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[str] = None
    role: str = Field(default="user")

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Passwort muss mindestens einen Großbuchstaben enthalten")
        if not re.search(r"[0-9]", v):
            raise ValueError("Passwort muss mindestens eine Ziffer enthalten")
        return v

class UserUpdate(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    # Pflicht, wenn jemand SEIN EIGENES Passwort aendert. Ohne das genuegte eine
    # gueltige Sitzung, um das Passwort zu setzen - wer sich einen Token
    # beschafft hatte, konnte damit den rechtmaessigen Besitzer aussperren.
    # Ein Administrator, der ein FREMDES Passwort zuruecksetzt, braucht es nicht:
    # er kennt es ja nicht.
    current_password: Optional[str] = Field(default=None, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.search(r"[A-Z]", v):
            raise ValueError("Passwort muss mindestens einen Großbuchstaben enthalten")
        if not re.search(r"[0-9]", v):
            raise ValueError("Passwort muss mindestens eine Ziffer enthalten")
        return v

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    class Config:
        from_attributes = True

class AgentResponse(BaseModel):
    id: int
    hostname: str
    ip_address: Optional[str]
    mac_address: Optional[str]
    device_type: str
    last_seen: datetime
    first_seen: datetime
    extra_data: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    is_online: Optional[bool] = None
    log_count: Optional[int] = None
    retention_max_logs: Optional[int] = None
    retention_days: Optional[int] = None
    # Gesetzt, wenn auf dem Gerät der RIMPERIO-Agent (Fernwartung) läuft.
    agent_uid: Optional[str] = None
    class Config:
        from_attributes = True

class AgentRetentionUpdate(BaseModel):
    retention_max_logs: Optional[int] = Field(default=None, ge=1000, le=10_000_000)
    retention_days: Optional[int] = Field(default=None, ge=1, le=3650)

class AgentListResponse(BaseModel):
    items: List[AgentResponse]
    total: int
    page: int
    page_size: int

class LogParsed(BaseModel):
    """Die lesbare Fassung einer Logzeile (siehe app/logparse.py).

    Warum das mit in die Liste gehoert und nicht nur in die Einzelansicht: eine
    Logzeile, die man erst aufklappen muss, um sie zu verstehen, hilft beim
    Ueberfliegen von zweihundert Zeilen gar nichts. Die Rohzeile bleibt
    daneben - wenn die Erkennung danebenliegt, muss man nachsehen koennen.
    """
    summary: str = ""
    format: str = "plain"
    readable: bool = False
    highlights: List[Dict[str, Any]] = []
    fields: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}


class LogResponse(BaseModel):
    id: int
    hostname: Optional[str]
    ip_address: Optional[str]
    timestamp: datetime
    level: Optional[str]
    source: Optional[str]
    message: Optional[str]
    # Wie oft diese Meldung zusammengefasst wurde (None = einmal).
    repeat: Optional[int] = None
    # Nur gefuellt, wenn die Liste mit readable=true abgefragt wurde.
    parsed: Optional[LogParsed] = None
    raw_message: Optional[str] = None
    class Config:
        from_attributes = True

class LogDetailResponse(LogResponse):
    agent_id: Optional[int]
    facility: Optional[int]
    raw_message: Optional[str]
    extra_data: Dict[str, Any] = {}

class LogListResponse(BaseModel):
    items: List[LogResponse]
    total: int
    page: int
    page_size: int

class LogStatsResponse(BaseModel):
    total_logs: int
    logs_today: int
    logs_by_level: Dict[str, int]
    logs_by_source: Dict[str, int]
    unique_hosts: int

class WebhookFilters(BaseModel):
    hostname: Optional[str] = None
    source: Optional[str] = None
    level: Optional[List[str]] = None

class WebhookBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    filters: WebhookFilters = WebhookFilters()
    max_results: int = Field(default=100, ge=1, le=1000)
    include_raw: bool = False
    is_active: bool = True

class WebhookCreate(WebhookBase):
    pass

class WebhookUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    filters: Optional[WebhookFilters] = None
    max_results: Optional[int] = None
    include_raw: Optional[bool] = None
    is_active: Optional[bool] = None

class WebhookResponse(WebhookBase):
    id: int
    token: str
    call_count: int
    last_called_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class AgentTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    device_type: Optional[str] = Field(default=None, pattern="^(linux|windows)$")

class AgentTokenResponse(BaseModel):
    id: int
    name: str
    token: str
    device_type: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class LogIngestEntry(BaseModel):
    """Ein Log-Eintrag beim HTTPS-Ingest.

    level/source duerfen leer bleiben: bei device_type="fritzbox" leitet der
    Server sie aus event_id/group ab, sonst gilt "info"/"unknown".
    timestamp = Zeitpunkt des Ereignisses auf dem Geraet. Nur Eintraege mit
    timestamp werden dedupliziert (sonst waeren sie ohnehin immer neu).
    """
    level: Optional[str] = Field(default=None, max_length=20)
    # Ohne Laengengrenze: der Server kuerzt auf die Spaltenbreite (100). Mit
    # Grenze lehnte er die GANZE Lieferung ab (422), sobald eine einzige Quelle
    # laenger war - beim Windows-Agent alltaeglich
    # ("Microsoft-Windows-TerminalServices-LocalSessionManager/Operational/...").
    source: Optional[str] = None
    message: str
    timestamp: Optional[datetime] = None
    event_id: Optional[int] = None
    group: Optional[str] = Field(default=None, max_length=32)
    facility: Optional[int] = Field(default=None, ge=0, le=23)

class LogIngestRequest(BaseModel):
    hostname: str = Field(..., min_length=1, max_length=255)
    # Gemeldete Geraete-IP; ohne Angabe gilt die IP des Absenders. Wird gebraucht,
    # wenn ein Sammler (z.B. n8n) die Logs fuer ein anderes Geraet liefert.
    ip_address: Optional[str] = Field(default=None, max_length=45)
    device_type: Optional[str] = Field(default=None, pattern="^[a-z0-9_-]{1,50}$")
    # Grosszuegig: die FRITZ!Box liefert ihren kompletten Puffer (schon ueber 800
    # Eintraege gesehen). Sammler sollten trotzdem stueckeln - siehe n8n-Workflow.
    events: List[LogIngestEntry] = Field(..., max_length=5000)

class LogIngestResponse(BaseModel):
    accepted: int
    duplicates: int = 0
    message: str = "ok"
    # Welcher Geraeteeintrag auf dem Server bekommt diese Logs? Der Agent merkt
    # sich die Nummer und nennt sie beim Deinstallieren - dann trifft das
    # Aufraeumen genau seinen eigenen Eintrag und nicht den eines Namensvetters.
    agent_id: Optional[int] = None

class AgentDecommissionRequest(BaseModel):
    """Der Agent meldet sich ab.

    Zur Zuordnung gibt es vier Wege, vom genauesten zum ungenauesten:
    agent_id (der Agent kennt seine Nummer vom Ingest), MAC, Hostname+IP,
    Hostname allein. Der letzte Weg trifft bei gleichnamigen Rechnern womoeglich
    den falschen - deshalb steht in der Antwort, wonach zugeordnet wurde.
    """
    agent_id: Optional[int] = None
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    purge: bool = False
    # Alle Eintraege dieses Hostnamens erwischen. Wechselt ein Rechner die IP
    # (DHCP, NAT, Umzug), legt der Server jedes Mal einen neuen Eintrag an -
    # beim Abraeumen sollen sie alle mitgehen, nicht nur einer.
    all_for_hostname: bool = False

class SettingsResponse(BaseModel):
    settings: Dict[str, Any]

class SettingUpdate(BaseModel):
    value: Any

class RetentionResponse(BaseModel):
    logs_to_delete: Optional[int] = None
    deleted_count: Optional[int] = None
    oldest_log_date: Optional[datetime] = None
    message: Optional[str] = None

class DatabaseSettingsResponse(BaseModel):
    host: str
    port: int
    user: str
    name: str
    password: str

class HealthResponse(BaseModel):
    status: str
    version: str
    # Vom Wachhund (app/watchdog.py): sammelt RIMPERIO gerade wirklich?
    database: Optional[bool] = None
    last_log_at: Optional[str] = None
    problems: List[str] = []

class HealthDetailedResponse(HealthResponse):
    uptime_seconds: float
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    database_connected: bool
    logs_total: int
    logs_last_24h: int
    agents_total: int
    agents_online: int

# Caddy management
class CaddyConfigResponse(BaseModel):
    running_config: Dict[str, Any] = {}
    saved_caddyfile: str = ""
    cert_present: bool = False
    last_error: Optional[str] = None

class CaddyApplyRequest(BaseModel):
    caddyfile: str = ""
    save: bool = True
    mode: Optional[str] = Field(default=None, pattern="^(http|letsencrypt|custom|internal)$")
    domain: Optional[str] = None
    letsencrypt_email: Optional[str] = None
    # Zusaetzliche Adressen (IPs/Namen), die per HTTPS erreichbar sein sollen
    extra_hosts: List[str] = []
    # Port 80 nur noch als Weiterleitung auf HTTPS (Passwoerter nie im Klartext)
    https_only: bool = False

class CaddyTemplateRequest(BaseModel):
    domain: Optional[str] = None
    mode: str = Field(..., pattern="^(http|letsencrypt|custom|internal)$")
    letsencrypt_email: Optional[str] = None
    extra_hosts: List[str] = []
    https_only: bool = False

# Netzwerk / DNS
class DnsConfigUpdate(BaseModel):
    mode: Literal["dhcp", "manual"] = "dhcp"
    servers: List[str] = []
    search_domains: List[str] = []

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, v: List[str]) -> List[str]:
        import ipaddress
        cleaned = []
        for s in v:
            s = s.strip()
            if not s:
                continue
            ipaddress.ip_address(s)  # ValueError bei ungültiger IP
            cleaned.append(s)
        return cleaned

class DnsConfigResponse(BaseModel):
    mode: str = "dhcp"
    detected: List[str] = []       # per DHCP/System vergebener DNS des Hosts
    configured: List[str] = []     # manuell gespeicherte Server
    active: List[str] = []         # aktuell in /etc/resolv.conf des Containers
    search_domains: List[str] = []

class DnsTestRequest(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)

class DnsTestResponse(BaseModel):
    host: str
    resolved: bool
    addresses: List[str] = []
    error: Optional[str] = None

# App-Login (QR-Code / Token-Exchange für Android-App)
class AppTokenResponse(BaseModel):
    token: str
    expires_at: datetime
    expires_in_seconds: int

class AppTokenExchangeRequest(BaseModel):
    token: str = Field(..., min_length=64, max_length=64)

class AppQRResponse(BaseModel):
    qr_image: str          # data:image/png;base64,...
    token: str
    expires_at: datetime
    expires_in_seconds: int
    api_url: str
