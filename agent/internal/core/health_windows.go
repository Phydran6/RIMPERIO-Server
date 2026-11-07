//go:build windows

// ---------------------------------------------------------------------------
// Datei:        health_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Dienstzustand unter Windows über CIM: laufende Dienste und
//               solche mit Starttyp „Automatisch“, die nicht laufen.
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"strings"
)

const winServiceScript = `
$ErrorActionPreference = 'Stop'
$all = Get-CimInstance Win32_Service | Select-Object Name, State, StartMode
[pscustomobject]@{
  running      = @($all | Where-Object { $_.State -eq 'Running' } | ForEach-Object { $_.Name })
  auto_stopped = @($all | Where-Object { $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' } | ForEach-Object { $_.Name })
} | ConvertTo-Json -Compress
`

func serviceState() (running, autoStopped, failed []string) {
	r := Execute(PendingCommand{Shell: "powershell", Command: winServiceScript, Timeout: 120})
	var out struct {
		Running     []string `json:"running"`
		AutoStopped []string `json:"auto_stopped"`
	}
	if err := json.Unmarshal([]byte(strings.TrimSpace(r.Stdout)), &out); err == nil {
		running, autoStopped = out.Running, out.AutoStopped
	}
	return running, autoStopped, []string{}
}
