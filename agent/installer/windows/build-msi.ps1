<#
  Datei:        build-msi.ps1
  Projekt:      RIMPERIO — Agent-Installer
  Autor:        Phydran6
  Version:      2026.11.26.20.00.00

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

# MSI-Version aus der VERSION-Datei ableiten. MSI kennt nur Zahlen
# (Feld 1 und 2: 0..255, Feld 3: 0..65535):
#   SemVer 1.2.3[-rc.1]        -> 1.2.3
#   alt JJJJ.MM.TT.HH.MM.SS    -> JJ.MM.TT
# Der Wechsel 26.11.x -> 0.0.1 ist fuer Windows Installer ein Rueckschritt;
# darum erlaubt die .wxs das Ersetzen auch einer "hoeheren" Version.
if (-not $Version) {
    $v = (Get-Content "$here\..\..\..\VERSION" -Raw).Trim()
    $p = ($v -replace '[-+].*$', '').Split('.')
    if ($p.Count -ge 6) {
        $Version = ("{0}.{1}.{2}" -f ($p[0].Substring(2)), [int]$p[1], [int]$p[2])
    } else {
        if ([int]$p[0] -gt 255 -or [int]$p[1] -gt 255 -or [int]$p[2] -gt 65535) {
            throw "VERSION $v passt nicht in eine MSI-Version (max. 255.255.65535)."
        }
        $Version = ("{0}.{1}.{2}" -f [int]$p[0], [int]$p[1], [int]$p[2])
    }
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
