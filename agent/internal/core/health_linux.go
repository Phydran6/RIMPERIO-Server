//go:build linux

// ---------------------------------------------------------------------------
// Datei:        health_linux.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Dienstzustand unter Linux über systemd (laufende und
//               fehlgeschlagene Service-Units). Ohne systemd bleibt die Liste
//               leer — der Vergleich stützt sich dann auf Checks und Container.
// ---------------------------------------------------------------------------

package core

import "strings"

func serviceState() (running, autoStopped, failed []string) {
	if !hasCmd("systemctl") {
		return
	}
	r := Execute(PendingCommand{Shell: "sh",
		Command: "systemctl list-units --type=service --state=running --no-legend --plain --no-pager", Timeout: 60})
	running = firstColumn(r.Stdout)
	f := Execute(PendingCommand{Shell: "sh",
		Command: "systemctl list-units --type=service --state=failed --no-legend --plain --no-pager", Timeout: 60})
	failed = firstColumn(f.Stdout)
	return
}

func firstColumn(out string) []string {
	res := []string{}
	for _, line := range strings.Split(out, "\n") {
		f := strings.Fields(strings.TrimSpace(line))
		if len(f) > 0 && strings.HasSuffix(f[0], ".service") {
			res = append(res, f[0])
		}
	}
	return res
}
