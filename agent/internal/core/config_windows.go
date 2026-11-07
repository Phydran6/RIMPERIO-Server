//go:build windows

// ---------------------------------------------------------------------------
// Datei:        config_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
//
// Beschreibung: Zugriffsrechte für C:\ProgramData\RIMPERIO.
//
// Warum: Unter Windows ignoriert os.WriteFile die Unix-Rechte 0600. Das
// Verzeichnis erbt die Rechte von ProgramData, und dort darf die Gruppe
// "Benutzer" lesen. Damit konnte jeder angemeldete Benutzer agent.json öffnen
// und den Agent-Token lesen, und während der Installation sogar den
// Zugangsschlüssel. Mit dem Token kann man sich gegenüber dem Server als dieser
// Rechner ausgeben.
//
// Jetzt: Vererbung aus, Vollzugriff nur für SYSTEM und Administratoren. Die
// SIDs statt der Gruppennamen, weil die Namen auf einem deutschen Windows
// anders heißen ("Administratoren").
// ---------------------------------------------------------------------------

package core

import (
	"fmt"
	"os/exec"
	"syscall"
)

func secureConfigDir(dir string) error {
	cmd := exec.Command("icacls", dir,
		"/inheritance:r",
		"/grant:r", "*S-1-5-18:(OI)(CI)F", // SYSTEM
		"/grant:r", "*S-1-5-32-544:(OI)(CI)F", // Administratoren
		"/T", "/C", "/Q")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("Rechte für %s setzen: %v (%s)", dir, err, out)
	}
	return nil
}
