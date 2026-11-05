//go:build !windows && !linux

// ---------------------------------------------------------------------------
// Datei:        software_other.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Software-Inventar auf sonstigen Plattformen — leer.
// ---------------------------------------------------------------------------

package core

func collectSoftware() []SoftwareItem {
	return []SoftwareItem{}
}
