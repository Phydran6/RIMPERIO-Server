//go:build linux

// ---------------------------------------------------------------------------
// Datei:        software_linux.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Installierte Software unter Linux — über dpkg bzw. rpm.
// ---------------------------------------------------------------------------

package core

import "strings"

func collectSoftware() []SoftwareItem {
	out := []SoftwareItem{}
	var raw string
	switch {
	case hasCmd("dpkg-query"):
		r := Execute(PendingCommand{Shell: "sh", Command: `dpkg-query -W -f='${Package}\t${Version}\n'`, Timeout: 120})
		raw = r.Stdout
	case hasCmd("rpm"):
		r := Execute(PendingCommand{Shell: "sh", Command: `rpm -qa --qf '%{NAME}\t%{VERSION}\n'`, Timeout: 120})
		raw = r.Stdout
	default:
		return out
	}
	for _, line := range strings.Split(raw, "\n") {
		parts := strings.SplitN(strings.TrimSpace(line), "\t", 2)
		if parts[0] == "" {
			continue
		}
		item := SoftwareItem{Name: parts[0]}
		if len(parts) > 1 {
			item.Version = parts[1]
		}
		out = append(out, item)
	}
	return out
}
