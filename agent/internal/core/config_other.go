//go:build !windows

// ---------------------------------------------------------------------------
// Datei:        config_other.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
//
// Beschreibung: Zugriffsrechte für /etc/rimperio. Die Datei selbst wird schon
//               mit 0600 geschrieben; hier wird das Verzeichnis auf 0700
//               gezogen, falls es vorher schon lockerer angelegt war.
// ---------------------------------------------------------------------------

package core

import "os"

func secureConfigDir(dir string) error {
	return os.Chmod(dir, 0o700)
}
