<#
  Datei:        build-msi.ps1
  Projekt:      RIMPERIO — Agent-Installer
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00

  Beschreibung: Packt die Agent-.exe in ein MSI (WiX v4). Reihenfolge beim
                Signieren: erst die .exe signieren, dann einpacken, dann das MSI
                signieren — so trägt beides eine gültige Signatur.

  Voraussetzung: WiX v4  ->  dotnet tool install --global wix
                 (und eine gebaute agent\bin\rimperio-agent.exe, siehe build-agent.ps1)

  Aufruf:        pwsh ./build-msi.ps1
                 pwsh ./build-msi.ps1 -Sign
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$exeSource = Resolve-Path "$here\..\..\bin\rimperio-agent.exe" -ErrorAction SilentlyContinue
if (-not $exeSource) { throw "agent\bin\rimperio-agent.exe fehlt. Zuerst build-agent.ps1 ausführen." }

if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
    throw "WiX v4 (wix) nicht gefunden. Installieren: dotnet tool install --global wix"
}

# MSI-Version aus der VERSION-Datei ableiten (YYYY.MM.DD… -> YY.MM.DD),
# denn MSI erlaubt im ersten Feld nur 0..255.
if (-not $Version) {
    $v = (Get-Content "$here\..\..\..\VERSION" -Raw).Trim()
    $p = $v.Split('.')
    $Version = ("{0}.{1}.{2}" -f ($p[0].Substring(2)), [int]$p[1], [int]$p[2])
}
Write-Host "MSI-Version: $Version"

# exe neben die .wxs legen (dort erwartet sie das Source-Attribut).
$exeLocal = Join-Path $here "rimperio-agent.exe"
Copy-Item $exeSource $exeLocal -Force

# 1) exe signieren (vor dem Einpacken).
if ($Sign) { & "$here\sign.ps1" -File $exeLocal }

# 2) MSI bauen.
Push-Location $here
try {
    wix build rimperio-agent.wxs -d Version=$Version -o rimperio-agent.msi
    if ($LASTEXITCODE -ne 0) { throw "wix build schlug fehl." }
}
finally { Pop-Location }

# 3) MSI signieren.
$msi = Join-Path $here "rimperio-agent.msi"
if ($Sign) { & "$here\sign.ps1" -File $msi }

Write-Host "Fertig: $msi"
