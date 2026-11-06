<#
  Datei:        sign.ps1
  Projekt:      RIMPERIO — Agent-Installer
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00

  Beschreibung: Signiert eine Datei (.exe/.msi) mit Authenticode. Code Signing
                ist der EINZIGE seriöse Weg, damit SmartScreen und Virenscanner
                einem Programm vertrauen — es gibt keine Abkürzung und keine
                "Tarnung". Das Skript kennt drei Wege:

                  azure : Azure Trusted Signing (günstig, von Microsoft betrieben)
                  pfx   : klassisches Zertifikat als .pfx-Datei
                  none  : überspringen (Datei bleibt unsigniert)

                Welcher Weg, wofür, und warum "von jedem AV als OK" niemand
                garantieren kann: siehe installer/README.md.

  Aufruf:       pwsh ./sign.ps1 -File ..\..\bin\rimperio-agent.exe
  Steuerung:    über Umgebungsvariablen (siehe unten) — nichts im Klartext im Repo.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$File,
    [string]$Method = $env:RIMPERIO_SIGN_METHOD,
    [string]$TimestampUrl = $(if ($env:RIMPERIO_TS_URL) { $env:RIMPERIO_TS_URL } else { "http://timestamp.digicert.com" })
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $File)) { throw "Datei nicht gefunden: $File" }
if ([string]::IsNullOrWhiteSpace($Method)) { $Method = "none" }

function Resolve-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "${env:ProgramFiles}\Windows Kits\10\bin")
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $found = Get-ChildItem -Path $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "x64" } | Sort-Object FullName -Descending | Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }
    throw "signtool.exe nicht gefunden. Windows SDK installieren oder in PATH legen."
}

switch ($Method.ToLower()) {
    "none" {
        Write-Warning "RIMPERIO_SIGN_METHOD nicht gesetzt — '$File' bleibt UNSIGNIERT."
        Write-Warning "Unsignierte RMM-Agents werden von SmartScreen/Defender fast sicher beanstandet."
        Write-Warning "Richte 'azure' oder 'pfx' ein — siehe installer/README.md."
        exit 0
    }

    "pfx" {
        $signtool = Resolve-SignTool
        if (-not $env:RIMPERIO_PFX_PATH) { throw "RIMPERIO_PFX_PATH (Pfad zur .pfx) fehlt." }
        $pfxPass = $env:RIMPERIO_PFX_PASSWORD
        Write-Host "Signiere mit PFX-Zertifikat: $File"
        $args = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/f", $env:RIMPERIO_PFX_PATH)
        if ($pfxPass) { $args += @("/p", $pfxPass) }
        $args += $File
        & $signtool @args
        if ($LASTEXITCODE -ne 0) { throw "signtool (pfx) schlug fehl ($LASTEXITCODE)." }
    }

    "azure" {
        # Azure Trusted Signing: signtool + von Microsoft bereitgestelltes dlib.
        # Nötige Variablen: RIMPERIO_AZURE_DLIB (Pfad zur Azure.CodeSigning.Dlib.dll),
        # RIMPERIO_AZURE_METADATA (Pfad zur metadata.json mit Endpoint/Account/Profil).
        $signtool = Resolve-SignTool
        if (-not $env:RIMPERIO_AZURE_DLIB) { throw "RIMPERIO_AZURE_DLIB fehlt." }
        if (-not $env:RIMPERIO_AZURE_METADATA) { throw "RIMPERIO_AZURE_METADATA fehlt." }
        $ts = $(if ($env:RIMPERIO_TS_URL) { $env:RIMPERIO_TS_URL } else { "http://timestamp.acs.microsoft.com" })
        Write-Host "Signiere mit Azure Trusted Signing: $File"
        & $signtool sign /v /fd SHA256 /tr $ts /td SHA256 `
            /dlib $env:RIMPERIO_AZURE_DLIB /dmdf $env:RIMPERIO_AZURE_METADATA $File
        if ($LASTEXITCODE -ne 0) { throw "signtool (azure) schlug fehl ($LASTEXITCODE)." }
    }

    default { throw "Unbekannte Signier-Methode: '$Method' (azure | pfx | none)." }
}

# Ergebnis prüfen.
$signtool = Resolve-SignTool
& $signtool verify /pa /v $File
if ($LASTEXITCODE -ne 0) { throw "Signaturprüfung fehlgeschlagen." }
Write-Host "Signatur OK: $File"
