//go:build !linux

// ---------------------------------------------------------------------------
// Datei:        hardware_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.26.22.00.00
//
// Beschreibung: Ohne Linux keine Einplatinen-Angaben.
// ---------------------------------------------------------------------------

package core

func collectHardware() *HardwareInfo { return nil }
