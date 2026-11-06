<#
==============================================================================
Name:        Phydran6
Kontakt:     Phydran6
Changelog:   docs/changelog/agents.md

RIMPERIO Agent - Windows Installer
================================
Zwei Betriebsarten:

  * HTTPS (Standard, empfohlen): verschluesselt + Token, laeuft auch ueber das
    Internet. Ziel ist https://<FQDN>/api/agents/ingest.
  * Syslog (UDP): klassisch, unverschluesselt, nur im eigenen Netz sinnvoll.

EINZEILER (PowerShell als Administrator)
----------------------------------------
Vollautomatisch, mit FQDN und Token:

  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-windows.ps1))) -Action install -Fqdn rimperio.example.com -Token DEIN-TOKEN -Yes

Deinstallieren (raeumt auf Wunsch auch den Server-Eintrag ab):

  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-windows.ps1))) -Action uninstall -PurgeServer -Yes

Ohne -Action erscheint wie bisher das Menue.

WARUM DIESE SCHREIBWEISE
------------------------
`irm ... | iex` kann keine Parameter uebergeben - die Pipeline reicht nur den
Text weiter. Mit [scriptblock]::Create wird der geholte Text zu einem echten
Skriptblock, und der nimmt Parameter entgegen wie eine normale Funktion.
==============================================================================
#>

#Requires -RunAsAdministrator

param(
    # install | test | uninstall | menu (leer = Menue)
    [ValidateSet("", "install", "test", "uninstall", "menu")]
    [string]$Action = "",

    # Server
    [string]$Fqdn = "",
    [string]$ServerFqdn = "",          # alter Name, bleibt gueltig
    [string]$ServerIP = "",
    [int]$ServerPort = 0,

    # HTTPS-Betrieb
    [string]$Token = "",
    [ValidateSet("", "https", "syslog")]
    [string]$Mode = "",
    [switch]$Insecure,                 # selbstsignierte Zertifikate annehmen

    # Verhalten
    [ValidateSet("", "info", "warning", "error")]
    [string]$MinLevel = "",
    [switch]$Yes,                      # keine Rueckfragen, Standardwerte nehmen
    [switch]$PurgeServer               # beim Deinstallieren auch auf dem Server abraeumen
)

$ErrorActionPreference = "Stop"

# -Fqdn und -ServerFqdn meinen dasselbe; der kuerzere gewinnt.
if ([string]::IsNullOrWhiteSpace($ServerFqdn) -and -not [string]::IsNullOrWhiteSpace($Fqdn)) {
    $ServerFqdn = $Fqdn
}

# Unbeaufsichtigt laeuft es, sobald -Yes gesetzt ist ODER kein Mensch davorsitzt
# (Aufruf aus einem Skript, per Fernwartung, aus der Aufgabenplanung). Dann wird
# nie gefragt, sondern immer der Standardwert genommen - sonst bliebe der Lauf
# an der ersten Rueckfrage stehen.
$script:Unattended = $Yes.IsPresent -or -not [Environment]::UserInteractive

# Read-Host mit Standardwert, das im unbeaufsichtigten Betrieb nicht fragt.
function Read-Answer {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$Default = ""
    )
    if ($script:Unattended) {
        Write-Host "  $Prompt -> $(if ($Default) { $Default } else { '(leer)' })"
        return $Default
    }
    $label = if ($Default) { "$Prompt [$Default]" } else { $Prompt }
    $answer = Read-Host $label
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

# Konfiguration
$INSTALL_DIR = "$env:ProgramData\RIMPERIO-Agent"
$SCRIPT_NAME = "RIMPERIOAgent.ps1"
$TASK_NAME = "RIMPERIOAgent"
$AGENT_VERSION = "2026.10.11.20.00.00"

# ==============================================================================
# Hilfsfunktionen
# ==============================================================================

function Write-LogInfo($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-LogSuccess($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-LogWarn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-LogError($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

function Test-ServerReachable {
    param([string]$TargetHost, [int]$Port, [string]$Label)
    Write-LogInfo "Pruefe $Label ($TargetHost`:$Port)..."
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $result = $tcp.BeginConnect($TargetHost, $Port, $null, $null)
        $wait = $result.AsyncWaitHandle.WaitOne(3000, $false)
        if ($wait -and $tcp.Connected) {
            $tcp.Close()
            Write-LogSuccess "$Label erreichbar"
            return $true
        }
        $tcp.Close()
    } catch {}
    Write-LogWarn "$Label NICHT erreichbar"
    return $false
}

function Test-DnsResolve {
    param([string]$Fqdn)
    try {
        $resolved = [System.Net.Dns]::GetHostAddresses($Fqdn)
        if ($resolved.Count -gt 0) {
            Write-LogSuccess "DNS aufgeloest: $Fqdn -> $($resolved[0])"
            return $true
        }
    } catch {}
    Write-LogWarn "DNS-Aufloesung fehlgeschlagen: $Fqdn"
    return $false
}

# ==============================================================================
# Agent Script - Syslog Modus (UDP, wie bisher)
# ==============================================================================

$AgentScriptSyslog = @'
<#
==============================================================================
RIMPERIO Agent - Windows Event Log Forwarder (Syslog/UDP)
==============================================================================
#>
param([string]$ConfigFile = "$PSScriptRoot\config.json")

if (Test-Path $ConfigFile) {
    $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
} else { Write-Error "Konfiguration nicht gefunden: $ConfigFile"; exit 1 }

$ServerFqdn = $Config.server_fqdn
$ServerIP = $Config.server_ip
$ServerPort = $Config.server_port
$LogSources = $Config.log_sources
$MinLevel = $Config.min_level
$Hostname = if ($Config.hostname) { $Config.hostname } else { $env:COMPUTERNAME }

$LevelMap = @{ 1=2; 2=3; 3=4; 4=6; 0=6; 5=7 }
$MinLevelNum = @{ "debug"=7; "info"=6; "notice"=5; "warning"=4; "error"=3; "critical"=2 }[$MinLevel]
if (-not $MinLevelNum) { $MinLevelNum = 6 }

$UdpClient = New-Object System.Net.Sockets.UdpClient

# Aktiven Server ermitteln (FQDN first, IP fallback)
function Get-ActiveServer {
    if ($ServerFqdn) {
        try {
            [System.Net.Dns]::GetHostAddresses($ServerFqdn) | Out-Null
            return $ServerFqdn
        } catch {}
    }
    if ($ServerIP) { return $ServerIP }
    return $ServerFqdn
}

function Send-Syslog {
    param([string]$Message, [int]$Severity = 6, [string]$Source = "EventLog")
    $Priority = 8 + $Severity
    $Timestamp = Get-Date -Format "MMM dd HH:mm:ss"
    $SyslogMsg = "<$Priority>$Timestamp $Hostname $Source`: $Message"
    $Target = Get-ActiveServer
    try {
        $Bytes = [System.Text.Encoding]::UTF8.GetBytes($SyslogMsg)
        $UdpClient.Send($Bytes, $Bytes.Length, $Target, $ServerPort) | Out-Null
    } catch { Write-Warning "Sendefehler ($Target): $_" }
}

function Process-Event {
    param($Event, [string]$LogName)
    $Level = $LevelMap[[int]$Event.Level]
    if (-not $Level) { $Level = 6 }
    if ($Level -gt $MinLevelNum) { return }
    $Message = $Event.Message
    if (-not $Message) { $Message = "EventID: $($Event.Id)" }
    # Erst ersetzen, dann kuerzen: aus CRLF wird EIN Zeichen, der Text wird
    # kuerzer. Mit der alten Laenge warf Substring() bei jeder mehrzeiligen
    # Meldung - sie und der Rest des Kanals in diesem Durchlauf fehlten.
    $Message = $Message -replace "`r`n|`n|`r", " "
    if ($Message.Length -gt 1024) { $Message = $Message.Substring(0, 1024) }
    Send-Syslog -Message $Message -Severity $Level -Source "$LogName/$($Event.ProviderName)"
}

Write-Host "RIMPERIO Agent (Syslog) gestartet - Server: $(Get-ActiveServer):$ServerPort"

# Leseposition je Kanal ueber die fortlaufende RecordId statt "die neuesten
# 100 seit Zeitpunkt X": frueher fielen bei mehr als 100 Ereignissen zwischen
# zwei Durchlaeufen die aelteren still weg.
$Positions = @{}
foreach ($LogName in $LogSources) {
    $e = Get-WinEvent -LogName $LogName -MaxEvents 1 -ErrorAction SilentlyContinue
    $Positions[$LogName] = if ($e) { [long]$e.RecordId } else { [long]0 }
}

while ($true) {
    $More = $false
    foreach ($LogName in $LogSources) {
        try {
            $Events = @(Get-WinEvent -LogName $LogName `
                -FilterXPath "*[System[(EventRecordID > $($Positions[$LogName]))]]" `
                -MaxEvents 500 -Oldest -ErrorAction SilentlyContinue)
            if ($Events.Count -ge 500) { $More = $true }
            foreach ($Ev in $Events) {
                Process-Event -Event $Ev -LogName $LogName
                $Positions[$LogName] = [long]$Ev.RecordId
            }
            # Protokoll geleert: RecordId beginnt neu.
            if (-not $Events.Count) {
                $e = Get-WinEvent -LogName $LogName -MaxEvents 1 -ErrorAction SilentlyContinue
                if ($e -and [long]$e.RecordId -lt $Positions[$LogName]) { $Positions[$LogName] = [long]0 }
            }
        } catch {}
    }
    if ($More) { Start-Sleep -Seconds 1 } else { Start-Sleep -Seconds 5 }
}
'@

# ==============================================================================
# Agent Script - HTTPS Modus (verschluesselt + authentifiziert, ALLE Logs)
# ==============================================================================

$AgentScriptHttps = @'
<#
==============================================================================
RIMPERIO Agent - Windows Event Log Forwarder (HTTPS + Token Auth)
Sammelt ALLE verfuegbaren Event Logs und sendet sie verschluesselt
==============================================================================
#>
param([string]$ConfigFile = "$PSScriptRoot\config.json")

if (Test-Path $ConfigFile) {
    $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
} else { Write-Error "Konfiguration nicht gefunden: $ConfigFile"; exit 1 }

$ServerFqdn = $Config.server_fqdn
$ServerIP = $Config.server_ip
$ServerPort = $Config.server_port
$AgentToken = $Config.agent_token
$SkipTlsVerify = $Config.skip_tls_verify
$MinLevel = $Config.min_level
$Hostname = if ($Config.hostname) { $Config.hostname } else { $env:COMPUTERNAME }

$LevelMap = @{ 1="critical"; 2="error"; 3="warning"; 4="info"; 0="info"; 5="debug" }
$LevelPriority = @{ "debug"=7; "info"=6; "notice"=5; "warning"=4; "error"=3; "critical"=2 }
$MinLevelNum = $LevelPriority[$MinLevel]
if (-not $MinLevelNum) { $MinLevelNum = 6 }

# TLS-Zertifikat-Pruefung deaktivieren wenn gewuenscht
if ($SkipTlsVerify) {
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $script:TlsParam = @{ SkipCertificateCheck = $true }
    } else {
        $script:TlsParam = @{}
        Add-Type @"
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public class TrustAll {
    public static void Enable() {
        ServicePointManager.ServerCertificateValidationCallback = delegate { return true; };
    }
}
"@
        [TrustAll]::Enable()
    }
} else {
    $script:TlsParam = @{}
}

$Headers = @{
    "Authorization" = "Bearer $AgentToken"
    "Content-Type" = "application/json"
}

# Aktive Server-URL ermitteln (FQDN first, IP fallback)
$script:CurrentUrl = $null

function Get-IngestUrl {
    # Wenn aktuelle URL funktioniert, beibehalten
    if ($script:CurrentUrl) { return $script:CurrentUrl }

    # FQDN versuchen
    if ($ServerFqdn) {
        $url = "https://${ServerFqdn}:${ServerPort}/api/agents/ingest"
        if ($ServerPort -eq 443) { $url = "https://${ServerFqdn}/api/agents/ingest" }
        try {
            [System.Net.Dns]::GetHostAddresses($ServerFqdn) | Out-Null
            $script:CurrentUrl = $url
            return $url
        } catch {
            Write-Warning "FQDN nicht aufloesbar: $ServerFqdn - Fallback auf IP"
        }
    }

    # IP Fallback
    if ($ServerIP) {
        $url = "https://${ServerIP}:${ServerPort}/api/agents/ingest"
        if ($ServerPort -eq 443) { $url = "https://${ServerIP}/api/agents/ingest" }
        $script:CurrentUrl = $url
        return $url
    }

    return $null
}

# Merkt sich, welcher Geraeteeintrag auf dem Server zu diesem Rechner gehoert.
# Beim Deinstallieren wird genau dieser abgeraeumt - und nicht der eines
# gleichnamigen Rechners.
function Save-AgentId {
    param($Response)
    if (-not $Response -or -not $Response.agent_id) { return }
    $file = Join-Path $PSScriptRoot "agent_id"
    try {
        if ((Test-Path $file) -and ((Get-Content $file -Raw).Trim() -eq "$($Response.agent_id)")) { return }
        Set-Content -Path $file -Value "$($Response.agent_id)" -NoNewline -Encoding ascii
        Write-Host "Geraetenummer auf dem Server: $($Response.agent_id)"
    } catch {
        # Kein Grund abzubrechen: ohne die Nummer greift beim Abmelden der
        # ungenauere Weg ueber den Hostnamen.
    }
}

# Ergebnis eines Sendeversuchs:
#   ok    - angekommen (auch: vom Server als Duplikat erkannt)
#   retry - spaeter GENAU DIESES Paket nochmal (Netz, Server weg, 429/5xx)
#   drop  - der Server lehnt den Inhalt dauerhaft ab (400/413/422). Erneut
#           schicken wuerde ewig scheitern und alles dahinter blockieren.
function Send-LogBatch {
    param([array]$Events)
    if ($Events.Count -eq 0) { return @{ Result = "ok"; Wait = 0 } }

    $url = Get-IngestUrl
    if (-not $url) { Write-Warning "Kein Server erreichbar"; return @{ Result = "retry"; Wait = 0 } }

    $body = @{
        hostname = $Hostname
        device_type = "windows_agent"
        events = @($Events)
    } | ConvertTo-Json -Depth 4

    try {
        # Als UTF-8-Bytes senden: Windows PowerShell 5.1 kodiert einen Text-Body
        # sonst als ISO-8859-1. Aus "ue" (Umlaut) wird ein ungueltiges Byte, der
        # Server lehnt die GANZE Lieferung ab - 50 Ereignisse still verloren.
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $response = Invoke-RestMethod -Uri $url -Method POST -Headers $Headers -Body $bodyBytes -TimeoutSec 30 @script:TlsParam
        Save-AgentId -Response $response
        return @{ Result = "ok"; Wait = 0 }
    } catch {
        $code = 0
        try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -in 400, 413, 422) {
            Write-Warning "HTTP $code - Paket mit $($Events.Count) Eintraegen verworfen: $_"
            return @{ Result = "drop"; Wait = 0 }
        }
        $wait = 0
        if ($code -in 429, 503) {
            # Der Server sagt, wann es wieder geht (z.B. Platte voll: 300 s).
            try { $wait = [int]$_.Exception.Response.Headers["Retry-After"] } catch {}
            if (-not $wait) { try { $wait = [int]$_.Exception.Response.Headers.RetryAfter.Delta.TotalSeconds } catch {} }
        }
        Write-Warning "Sendefehler ($url, HTTP $code): $_"
        # URL zuruecksetzen fuer naechsten Versuch (Fallback-Logik)
        $script:CurrentUrl = $null
        return @{ Result = "retry"; Wait = [Math]::Min([Math]::Max($wait, 0), $MaxBackoff) }
    }
}

# ------------------------------------------------------------------------------
# Leseposition je Kanal = RecordId des zuletzt ERFOLGREICH gesendeten
# Ereignisses. Liegt auf der Platte, damit ein Neustart nichts verliert.
#
# Frueher: -MaxEvents 100 holte die NEUESTEN 100 Ereignisse und filterte nach
# Zeit. Kamen zwischen zwei Durchlaeufen mehr, waren die aelteren still weg.
# Und ein gescheitertes Senden verwarf das Paket, die Zeit lief trotzdem weiter.
# ------------------------------------------------------------------------------
$PositionFile = Join-Path $PSScriptRoot "positions.json"
$MaxPerChannel = 500      # Ereignisse je Kanal und Durchlauf
$MaxPerRound = 5000       # Ereignisse je Durchlauf insgesamt (Rueckstand gedrosselt)
$BatchSize = 200          # Ereignisse je Anfrage (der Server nimmt bis 5000)
$MaxBackoff = 300         # laengste Pause nach Sendefehlern (Sekunden)

function Load-Positions {
    $map = @{}
    if (Test-Path $PositionFile) {
        try {
            $data = Get-Content $PositionFile -Raw | ConvertFrom-Json
            foreach ($p in $data.PSObject.Properties) { $map[$p.Name] = [long]$p.Value }
        } catch { Write-Warning "positions.json unlesbar - beginne bei jetzt" }
    }
    return $map
}

function Save-Positions {
    param($Map)
    try {
        $tmp = "$PositionFile.tmp"
        ($Map | ConvertTo-Json -Compress) | Set-Content -Path $tmp -Encoding UTF8
        Move-Item -Path $tmp -Destination $PositionFile -Force
    } catch { Write-Warning "Position nicht gespeichert: $_" }
}

function Get-NewestRecordId {
    param([string]$LogName)
    $e = Get-WinEvent -LogName $LogName -MaxEvents 1 -ErrorAction SilentlyContinue
    if ($e) { return [long]$e.RecordId }
    return [long]0
}

function Read-Channel {
    param([string]$LogName, [long]$After, [int]$Max)
    # Aelteste zuerst, lueckenlos ab der gemerkten Position.
    return @(Get-WinEvent -LogName $LogName -FilterXPath "*[System[(EventRecordID > $After)]]" `
                -MaxEvents $Max -Oldest -ErrorAction SilentlyContinue)
}

# Alle verfuegbaren Event Logs ermitteln
function Get-ActiveLogNames {
    $logs = Get-WinEvent -ListLog * -ErrorAction SilentlyContinue |
            Where-Object { $_.RecordCount -gt 0 } |
            Select-Object -ExpandProperty LogName
    return $logs
}

Write-Host "RIMPERIO Agent (HTTPS) gestartet"
Write-Host "Hostname: $Hostname"
Write-Host "Ermittle alle verfuegbaren Event Logs..."

$AllLogs = Get-ActiveLogNames
Write-Host "Gefunden: $($AllLogs.Count) aktive Log-Kanaele"
Write-Host "---"

$Positions = Load-Positions
$LogRefreshTime = Get-Date
$Failures = 0

while ($true) {
    $Dirty = $false

    # Log-Kanaele regelmaessig aktualisieren (neue Logs koennen hinzukommen)
    # und geleerte Protokolle erkennen: dort beginnt die RecordId neu, die
    # gemerkte Position laege sonst fuer immer hinter dem Ende.
    if ((Get-Date) -gt $LogRefreshTime.AddMinutes(5)) {
        $AllLogs = Get-ActiveLogNames
        $LogRefreshTime = Get-Date
        foreach ($LogName in @($Positions.Keys)) {
            $Newest = Get-NewestRecordId -LogName $LogName
            if ($Newest -lt $Positions[$LogName]) { $Positions[$LogName] = [long]0; $Dirty = $true }
        }
    }

    # Lesen: je Kanal ab der gemerkten Position. Jedes Paket merkt sich, bis
    # zu welcher RecordId es je Kanal reicht - die Position rueckt erst vor,
    # wenn genau dieses Paket angekommen ist.
    $Pending = New-Object System.Collections.ArrayList
    $Batch = New-Object System.Collections.ArrayList
    $BatchPos = @{}
    $Total = 0
    $More = $false

    foreach ($LogName in $AllLogs) {
        if (-not $Positions.ContainsKey($LogName)) {
            # Neuer Kanal (oder erster Start): ab jetzt - keine Vorgeschichte.
            $Positions[$LogName] = Get-NewestRecordId -LogName $LogName
            $Dirty = $true
            continue
        }
        if ($Total -ge $MaxPerRound) { $More = $true; break }

        try {
            $Events = Read-Channel -LogName $LogName -After $Positions[$LogName] -Max $MaxPerChannel
        } catch { continue }
        if ($Events.Count -ge $MaxPerChannel) { $More = $true }

        foreach ($Ev in $Events) {
            $Total++
            # Position auch fuer herausgefilterte Ereignisse vorruecken.
            $BatchPos[$LogName] = [long]$Ev.RecordId

            $Level = $LevelMap[[int]$Ev.Level]
            if (-not $Level) { $Level = "info" }
            $LevelNum = $LevelPriority[$Level]
            if (-not $LevelNum) { $LevelNum = 6 }
            if ($LevelNum -gt $MinLevelNum) { continue }

            $Message = $null
            try { $Message = $Ev.Message } catch {}
            if (-not $Message) { $Message = "EventID: $($Ev.Id)" }
            # Erst ersetzen, dann kuerzen: aus CRLF wird EIN Zeichen, der Text
            # wird kuerzer. Mit der alten Laenge warf Substring() bei jeder
            # mehrzeiligen Meldung - sie und der Rest des Kanals fehlten.
            $Message = $Message -replace "`r`n|`n|`r", " "
            if ($Message.Length -gt 2048) { $Message = $Message.Substring(0, 2048) }

            # Zeitpunkt des Ereignisses + Event-ID: der Server erkennt damit
            # doppelt gelieferte Eintraege, nachgeholte landen zur richtigen Zeit.
            $null = $Batch.Add(@{
                level = $Level
                source = "$LogName/$($Ev.ProviderName)"
                message = $Message
                timestamp = $Ev.TimeCreated.ToUniversalTime().ToString("o")
                event_id = [int]$Ev.Id
            })

            if ($Batch.Count -ge $BatchSize) {
                $null = $Pending.Add(@{ Events = $Batch.ToArray(); Pos = $BatchPos.Clone() })
                $Batch = New-Object System.Collections.ArrayList
                $BatchPos = @{}
            }
        }
    }
    if ($Batch.Count -gt 0 -or $BatchPos.Count -gt 0) {
        $null = $Pending.Add(@{ Events = $Batch.ToArray(); Pos = $BatchPos.Clone() })
    }

    # Senden: Paket fuer Paket. Scheitert eines, wird nur ab diesem wiederholt.
    $Failed = $false
    $Wait = 0
    foreach ($Item in $Pending) {
        $Result = Send-LogBatch -Events $Item.Events
        if ($Result.Result -eq "retry") { $Failed = $true; $Wait = $Result.Wait; break }
        foreach ($Key in $Item.Pos.Keys) { $Positions[$Key] = $Item.Pos[$Key] }
        if ($Item.Pos.Count -gt 0) { $Dirty = $true }
        $Failures = 0
    }
    if ($Dirty) { Save-Positions -Map $Positions }

    if ($Failed) {
        # Wachsende Pause (5 s, 10 s, 20 s ... bis 5 min), damit ein
        # angeschlagener Server nicht zusaetzlich unter Beschuss geraet.
        $Failures++
        $Backoff = [Math]::Min(5 * [Math]::Pow(2, [Math]::Min($Failures - 1, 10)), $MaxBackoff)
        Start-Sleep -Seconds ([int][Math]::Max($Wait, $Backoff))
        continue
    }

    # Rueckstand gedrosselt abarbeiten, sonst im normalen Takt.
    if ($More) { Start-Sleep -Seconds 2 } else { Start-Sleep -Seconds 5 }
}
'@

# ==============================================================================
# Installation
# ==============================================================================

function Install-Agent {
    Write-LogInfo "Installiere RIMPERIO Agent..."

    # Verzeichnis erstellen
    if (-not (Test-Path $INSTALL_DIR)) {
        New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
    }

    # --- Modus-Auswahl ---
    # Standard ist HTTPS: verschluesselt, mit Token, funktioniert auch ueber das
    # Internet. Syslog bleibt fuer das eigene Netz waehlbar.
    if ([string]::IsNullOrWhiteSpace($Mode)) {
        Write-Host ""
        Write-Host "Verbindungsmodus:"
        Write-Host "  1) Agent-basiert (HTTPS) - verschluesselt + Token, auch ueber das Internet"
        Write-Host "  2) Syslog (UDP) - unverschluesselt, nur im eigenen Netz"
        $ModeChoice = Read-Answer -Prompt "Auswahl" -Default "1"
        $Mode = if ($ModeChoice -eq "2") { "syslog" } else { "https" }
    }
    Write-LogInfo "Modus: $Mode"

    # --- Server-Adressen ---
    Write-Host ""
    if ([string]::IsNullOrWhiteSpace($ServerFqdn)) {
        $ServerFqdn = Read-Answer -Prompt "RIMPERIO Server FQDN (z.B. rimperio.example.com)"
    }
    if ([string]::IsNullOrWhiteSpace($ServerIP)) {
        $ServerIP = Read-Answer -Prompt "RIMPERIO Server IP als Fallback (leer = keine)"
    }

    # FQDN aufraeumen: aus einer eingefuegten Adresse wird sonst nie ein Ziel.
    $ServerFqdn = $ServerFqdn -replace '^https?://', '' -replace '/.*$', ''

    if ([string]::IsNullOrWhiteSpace($ServerFqdn) -and [string]::IsNullOrWhiteSpace($ServerIP)) {
        Write-LogError "Mindestens FQDN oder IP muss angegeben werden."
        Write-LogInfo  "Einzeiler-Beispiel: ... -Fqdn rimperio.example.com -Token DEIN-TOKEN -Yes"
        exit 1
    }
    if ($Mode -eq "https" -and [string]::IsNullOrWhiteSpace($ServerFqdn)) {
        Write-LogWarn "HTTPS ohne FQDN: das Zertifikat passt dann meist nicht zur IP."
    }

    # --- Port ---
    if ($ServerPort -eq 0) {
        $DefaultPort = if ($Mode -eq "https") { 443 } else { 514 }
        $PortInput = Read-Answer -Prompt "Server Port" -Default "$DefaultPort"
        $ServerPort = [int]$PortInput
    }

    # --- Erreichbarkeitscheck ---
    Write-Host ""
    Write-LogInfo "Pruefe Server-Erreichbarkeit..."

    $FqdnOk = $false
    $IpOk = $false

    if (-not [string]::IsNullOrEmpty($ServerFqdn)) {
        $DnsOk = Test-DnsResolve -Fqdn $ServerFqdn
        if ($DnsOk) {
            $FqdnOk = Test-ServerReachable -TargetHost $ServerFqdn -Port $ServerPort -Label "FQDN"
        }
    }

    if (-not [string]::IsNullOrEmpty($ServerIP)) {
        $IpOk = Test-ServerReachable -TargetHost $ServerIP -Port $ServerPort -Label "IP"
    }

    if (-not $FqdnOk -and -not $IpOk) {
        # Kein Abbruch: ein Rechner wird oft eingerichtet, bevor die Firewall
        # offen ist oder der DNS-Eintrag steht. Der Agent versucht es zur
        # Laufzeit ohnehin immer wieder.
        Write-LogWarn "Der Server ist derzeit weder ueber FQDN noch ueber IP erreichbar."
        $Continue = Read-Answer -Prompt "Trotzdem einrichten? (j/n)" -Default "j"
        if ($Continue -notmatch '^(j|J|y|Y)') {
            Write-LogInfo "Abgebrochen."
            exit 1
        }
    }

    # --- Modus-spezifische Konfiguration ---
    $AgentToken = ""
    $SkipTlsVerify = $false
    $LogSources = @()

    if ($Mode -eq "https") {
        Write-Host ""
        $AgentToken = $Token
        if ([string]::IsNullOrWhiteSpace($AgentToken)) {
            $AgentToken = Read-Answer -Prompt "Zugangsschluessel (Einladung oder Generalschluessel, im Web-UI unter Zugangsschluessel)"
        }
        if ([string]::IsNullOrWhiteSpace($AgentToken)) {
            Write-LogError "Ohne Zugangsschluessel geht der HTTPS-Modus nicht."
            Write-LogInfo  "Den Schluessel erzeugt man im Web-UI unter Verwaltung -> Zugang & Sicherheit -> Zugangsschluessel."
            Write-LogInfo  "Empfohlen: eine 'Einladung' - die laeuft ab und gilt nur einmal."
            exit 1
        }
        if ($Insecure.IsPresent) { $SkipTlsVerify = $true }

        # Token validieren
        Write-LogInfo "Validiere Token..."
        $TestUrl = $null
        if ($FqdnOk) {
            $TestUrl = if ($ServerPort -eq 443) { "https://$ServerFqdn/api" } else { "https://${ServerFqdn}:${ServerPort}/api" }
        } elseif ($IpOk) {
            $TestUrl = if ($ServerPort -eq 443) { "https://$ServerIP/api" } else { "https://${ServerIP}:${ServerPort}/api" }
        }

        if ($TestUrl) {
            try {
                Invoke-RestMethod -Uri $TestUrl -Method GET -TimeoutSec 5 | Out-Null
                Write-LogSuccess "Server-API erreichbar"
            } catch {
                if ($_.Exception.Message -match "SSL|certificate|trust") {
                    Write-LogWarn "TLS-Zertifikat nicht vertrauenswuerdig"
                    $SkipChoice = Read-Answer -Prompt "Selbstsignierte Zertifikate akzeptieren? (j/n)" -Default "n"
                    if ($SkipChoice -match '^(j|J|y|Y)') {
                        $SkipTlsVerify = $true
                        # Temporaer fuer Validierung deaktivieren
                        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
                    }
                } else {
                    Write-LogWarn "API-Check fehlgeschlagen: $_"
                }
            }
        }

        # ----------------------------------------------------------------------
        # Eigenen Geraeteschluessel holen
        # ----------------------------------------------------------------------
        # Frueher lag auf jedem Rechner derselbe Schluessel. Wer einen davon
        # aufmachte, hatte den Schluessel fuer ALLE Geraete - und konnte im Namen
        # jedes beliebigen Rechners Logzeilen erfinden oder Geraete loeschen.
        #
        # Jetzt wird der mitgegebene Schluessel gegen einen eigenen getauscht,
        # der nur fuer diesen Rechner gilt. Der mitgegebene darf eine
        # kurzlebige Einladung sein - dann liegt der Generalschluessel nirgends
        # mehr herum.
        #
        # Scheitert das - etwa weil der Server noch eine aeltere Fassung faehrt
        # und den Endpunkt nicht kennt -, laeuft die Installation mit dem
        # mitgegebenen Schluessel weiter. Ein Agent, der sich wegen einer
        # Neuerung gar nicht installieren laesst, waere die schlechtere Loesung.
        if ($TestUrl) {
            $EnrollUrl = ($TestUrl -replace '/api$', '/api/agents/enroll')
            Write-LogInfo "Melde diesen Rechner am Server an (eigener Schluessel)..."
            try {
                $EnrollBody = @{
                    hostname    = $env:COMPUTERNAME
                    device_type = "windows_agent"
                } | ConvertTo-Json -Compress

                $EnrollResult = Invoke-RestMethod -Uri $EnrollUrl -Method POST -TimeoutSec 20 `
                    -Headers @{ "Authorization" = "Bearer $AgentToken"; "Content-Type" = "application/json" } `
                    -Body $EnrollBody

                if ($EnrollResult.token) {
                    $AgentToken = $EnrollResult.token
                    Write-LogSuccess "Eigener Geraeteschluessel erhalten - der mitgegebene wird nicht gespeichert."
                } else {
                    Write-LogWarn "Der Server hat geantwortet, aber keinen Schluessel geschickt. Nehme den mitgegebenen."
                }
            } catch {
                $StatusCode = $null
                if ($_.Exception.Response) { $StatusCode = [int]$_.Exception.Response.StatusCode }
                if ($StatusCode -eq 401 -or $StatusCode -eq 403) {
                    Write-LogError "Der Server hat den Schluessel abgelehnt. Stimmt er noch, oder ist die Einladung abgelaufen?"
                    exit 1
                } elseif ($StatusCode -eq 404) {
                    Write-LogWarn "Der Server kennt die Anmeldung noch nicht (aeltere Fassung). Nehme den mitgegebenen Schluessel."
                } else {
                    Write-LogWarn "Anmeldung nicht moeglich ($_). Nehme den mitgegebenen Schluessel."
                }
            }
        }

        # HTTPS-Modus: ALLE Logs (Default)
        $LogSources = @("*")
        Write-LogInfo "HTTPS-Modus: Alle Event Logs werden gesammelt"

    } else {
        # Syslog-Modus: Log-Auswahl wie bisher
        Write-Host ""
        Write-Host "Welche Event Logs sollen weitergeleitet werden?"
        Write-Host "  1) Application + System (Standard)"
        Write-Host "  2) Application + System + Security"
        Write-Host "  3) Nur Application"
        $LogChoice = Read-Answer -Prompt "Auswahl" -Default "1"

        $LogSources = switch ($LogChoice) {
            "2" { @("Application", "System", "Security") }
            "3" { @("Application") }
            default { @("Application", "System") }
        }
    }

    # Min-Level - per Parameter vorgebbar, sonst gefragt.
    if ([string]::IsNullOrWhiteSpace($MinLevel)) {
        Write-Host ""
        Write-Host "Minimales Log-Level:"
        Write-Host "  1) Alle (info und hoeher)"
        Write-Host "  2) Nur Warnungen (warning und hoeher)"
        Write-Host "  3) Nur Fehler (error und hoeher)"
        $LevelChoice = Read-Answer -Prompt "Auswahl" -Default "1"

        $MinLevel = switch ($LevelChoice) {
            "2" { "warning" }
            "3" { "error" }
            default { "info" }
        }
    }
    Write-LogInfo "Log-Level: $MinLevel"

    # --- Agent Script speichern ---
    if ($Mode -eq "https") {
        $AgentScriptHttps | Set-Content "$INSTALL_DIR\$SCRIPT_NAME" -Encoding UTF8
    } else {
        $AgentScriptSyslog | Set-Content "$INSTALL_DIR\$SCRIPT_NAME" -Encoding UTF8
    }

    # --- Konfiguration speichern ---
    $Config = @{
        mode = $Mode
        server_fqdn = $ServerFqdn
        server_ip = $ServerIP
        server_port = $ServerPort
        hostname = $env:COMPUTERNAME
        log_sources = $LogSources
        min_level = $MinLevel
    }

    if ($Mode -eq "https") {
        $Config.agent_token = $AgentToken
        $Config.skip_tls_verify = $SkipTlsVerify
    }

    $Config | ConvertTo-Json | Set-Content "$INSTALL_DIR\config.json" -Encoding UTF8

    Write-LogSuccess "Agent installiert: $INSTALL_DIR (Modus: $Mode)"
}

function Install-ScheduledTask {
    Write-LogInfo "Erstelle Scheduled Task..."

    $Existing = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($Existing) {
        Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
    }

    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$INSTALL_DIR\$SCRIPT_NAME`"" `
        -WorkingDirectory $INSTALL_DIR

    $Trigger = New-ScheduledTaskTrigger -AtStartup

    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    # Ohne Zeitlimit und mit vielen Neustarts: frueher beendete die
    # Aufgabenplanung den Agent nach 365 Tagen Laufzeit, und nach drei
    # Abstuerzen blieb er bis zum naechsten Neustart des Rechners aus - beides
    # still, ohne dass es jemand merkt.

    $Principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $TASK_NAME `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "RIMPERIO Agent v$AGENT_VERSION - Windows Event Log Forwarder" | Out-Null

    Write-LogSuccess "Scheduled Task erstellt"
}

function Start-Agent {
    Write-LogInfo "Starte Agent..."
    Start-ScheduledTask -TaskName $TASK_NAME

    Start-Sleep -Seconds 3

    $Task = Get-ScheduledTask -TaskName $TASK_NAME
    if ($Task.State -eq "Running") {
        Write-LogSuccess "Agent laeuft"
    } else {
        Write-LogWarn "Agent-Status: $($Task.State)"
    }
}

# ==============================================================================
# Test
# ==============================================================================

function Send-TestMessage {
    Write-LogInfo "Sende Test-Nachrichten..."

    $ConfigFile = "$INSTALL_DIR\config.json"
    if (-not (Test-Path $ConfigFile)) {
        Write-LogError "Agent nicht installiert!"
        exit 1
    }

    $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    $Hostname = $env:COMPUTERNAME

    if ($Config.mode -eq "https") {
        # HTTPS-Test
        $Headers = @{
            "Authorization" = "Bearer $($Config.agent_token)"
            "Content-Type" = "application/json"
        }

        $TlsParam = @{}
        if ($Config.skip_tls_verify) {
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
            if ($PSVersionTable.PSVersion.Major -ge 6) {
                $TlsParam = @{ SkipCertificateCheck = $true }
            }
        }

        # Server-URL ermitteln
        $BaseUrl = $null
        if ($Config.server_fqdn) {
            try {
                [System.Net.Dns]::GetHostAddresses($Config.server_fqdn) | Out-Null
                $BaseUrl = if ($Config.server_port -eq 443) { "https://$($Config.server_fqdn)" } else { "https://$($Config.server_fqdn):$($Config.server_port)" }
            } catch {}
        }
        if (-not $BaseUrl -and $Config.server_ip) {
            $BaseUrl = if ($Config.server_port -eq 443) { "https://$($Config.server_ip)" } else { "https://$($Config.server_ip):$($Config.server_port)" }
        }

        $body = @{
            hostname = $Hostname
            device_type = "windows_agent"
            events = @(
                @{ level = "info"; source = "rimperio-test"; message = "RIMPERIO Agent Test - Info Nachricht" },
                @{ level = "warning"; source = "rimperio-test"; message = "RIMPERIO Agent Test - Warning Nachricht" },
                @{ level = "error"; source = "rimperio-test"; message = "RIMPERIO Agent Test - Error Nachricht" }
            )
        } | ConvertTo-Json -Depth 3

        try {
            $response = Invoke-RestMethod -Uri "$BaseUrl/api/agents/ingest" -Method POST -Headers $Headers -Body $body @TlsParam
            Write-LogSuccess "3 Test-Nachrichten gesendet via HTTPS (akzeptiert: $($response.accepted))"
        } catch {
            Write-LogError "HTTPS-Test fehlgeschlagen: $_"
        }
    } else {
        # Syslog-Test (UDP)
        $UdpClient = New-Object System.Net.Sockets.UdpClient
        $Target = if ($Config.server_fqdn) { $Config.server_fqdn } else { $Config.server_ip }

        $Messages = @(
            @{ Level = 6; Text = "RIMPERIO Agent Test - Info Nachricht" },
            @{ Level = 4; Text = "RIMPERIO Agent Test - Warning Nachricht" },
            @{ Level = 3; Text = "RIMPERIO Agent Test - Error Nachricht" }
        )

        foreach ($Msg in $Messages) {
            $Priority = 8 + $Msg.Level
            $Timestamp = Get-Date -Format "MMM dd HH:mm:ss"
            $SyslogMsg = "<$Priority>$Timestamp $Hostname rimperio-test: $($Msg.Text)"
            try {
                $Bytes = [System.Text.Encoding]::UTF8.GetBytes($SyslogMsg)
                $UdpClient.Send($Bytes, $Bytes.Length, $Target, $Config.server_port) | Out-Null
                Write-Host "[SENT] $($Msg.Text)" -ForegroundColor Green
            } catch {
                Write-Host "[ERROR] $($Msg.Text): $_" -ForegroundColor Red
            }
        }
        $UdpClient.Close()
        Write-LogSuccess "Test abgeschlossen"
    }

    Write-LogInfo "Pruefe RIMPERIO Web-Interface ob die Nachrichten angekommen sind"
}

# ==============================================================================
# Deinstallation
# ==============================================================================

function Stop-AgentProcesses {
    Write-LogInfo "Pruefe laufende Agent-Prozesse..."
    try {
        $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape("$INSTALL_DIR\$SCRIPT_NAME") }

        foreach ($proc in $processes) {
            Write-LogInfo "Beende Prozess PID $($proc.ProcessId)"
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }

        if (-not $processes) {
            Write-LogInfo "Keine laufenden Agent-Prozesse gefunden"
        }
    } catch {
        Write-LogWarn "Konnte Prozesse nicht ermitteln/stoppen: $_"
    }
}

function Remove-ScheduledTaskForce {
    Write-LogInfo "Entferne Scheduled Task..."

    # Versuche regulär über ScheduledTasks-Modul
    $task = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($task) {
        try { Disable-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue | Out-Null } catch {}
        try { Stop-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue } catch {}
        try {
            Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
            Write-LogInfo "Scheduled Task entfernt (Unregister-ScheduledTask)"
        } catch {
            Write-LogWarn "Unregister-ScheduledTask fehlgeschlagen: $_"
        }
    } else {
        Write-LogInfo "Kein Scheduled Task gefunden (Get-ScheduledTask)"
    }

    # Fallback via schtasks (deckt evtl. Ghost-Eintraege ab)
    foreach ($tn in @($TASK_NAME, "\$TASK_NAME")) {
        try {
            schtasks.exe /Delete /TN $tn /F 2>$null | Out-Null
        } catch {}
    }
}

# Meldet den Agent auf dem Server ab, damit dort nicht ewig ein Geraet steht,
# das es gar nicht mehr gibt. Muss VOR dem Loeschen der Dateien laufen - danach
# sind Token und Geraetenummer weg.
function Remove-ServerEntry {
    $ConfigFile = "$INSTALL_DIR\config.json"
    if (-not (Test-Path $ConfigFile)) {
        Write-LogInfo "Keine Konfiguration gefunden - auf dem Server wird nichts abgeraeumt."
        return
    }

    try {
        $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    } catch {
        Write-LogWarn "Konfiguration unlesbar - auf dem Server wird nichts abgeraeumt."
        return
    }

    $ApiToken = $Config.agent_token
    if ([string]::IsNullOrWhiteSpace($ApiToken)) {
        Write-LogInfo "Kein Token hinterlegt (Syslog-Modus) - auf dem Server gibt es nichts abzumelden."
        return
    }

    # Ziel bestimmen: FQDN, sonst IP.
    $BaseUrl = $null
    if ($Config.server_fqdn) {
        $BaseUrl = if ($Config.server_port -eq 443) { "https://$($Config.server_fqdn)" }
                   else { "https://$($Config.server_fqdn):$($Config.server_port)" }
    } elseif ($Config.server_ip) {
        $BaseUrl = if ($Config.server_port -eq 443) { "https://$($Config.server_ip)" }
                   else { "https://$($Config.server_ip):$($Config.server_port)" }
    }
    if (-not $BaseUrl) {
        Write-LogWarn "Kein Serverziel in der Konfiguration - auf dem Server wird nichts abgeraeumt."
        return
    }

    if ($Config.skip_tls_verify) {
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    }

    # Die Geraetenummer hat der Server beim ersten Senden zurueckgemeldet. Mit ihr
    # trifft das Abraeumen genau diesen Rechner - und nicht einen gleichnamigen.
    $AgentIdFile = "$INSTALL_DIR\agent_id"
    $Payload = @{
        hostname         = $env:COMPUTERNAME
        purge            = $true
        all_for_hostname = $true
    }
    if (Test-Path $AgentIdFile) {
        $StoredId = (Get-Content $AgentIdFile -Raw).Trim()
        if ($StoredId -match '^\d+$') { $Payload.agent_id = [int]$StoredId }
    }
    try {
        $Ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
               Where-Object { $_.IPAddress -notlike "127.*" } |
               Select-Object -First 1).IPAddress
        if ($Ip) { $Payload.ip_address = $Ip }
        $Mac = (Get-NetAdapter -ErrorAction SilentlyContinue |
                Where-Object { $_.Status -eq "Up" } |
                Select-Object -First 1).MacAddress
        if ($Mac) { $Payload.mac_address = $Mac.Replace("-", ":").ToLower() }
    } catch {
        # Ohne IP/MAC greift die Zuordnung ueber Nummer bzw. Hostname.
    }

    Write-LogInfo "Melde den Agent auf dem Server ab ($BaseUrl)..."
    try {
        $Response = Invoke-RestMethod -Uri "$BaseUrl/api/agents/decommission" `
            -Method POST `
            -Headers @{ "Authorization" = "Bearer $ApiToken"; "Content-Type" = "application/json" } `
            -Body ($Payload | ConvertTo-Json -Compress) `
            -TimeoutSec 20
        Write-LogSuccess "Auf dem Server abgeraeumt: $($Response.message)"
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        if ($code -eq 404) {
            Write-LogInfo "Auf dem Server gab es dazu keinen Eintrag (mehr)."
        } else {
            Write-LogWarn "Der Server war nicht erreichbar oder hat abgelehnt - der Eintrag bleibt stehen."
            Write-LogInfo  "Im Web-UI unter 'Geraete' von Hand loeschen."
        }
    }
}

function Uninstall-Agent {
    Write-LogInfo "Deinstalliere RIMPERIO Agent..."

    $taskFile = Join-Path $env:SystemRoot "System32\\Tasks\\$TASK_NAME"
    $errors = @()

    # Zuerst auf dem Server abmelden - danach sind Token und Nummer weg.
    $DoPurge = $PurgeServer.IsPresent
    if (-not $DoPurge -and -not $script:Unattended) {
        $answer = Read-Answer -Prompt "Auch den Eintrag samt Logs auf dem Server loeschen? (j/n)" -Default "n"
        $DoPurge = $answer -match '^(j|J|y|Y)'
    }
    if ($DoPurge) { Remove-ServerEntry }

    # Laufende Agent-Instanzen beenden (vor Filesystem-Delete)
    Stop-AgentProcesses

    Remove-ScheduledTaskForce

    # Falls Task-Datei liegen bleibt, manuell loeschen
    if (Test-Path $taskFile) {
        try {
            Remove-Item -Path $taskFile -Force
            Write-LogInfo "Task-Datei aus Aufgabenplaner entfernt"
        } catch {
            Write-LogWarn "Konnte Task-Datei nicht entfernen: $_"
            $errors += "Task-Datei"
        }
    }

    # Mehrere moegliche Installationspfade (Altlasten)
    $installPaths = @(
        $INSTALL_DIR,
        "$env:ProgramData\\RIMPERIOAgent",
        "$env:ProgramFiles\\RIMPERIO-Agent",
        "$env:ProgramFiles(x86)\\RIMPERIO-Agent"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

    if ($installPaths.Count -eq 0) {
        Write-LogInfo "Keine Installationsverzeichnisse gefunden"
    }

    foreach ($path in $installPaths) {
        try {
            Remove-Item -Path $path -Recurse -Force
            Write-LogInfo "Verzeichnis entfernt: $path"
        } catch {
            Write-LogWarn "Konnte Verzeichnis nicht entfernen: $path - $_"
            $errors += "Verzeichnis $path"
        }
    }

    # Abschlusspruefung
    $stillTask = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    $stillFiles = Test-Path $INSTALL_DIR
    $stillTaskFile = Test-Path $taskFile

    if ($stillTask -or $stillFiles -or $stillTaskFile -or $errors.Count -gt 0) {
        Write-LogWarn "Deinstallation unvollstaendig. Offene Reste: $($errors -join ', ')"
        if ($stillTask) { Write-LogWarn "Aufgabenplaner-Eintrag existiert noch." }
        if ($stillTaskFile) { Write-LogWarn "Task-Datei unter $taskFile existiert noch." }
        if ($stillFiles) { Write-LogWarn "Verzeichnis $INSTALL_DIR existiert noch." }
        return
    }

    Write-LogSuccess "RIMPERIO Agent vollstaendig deinstalliert"
}

# ==============================================================================
# Zusammenfassung
# ==============================================================================

function Show-Summary {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "  RIMPERIO Agent v$AGENT_VERSION installiert!" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Installation: $INSTALL_DIR"
    Write-Host ""
    Write-Host "Nuetzliche Befehle (PowerShell als Admin):"
    Write-Host "  Get-ScheduledTask -TaskName $TASK_NAME        # Status"
    Write-Host "  Start-ScheduledTask -TaskName $TASK_NAME      # Starten"
    Write-Host "  Stop-ScheduledTask -TaskName $TASK_NAME       # Stoppen"
    Write-Host ""
    Write-Host "Test:"
    Write-Host "  Installer starten und Option 2 waehlen"
    Write-Host ""
    Write-Host "Deinstallation:"
    Write-Host "  Installer starten und Option 3 waehlen"
    Write-Host ""
}

# ==============================================================================
# Menue
# ==============================================================================

function Show-MainMenu {
    Write-Host ""
    Write-Host "Was moechtest du tun?"
    Write-Host "  1) Installieren / Neu installieren"
    Write-Host "  2) Testnachrichten senden (Installation erforderlich)"
    Write-Host "  3) Vollstaendig deinstallieren (alles ausser diesem Script wird entfernt)"
    Write-Host "  4) Abbrechen"
    $choice = Read-Answer -Prompt "Auswahl" -Default "1"

    switch ($choice) {
        "2" { return "test" }
        "3" { return "uninstall" }
        "4" { return "exit" }
        default { return "install" }
    }
}

# ==============================================================================
# Hauptprogramm
# ==============================================================================

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  RIMPERIO Agent v$AGENT_VERSION - Windows" -ForegroundColor Cyan
Write-Host "  (PowerShell Event Log Forwarder)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# Ohne -Action gibt es das Menue wie bisher. Mit -Action laeuft es direkt durch -
# das ist der Weg fuer den Einzeiler und fuer die Verteilung per Softwarepaket.
if ([string]::IsNullOrWhiteSpace($Action) -or $Action -eq "menu") {
    $Action = Show-MainMenu
}

switch ($Action) {
    "uninstall" { Uninstall-Agent }
    "test"      { Send-TestMessage }
    "install"   {
        Install-Agent
        Install-ScheduledTask
        Start-Agent
        Send-TestMessage
        Show-Summary
    }
    default { Write-LogInfo "Abgebrochen." }
}
