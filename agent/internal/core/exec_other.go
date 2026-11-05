//go:build !windows

// ---------------------------------------------------------------------------
// Datei:        exec_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.10.31.15.40.00
//
// Beschreibung: Nicht-Windows — kein Fenster zu verstecken, also Leerfunktion.
// ---------------------------------------------------------------------------

package core

import "os/exec"

func hideWindow(cmd *exec.Cmd) {}
