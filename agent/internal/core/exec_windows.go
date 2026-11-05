//go:build windows

// ---------------------------------------------------------------------------
// Datei:        exec_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.10.31.15.40.00
//
// Beschreibung: Windows-spezifisch — Befehle laufen ohne sichtbares Fenster.
// ---------------------------------------------------------------------------

package core

import (
	"os/exec"
	"syscall"
)

const createNoWindow = 0x08000000 // CREATE_NO_WINDOW

func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow,
	}
}
