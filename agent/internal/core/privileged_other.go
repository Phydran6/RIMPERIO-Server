//go:build !linux

// ---------------------------------------------------------------------------
// Datei:        privileged_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Gegenstück zu privileged_linux.go für Windows und macOS: Dort
//               läuft der Dienst wie bisher (Windows: SYSTEM), nichts zu tun.
// ---------------------------------------------------------------------------

package core

import (
	"os"
	"os/user"
)

// DropRoot gibt es nur unter Linux.
func DropRoot() {}

// ManualSetupAllowed: "setup"/"install" sind hier der normale Weg.
func ManualSetupAllowed() error { return nil }

// agentUser nennt das Konto für die Inventur.
func agentUser() string {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username
	}
	return os.Getenv("USERNAME")
}
