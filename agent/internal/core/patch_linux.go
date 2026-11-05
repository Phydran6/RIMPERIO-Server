//go:build linux

// ---------------------------------------------------------------------------
// Datei:        patch_linux.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Patchmanagement unter Linux über den vorhandenen Paketmanager
//               (apt / dnf / yum). Suchen listet verfügbare Pakete; Einspielen
//               führt das Upgrade nicht-interaktiv aus.
// ---------------------------------------------------------------------------

package core

import (
	"fmt"
	"strings"
)

func patchScan() ([]PatchUpdate, error) {
	switch {
	case hasCmd("apt-get"):
		Execute(PendingCommand{Shell: "sh", Command: "apt-get update -qq", Timeout: 300})
		r := Execute(PendingCommand{Shell: "sh", Command: "apt list --upgradable 2>/dev/null", Timeout: 120})
		return parseApt(r.Stdout), nil
	case hasCmd("dnf"):
		r := Execute(PendingCommand{Shell: "sh", Command: "dnf -q check-update", Timeout: 300})
		return parseDnf(r.Stdout), nil
	case hasCmd("yum"):
		r := Execute(PendingCommand{Shell: "sh", Command: "yum -q check-update", Timeout: 300})
		return parseDnf(r.Stdout), nil
	default:
		return nil, fmt.Errorf("kein unterstützter Paketmanager (apt/dnf/yum) gefunden")
	}
}

func patchApply() (string, error) {
	switch {
	case hasCmd("apt-get"):
		r := Execute(PendingCommand{Shell: "sh", Command: "DEBIAN_FRONTEND=noninteractive apt-get -y upgrade", Timeout: 7200})
		return combine(r), exitErr(r)
	case hasCmd("dnf"):
		r := Execute(PendingCommand{Shell: "sh", Command: "dnf -y upgrade", Timeout: 7200})
		return combine(r), exitErr(r)
	case hasCmd("yum"):
		r := Execute(PendingCommand{Shell: "sh", Command: "yum -y update", Timeout: 7200})
		return combine(r), exitErr(r)
	default:
		return "", fmt.Errorf("kein unterstützter Paketmanager")
	}
}

func parseApt(out string) []PatchUpdate {
	ups := []PatchUpdate{}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "Listing") || !strings.Contains(line, "/") {
			continue
		}
		fields := strings.Fields(line)
		name := strings.SplitN(fields[0], "/", 2)[0]
		ver := ""
		if len(fields) >= 2 {
			ver = fields[1]
		}
		ups = append(ups, PatchUpdate{ID: name, Title: strings.TrimSpace(name + " " + ver), Size: ver})
	}
	return ups
}

func parseDnf(out string) []PatchUpdate {
	ups := []PatchUpdate{}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		low := strings.ToLower(line)
		if line == "" || strings.HasPrefix(low, "last metadata") || strings.HasPrefix(low, "obsoleting") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		ups = append(ups, PatchUpdate{ID: fields[0], Title: fields[0] + " " + fields[1], Size: fields[1]})
	}
	return ups
}

func combine(r CommandResult) string {
	return strings.TrimSpace(r.Stdout + "\n" + r.Stderr)
}

func exitErr(r CommandResult) error {
	if r.ExitCode != nil && *r.ExitCode != 0 {
		return fmt.Errorf("Paketmanager endete mit Exit %d", *r.ExitCode)
	}
	return nil
}
