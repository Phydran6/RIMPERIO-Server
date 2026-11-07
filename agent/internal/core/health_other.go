//go:build !windows && !linux

// ---------------------------------------------------------------------------
// Datei:        health_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Dienstzustand auf sonstigen Plattformen — nicht erhoben.
// ---------------------------------------------------------------------------

package core

func serviceState() (running, autoStopped, failed []string) {
	return []string{}, []string{}, []string{}
}
