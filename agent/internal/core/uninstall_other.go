//go:build !windows

// ---------------------------------------------------------------------------
// Datei:        uninstall_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.02.30.00
//
// Beschreibung: Deinstallation unter Linux (und macOS). Konfiguration und
//               Programmdatei lassen sich aus dem laufenden Prozess löschen.
//               Als Dienst zuletzt "systemctl stop --no-block": Ein Stopp, auf
//               den der Prozess selbst wartet, würde sich gegenseitig blockieren.
// ---------------------------------------------------------------------------

package core

import (
	"os"
	"os/exec"
	"strings"
)

// installedBinary ist der Ort, an den install-agent.sh das Programm legt.
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
	if fromService {
		if _, err := exec.LookPath("systemctl"); err == nil {
			_ = exec.Command("systemctl", "stop", "--no-block", "rimperio-agent").Run()
		}
	}
	return append(notes, "Agent entfernt.")
}
