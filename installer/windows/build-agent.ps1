<#
  Datei:        build-agent.ps1
  Projekt:      RIMPERIO — Agent-Installer
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00

  Beschreibung: Baut die Agent-.exe für Windows — mit Versions-Ressource
                (goversioninfo) und schlankem, getrimmtem Binary. Optional wird
                die fertige .exe gleich signiert (-Sign).

  Aufruf:       pwsh ./build-agent.ps1            # nur bauen
                pwsh ./build-agent.ps1 -Sign      # bauen + signieren
#>
[CmdletBinding()]
param(
    [string]$Arch = "amd64",
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
$agentRoot = (Resolve-Path "$PSScriptRoot\..\..\agent").Path
$out = "bin\rimperio-agent.exe"

Push-Location $agentRoot
try {
    # Versions-Ressource erzeugen (optional, aber empfohlen).
    if (Get-Command goversioninfo -ErrorAction SilentlyContinue) {
        $icoPath = Join-Path $PSScriptRoot "rimperio.ico"
        Push-Location "cmd\rimperio-agent"
        try {
            if (Test-Path $icoPath) {
                goversioninfo -64 -icon "$icoPath" -o resource.syso versioninfo.json
                Write-Host "Versions-Ressource + Symbol eingebettet."
            } else {
                goversioninfo -64 -o resource.syso versioninfo.json
                Write-Host "Versions-Ressource eingebettet (Symbol fehlt: installer/windows/rimperio.ico)."
            }
        }
        finally { Pop-Location }
    } else {
        Write-Warning "goversioninfo nicht gefunden — exe ohne Versions-Ressource."
        Write-Warning "Installieren: go install github.com/josephspurrier/goversioninfo/cmd/goversioninfo@latest"
    }

    $env:GOOS = "windows"; $env:GOARCH = $Arch; $env:CGO_ENABLED = "0"
    New-Item -ItemType Directory -Force -Path "bin" | Out-Null
    Write-Host "Baue $out ($Arch) …"
    go build -trimpath -ldflags "-s -w" -o $out ./cmd/rimperio-agent
    if ($LASTEXITCODE -ne 0) { throw "go build schlug fehl." }
}
finally { Pop-Location }

$exePath = Join-Path $agentRoot $out
Write-Host "Fertig: $exePath"

if ($Sign) { & "$PSScriptRoot\sign.ps1" -File $exePath }
