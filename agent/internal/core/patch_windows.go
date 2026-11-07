//go:build windows

// ---------------------------------------------------------------------------
// Datei:        patch_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Patchmanagement unter Windows über den eingebauten Windows
//               Update Agent (COM: Microsoft.Update.Session) via PowerShell —
//               ohne Zusatzmodule. Die Suche liefert Klassifizierung (über die
//               festen WSUS-Kennungen, damit ein deutsches Windows nicht
//               „Sicherheitsupdates“ statt „Security Updates“ meldet), KB,
//               CVEs, Veröffentlichung und Neustartverhalten. Installiert
//               werden nur die übergebenen Update-IDs.
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Gemeinsamer Kopf: Klassifizierungen über die festen Kennungen übersetzen.
const winClassMap = `
$classMap = @{
  '0fa1201d-4330-4fa8-8ae9-b877473b6441' = 'Security Updates'
  'e6cf1350-c01b-414d-a61f-263d14d133b4' = 'Critical Updates'
  'e0789628-ce08-4437-be74-2495b842f43b' = 'Definition Updates'
  'ebfc1fc5-71a4-4f7b-9aca-3b9a503104a0' = 'Drivers'
  'b54e7d24-7add-428f-8b75-90a396fa584f' = 'Feature Packs'
  '68c5b0a3-d1a6-4553-ae49-01d3a7827828' = 'Service Packs'
  'b4832bd8-e735-4761-8daf-37f882276dab' = 'Tools'
  '28bc880e-0592-4cbf-8f95-c79b17911d5f' = 'Update Rollups'
  'cd5ffd1e-e932-4e3a-bf74-18bf0b1bbd83' = 'Updates'
  '3689bdc8-b205-4af4-8d4a-a63924c5e9d5' = 'Upgrades'
}
function Test-RebootPending {
  if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { return $true }
  if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { return $true }
  return $false
}
`

const winScanScript = winClassMap + `
$ErrorActionPreference = 'Stop'
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
$updates = @()
foreach ($u in $result.Updates) {
  $class = ''; $products = @()
  foreach ($c in $u.Categories) {
    if ($c.Type -eq 'UpdateClassification') {
      $id = [string]$c.CategoryID
      if ($classMap.ContainsKey($id)) { $class = $classMap[$id] } else { $class = [string]$c.Name }
    } elseif ($c.Type -eq 'Product') { $products += [string]$c.Name }
  }
  $kbs = @(); foreach ($k in $u.KBArticleIDs) { $kbs += ('KB' + [string]$k) }
  $cves = @(); foreach ($c in $u.CveIDs) { $cves += [string]$c }
  $released = ''
  try { $released = ([datetime]$u.LastDeploymentChangeTime).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } catch {}
  $reboot = $false
  try { $reboot = ([int]$u.InstallationBehavior.RebootBehavior -ne 0) } catch {}
  $sev = [string]$u.MsrcSeverity
  $updates += [pscustomobject]@{
    id         = [string]$u.Identity.UpdateID
    title      = [string]$u.Title
    severity   = $sev
    kb         = ($kbs -join ',')
    size       = [string]$u.MaxDownloadSize
    category   = $class
    products   = $products
    cves       = $cves
    reboot     = $reboot
    released   = $released
    kind       = $(if ([int]$u.Type -eq 2) { 'driver' } else { 'software' })
    url        = [string]$u.SupportUrl
    downloaded = [bool]$u.IsDownloaded
    security   = ($class -eq 'Security Updates' -or $class -eq 'Critical Updates' -or $sev -ne '')
  }
}
[pscustomobject]@{ manager = 'windows_update'; updates = @($updates); reboot_pending = (Test-RebootPending) } | ConvertTo-Json -Compress -Depth 5
`

const winApplyScript = winClassMap + `
$ErrorActionPreference = 'Stop'
$want = @(%s)
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
$coll = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) {
  if ($want.Count -eq 0 -or $want -contains [string]$u.Identity.UpdateID) {
    if (-not $u.EulaAccepted) { $u.AcceptEula() }
    [void]$coll.Add($u)
  }
}
$out = [ordered]@{ installed = @(); failed = @(); reboot_required = $false; message = '' }
if ($coll.Count -eq 0) {
  $out.message = 'Keine der freigegebenen Updates ist noch offen.'
  $out.reboot_required = (Test-RebootPending)
  [pscustomobject]$out | ConvertTo-Json -Compress -Depth 5
  exit 0
}
$dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; [void]$dl.Download()
$inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
$r = $inst.Install()
for ($i = 0; $i -lt $coll.Count; $i++) {
  $u = $coll.Item($i); $ur = $r.GetUpdateResult($i)
  if ([int]$ur.ResultCode -eq 2 -or [int]$ur.ResultCode -eq 3) { $out.installed += [string]$u.Identity.UpdateID }
  else {
    $out.failed += [pscustomobject]@{ id = [string]$u.Identity.UpdateID; title = [string]$u.Title;
      error = ('Ergebnis {0}, HRESULT 0x{1:X8}' -f [int]$ur.ResultCode, [int]$ur.HResult) }
  }
}
$out.reboot_required = ([bool]$r.RebootRequired -or (Test-RebootPending))
$out.message = ('{0} installiert, {1} fehlgeschlagen.' -f $out.installed.Count, $out.failed.Count)
[pscustomobject]$out | ConvertTo-Json -Compress -Depth 5
`

func runPS(script string, timeout int) (string, error) {
	r := Execute(PendingCommand{Shell: "powershell", Command: script, Timeout: timeout})
	if r.ExitCode == nil || *r.ExitCode != 0 {
		return r.Stdout, fmt.Errorf("PowerShell: %s", strings.TrimSpace(r.Stderr))
	}
	return strings.TrimSpace(r.Stdout), nil
}

func patchScan() (PatchScanResult, error) {
	var res PatchScanResult
	out, err := runPS(winScanScript, 1200)
	if err != nil {
		return res, fmt.Errorf("Update-Suche fehlgeschlagen: %v", err)
	}
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		return res, fmt.Errorf("Antwort unlesbar: %v", err)
	}
	return res, nil
}

func patchApply(ids []string) (*PatchApplyResult, error) {
	quoted := make([]string, 0, len(ids))
	for _, id := range ids {
		quoted = append(quoted, "'"+strings.ReplaceAll(id, "'", "")+"'")
	}
	out, err := runPS(fmt.Sprintf(winApplyScript, strings.Join(quoted, ",")), 14400)
	res := &PatchApplyResult{}
	if perr := json.Unmarshal([]byte(out), res); perr != nil {
		if err == nil {
			err = fmt.Errorf("Antwort unlesbar: %v", perr)
		}
		res.Log = truncate(out, 100_000)
		return res, err
	}
	if err == nil && len(res.Failed) > 0 && len(res.Installed) == 0 {
		err = fmt.Errorf("kein Update installiert: %s", describeFailures(res.Failed))
	}
	return res, err
}

func rebootPending() bool {
	out, err := runPS(winClassMap+"\nif (Test-RebootPending) { 'yes' } else { 'no' }", 60)
	return err == nil && strings.Contains(out, "yes")
}

func scheduleReboot(delay int) error {
	if delay < 30 {
		delay = 60
	}
	r := Execute(PendingCommand{Shell: "cmd",
		Command: fmt.Sprintf(`shutdown.exe /r /t %d /c "RIMPERIO: Neustart nach Updates" /d p:2:17`, delay),
		Timeout: 60})
	if r.ExitCode != nil && *r.ExitCode != 0 {
		return fmt.Errorf("shutdown.exe: %s", strings.TrimSpace(r.Stdout+r.Stderr))
	}
	return nil
}
