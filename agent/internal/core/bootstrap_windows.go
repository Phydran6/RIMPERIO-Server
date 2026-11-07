//go:build windows

// ---------------------------------------------------------------------------
// Datei:        bootstrap_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.02.30.00
//
// Beschreibung: Wenn der MSI-Installer die Eckdaten (Server, Geheimnis, Standort)
//               in der Registry hinterlegt hat, baut der Dienst daraus beim
//               ersten Start seine Konfiguration — ohne dass zur Installations-
//               zeit eine Verbindung nötig war. Nach der Anmeldung verschwindet
//               das Geheimnis aus der Registry (die darf jeder Benutzer lesen).
// ---------------------------------------------------------------------------

package core

import (
	"fmt"

	"golang.org/x/sys/windows/registry"
)

const registryKey = `SOFTWARE\RIMPERIO\Agent`

func bootstrapFromSystem() (*Config, error) {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, registryKey, registry.QUERY_VALUE)
	if err != nil {
		return nil, fmt.Errorf("keine Bootstrap-Daten in der Registry: %w", err)
	}
	defer k.Close()

	server, _, _ := k.GetStringValue("Server")
	secret, _, _ := k.GetStringValue("Secret")
	site, _, _ := k.GetStringValue("Site")
	insecureVal, _, _ := k.GetStringValue("Insecure")

	if server == "" || secret == "" {
		return nil, fmt.Errorf("Registry-Bootstrap unvollständig (Server/Secret fehlen)")
	}
	if site == "" {
		site = "Standard"
	}
	cfg := &Config{
		ServerURL:    server,
		EnrollSecret: secret,
		Site:         site,
		Interval:     60,
		Insecure:     insecureVal == "1" || insecureVal == "true",
	}
	if err := cfg.Save(); err != nil {
		return nil, err
	}
	return cfg, nil
}

// clearBootstrapSecret löscht das Geheimnis aus der Registry, sobald der Agent
// sein Token hat. Server, Standort und ProductCode bleiben stehen.
func clearBootstrapSecret() {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, registryKey, registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	if _, _, err := k.GetStringValue("Secret"); err == nil {
		_ = k.DeleteValue("Secret")
	}
}
