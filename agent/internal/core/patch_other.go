//go:build !windows && !linux

// ---------------------------------------------------------------------------
// Datei:        patch_other.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Patchmanagement auf sonstigen Plattformen (z. B. macOS) —
//               derzeit nicht umgesetzt.
// ---------------------------------------------------------------------------

package core

import "fmt"

func patchScan() ([]PatchUpdate, error) {
	return nil, fmt.Errorf("Patchmanagement auf dieser Plattform nicht unterstützt")
}

func patchApply() (string, error) {
	return "", fmt.Errorf("Patchmanagement auf dieser Plattform nicht unterstützt")
}
