<#
  Datei:        install-agent.ps1
  Projekt:      RIMPERIO - Agent
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00

  Beschreibung: Schnelle Einrichtung eines Windows-Agents ohne MSI: laedt die
                (signierte) rimperio-agent.exe, legt sie nach "Program Files\RIMPERIO
                Agent" und richtet den Dienst ein. Muss als Administrator laufen.

  Aufruf (als Administrator):
    powershell -ExecutionPolicy Bypass -File install-agent.ps1 `
      -Server https://rmm.example.de -Secret GEHEIMNIS -Site Buero

  Oder als Einzeiler (Platzhalter ersetzen):
    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/installer/windows/install-agent.ps1))) `
      -Server https://rmm.example.de -Secret GEHEIMNIS
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Secret,
    [string]$Site = "Standard",
    [string]$Description = "",
    [string]$ExeUrl = "https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download/rimperio-agent.exe",
    [string]$InstallDir = "$env:ProgramFiles\RIMPERIO Agent",
    [switch]$Insecure
)

$ErrorActionPreference = "Stop"

# Administrator?
$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Bitte als Administrator ausfuehren." }

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$exe = Join-Path $InstallDir "rimperio-agent.exe"

Write-Host "Lade Agent von $ExeUrl ..."
Invoke-WebRequest -Uri $ExeUrl -OutFile $exe -UseBasicParsing

# Hinweis zur Signatur (nur informativ).
$sig = Get-AuthenticodeSignature $exe
if ($sig.Status -ne "Valid") {
    Write-Warning "Die heruntergeladene Datei ist nicht gueltig signiert (Status: $($sig.Status))."
    Write-Warning "Das kann von SmartScreen/Defender beanstandet werden - siehe installer/README.md."
} else {
    Write-Host "Signatur gueltig: $($sig.SignerCertificate.Subject)"
}

$arguments = @("setup", "-server", $Server, "-secret", $Secret, "-site", $Site)
if ($Description) { $arguments += @("-desc", $Description) }
if ($Insecure) { $arguments += "-insecure" }

Write-Host "Richte Dienst ein ..."
& $exe @arguments
if ($LASTEXITCODE -ne 0) { throw "Einrichtung schlug fehl ($LASTEXITCODE)." }

Write-Host "Fertig. Der RIMPERIO-Agent laeuft als Dienst 'rimperio-agent'."
