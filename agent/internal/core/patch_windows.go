//go:build windows

// ---------------------------------------------------------------------------
// Datei:        patch_windows.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Patchmanagement unter Windows über den eingebauten Windows
//               Update Agent (COM: Microsoft.Update.Session) via PowerShell —
//               ohne Zusatzmodule. Suchen listet offene Updates; Einspielen lädt
//               und installiert sie (ein Neustart kann nötig werden).
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"fmt"
	"strings"
)

const winScanScript = `
$ErrorActionPreference = 'Stop'
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
$updates = @()
foreach ($u in $result.Updates) {
  $updates += [pscustomobject]@{
    id       = $u.Identity.UpdateID
    title    = $u.Title
    severity = [string]$u.MsrcSeverity
    kb       = ($u.KBArticleIDs -join ',')
    size     = [string]$u.MaxDownloadSize
  }
}
$json = @($updates) | ConvertTo-Json -Compress
if ([string]::IsNullOrWhiteSpace($json)) { '[]' }
elseif ($json[0] -ne '[') { "[$json]" }
else { $json }
`

const winApplyScript = `
$ErrorActionPreference = 'Stop'
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
if ($result.Updates.Count -eq 0) { Write-Output 'Keine offenen Updates.'; exit 0 }
$coll = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) {
  if (-not $u.EulaAccepted) { $u.AcceptEula() }
  [void]$coll.Add($u)
}
$dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; [void]$dl.Download()
$inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
$r = $inst.Install()
Write-Output ("Installiert: {0}  Ergebnis: {1}  Neustart noetig: {2}" -f $coll.Count, $r.ResultCode, $r.RebootRequired)
`

func patchScan() ([]PatchUpdate, error) {
	r := Execute(PendingCommand{Shell: "powershell", Command: winScanScript, Timeout: 600})
	if r.ExitCode == nil || *r.ExitCode != 0 {
		return nil, fmt.Errorf("Update-Suche fehlgeschlagen: %s", strings.TrimSpace(r.Stderr))
	}
	out := strings.TrimSpace(r.Stdout)
	if out == "" {
		return []PatchUpdate{}, nil
	}
	var ups []PatchUpdate
	if err := json.Unmarshal([]byte(out), &ups); err != nil {
		return nil, fmt.Errorf("Antwort unlesbar: %v", err)
	}
	return ups, nil
}

func patchApply() (string, error) {
	r := Execute(PendingCommand{Shell: "powershell", Command: winApplyScript, Timeout: 7200})
	if r.ExitCode == nil || *r.ExitCode != 0 {
		return r.Stdout, fmt.Errorf("Installation fehlgeschlagen: %s", strings.TrimSpace(r.Stderr))
	}
	return strings.TrimSpace(r.Stdout), nil
}
