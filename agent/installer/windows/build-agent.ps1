<#
  Datei:        build-agent.ps1
  Projekt:      RIMPERIO — Agent-Installer
  Autor:        Phydran6
  Version:      2026.11.26.20.00.00

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
$agentRoot = (Resolve-Path "$PSScriptRoot\..\..").Path   # agent/installer/windows -> agent
$out = "bin\rimperio-agent.exe"

# Produktversion aus der Datei VERSION (Semantic Versioning, z. B. 0.0.1).
$agentVersion = (Get-Content "$agentRoot\..\VERSION" -Raw).Trim()

Push-Location $agentRoot
try {
    # Versions-Ressource erzeugen (optional, aber empfohlen).
    if (Get-Command goversioninfo -ErrorAction SilentlyContinue) {
        $icoPath = Join-Path $PSScriptRoot "rimperio.ico"
        Push-Location "cmd\rimperio-agent"
        try {
            # Version aus der VERSION-Datei in die Ressource schreiben — so tragen
            # exe und Release immer dieselbe Version.
            #   SemVer 1.2.3[-rc.1]   -> 1.2.3.0 (Vorab-Kennung passt nicht in die Ressource)
            #   alt JJJJ.MM.TT.HH.MM… -> JJJJ.MM.TT.HHMM
            $ver = $agentVersion
            $p = ($ver -replace '[-+].*$', '').Split('.') | ForEach-Object { [int]$_ }
            if ($p.Count -ge 6) {
                $fixed = [ordered]@{ Major = $p[0]; Minor = $p[1]; Patch = $p[2]; Build = $p[3] * 100 + $p[4] }
            } else {
                $fixed = [ordered]@{ Major = $p[0]; Minor = $p[1]; Patch = $p[2]; Build = 0 }
            }
            $vi = Get-Content versioninfo.json -Raw | ConvertFrom-Json
            $vi.FixedFileInfo.FileVersion = $fixed
            $vi.FixedFileInfo.ProductVersion = $fixed
            # Text-Version = numerische Version (Windows zeigt beide; SignPath prüft sie).
            $verText = "{0}.{1}.{2}.{3}" -f $fixed.Major, $fixed.Minor, $fixed.Patch, $fixed.Build
            $vi.StringFileInfo.ProductVersion = $verText
            $vi.StringFileInfo | Add-Member -NotePropertyName FileVersion -NotePropertyValue $verText -Force
            $viBuild = "versioninfo.build.json"
            # UTF-8 ohne BOM — Go's JSON-Parser lehnt ein BOM ab (PowerShell 5.1 schreibt sonst eins).
            [IO.File]::WriteAllText((Join-Path (Get-Location) $viBuild), ($vi | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))

            if (Test-Path $icoPath) {
                goversioninfo -64 -icon "$icoPath" -o resource.syso $viBuild
                Write-Host "Versions-Ressource ($ver) + Symbol eingebettet."
            } else {
                goversioninfo -64 -o resource.syso $viBuild
                Write-Host "Versions-Ressource ($ver) eingebettet (Symbol fehlt: installer/windows/rimperio.ico)."
            }
            if ($LASTEXITCODE -ne 0) { throw "goversioninfo schlug fehl." }
        }
        finally {
            Remove-Item "versioninfo.build.json" -ErrorAction SilentlyContinue
            Pop-Location
        }
    } else {
        Write-Warning "goversioninfo nicht gefunden — exe ohne Versions-Ressource."
        Write-Warning "Installieren: go install github.com/josephspurrier/goversioninfo/cmd/goversioninfo@latest"
    }

    $env:GOOS = "windows"; $env:GOARCH = $Arch; $env:CGO_ENABLED = "0"
    New-Item -ItemType Directory -Force -Path "bin" | Out-Null
    Write-Host "Baue $out ($Arch) …"
    # Dieselbe Nummer auch im Programm selbst (rimperio-agent version, Check-in).
    go build -trimpath -ldflags "-s -w -X github.com/Phydran6/RIMPERIO-Server/agent/internal/core.Version=$agentVersion" -o $out ./cmd/rimperio-agent
    if ($LASTEXITCODE -ne 0) { throw "go build schlug fehl." }
}
finally { Pop-Location }

$exePath = Join-Path $agentRoot $out
Write-Host "Fertig: $exePath"

if ($Sign) { & "$PSScriptRoot\sign.ps1" -File $exePath }
