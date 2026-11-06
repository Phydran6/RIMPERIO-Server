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
$agentRoot = (Resolve-Path "$PSScriptRoot\..\..").Path   # agent/installer/windows -> agent
$out = "bin\rimperio-agent.exe"

Push-Location $agentRoot
try {
    # Versions-Ressource erzeugen (optional, aber empfohlen).
    if (Get-Command goversioninfo -ErrorAction SilentlyContinue) {
        $icoPath = Join-Path $PSScriptRoot "rimperio.ico"
        Push-Location "cmd\rimperio-agent"
        try {
            # Version aus der VERSION-Datei (YYYY.MM.DD.HH.MM.SS) in die Ressource
            # schreiben — so tragen exe und Release immer dieselbe Version.
            $ver = (Get-Content "$agentRoot\..\VERSION" -Raw).Trim()
            $p = $ver.Split('.') | ForEach-Object { [int]$_ }
            $fixed = [ordered]@{ Major = $p[0]; Minor = $p[1]; Patch = $p[2]; Build = $p[3] * 100 + $p[4] }
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
    go build -trimpath -ldflags "-s -w" -o $out ./cmd/rimperio-agent
    if ($LASTEXITCODE -ne 0) { throw "go build schlug fehl." }
}
finally { Pop-Location }

$exePath = Join-Path $agentRoot $out
Write-Host "Fertig: $exePath"

if ($Sign) { & "$PSScriptRoot\sign.ps1" -File $exePath }
