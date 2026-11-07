//go:build !windows && !linux

// ---------------------------------------------------------------------------
// Datei:        patch_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Patchmanagement auf sonstigen Plattformen (z. B. macOS) —
//               derzeit nicht umgesetzt.
// ---------------------------------------------------------------------------

package core

import "fmt"

func patchScan() (PatchScanResult, error) {
	return PatchScanResult{}, fmt.Errorf("Patchmanagement auf dieser Plattform nicht unterstützt")
}

func patchApply(ids []string) (*PatchApplyResult, error) {
	return nil, fmt.Errorf("Patchmanagement auf dieser Plattform nicht unterstützt")
}

func rebootPending() bool { return false }

func scheduleReboot(delay int) error {
	return fmt.Errorf("Neustart auf dieser Plattform nicht unterstützt")
}
