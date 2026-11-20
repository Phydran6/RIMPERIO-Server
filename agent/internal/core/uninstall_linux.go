//go:build linux

// ---------------------------------------------------------------------------
// Datei:        uninstall_linux.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Deinstallation unter Linux.
//
//               Eingerichtet mit install-agent.sh (Dienstkonto, alles unter
//               /opt/RMM/rimperio): Das Hilfsskript lib/rimperio-helper räumt
//               alles ab — Dienst, Programm, Konfiguration, sudo-Regel und, wenn
//               sonst nichts von RIMPERIO mehr unter diesem Konto läuft, das
//               Konto und /opt/RMM/rimperio. Der Agent ruft es als Dienstkonto
//               über sudo, als root direkt. Kommt der Auftrag aus dem Dienst
//               selbst, übergibt das Skript an eine eigene kurzlebige
//               systemd-Unit; sonst beendete das Stoppen des Dienstes auch das
//               Skript.
//
//               Ältere Installation (root, /usr/local/bin, /etc/rimperio): wie
//               bisher — Konfiguration und Programmdatei aus dem laufenden
//               Prozess löschen, als Dienst zuletzt "systemctl stop --no-block".
// ---------------------------------------------------------------------------

package core

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// legacyBinary ist der Ort, an den ältere Fassungen von install-agent.sh das
// Programm legten.
const legacyBinary = "/usr/local/bin/rimperio-agent"

func platformUninstall(fromService bool) []string {
	if fileExists(HelperPath()) {
		return helperUninstall()
	}
	return legacyUninstall(fromService)
}

func helperUninstall() []string {
	cmd := shellQuote(HelperPath()) + " uninstall"
	if !isRoot() {
		cmd = "sudo -n " + cmd
	}
	r := Execute(PendingCommand{Shell: "sh", Command: cmd, Timeout: 180})
	var notes []string
	for _, line := range strings.Split(r.Stdout, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			notes = append(notes, line)
		}
	}
	if exitErr(r) != nil {
		if hint := privilegeHint(r); hint != "" {
			notes = append(notes, hint)
			// Ohne Rechte (--sudo none) bleibt der Dienst stehen - die nun
			// ungültige Anmeldung muss aber nicht liegen bleiben.
			if err := RemoveConfig(); err == nil {
				notes = append(notes, "Konfiguration gelöscht. Dienst und Programm entfernt: sudo "+
					filepath.Join(HomeDir(), "bin", "rimperio-agent")+" uninstall")
			}
		} else {
			notes = append(notes, "Deinstallation unvollständig: "+strings.TrimSpace(r.Stderr))
		}
	}
	return notes
}

func legacyUninstall(fromService bool) []string {
	var notes []string
	if !fromService {
		_ = ServiceControl("stop")
	}
	if err := RemoveConfig(); err != nil {
		notes = append(notes, err.Error())
	}
	if exe, err := os.Executable(); err == nil && exe == legacyBinary {
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
