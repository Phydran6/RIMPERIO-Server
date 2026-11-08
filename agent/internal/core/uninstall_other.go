//go:build !windows && !linux

// ---------------------------------------------------------------------------
// Datei:        uninstall_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Deinstallation auf sonstigen Plattformen (macOS & Co.):
//               Dienst austragen, Konfiguration löschen, die Programmdatei nur
//               dann, wenn sie am üblichen Ort (/usr/local/bin) liegt.
// ---------------------------------------------------------------------------

package core

import (
	"os"
	"strings"
)

const installedBinary = "/usr/local/bin/rimperio-agent"

func platformUninstall(fromService bool) []string {
	var notes []string
	if !fromService {
		_ = ServiceControl("stop")
	}
	if err := RemoveConfig(); err != nil {
		notes = append(notes, err.Error())
	}
	if exe, err := os.Executable(); err == nil && exe == installedBinary {
		if err := os.Remove(exe); err != nil {
			notes = append(notes, "Programmdatei löschen: "+err.Error())
		}
	} else {
		notes = append(notes, "Die Programmdatei bleibt liegen: "+exe)
	}
	if err := ServiceControl("uninstall"); err != nil && !strings.Contains(err.Error(), "not installed") {
		notes = append(notes, "Dienst entfernen: "+err.Error())
	}
	return append(notes, "Agent entfernt.")
}
