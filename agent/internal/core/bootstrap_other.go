//go:build !windows

// ---------------------------------------------------------------------------
// Datei:        bootstrap_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.10.31.15.40.00
//
// Beschreibung: Nicht-Windows — keinen Registry-Bootstrap. Auf Linux übernimmt
//               das Installationsskript (setup) das Anlegen der Konfiguration.
// ---------------------------------------------------------------------------

package core

import "fmt"

func bootstrapFromSystem() (*Config, error) {
	return nil, fmt.Errorf("kein System-Bootstrap auf dieser Plattform")
}
