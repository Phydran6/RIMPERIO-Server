// ---------------------------------------------------------------------------
// Datei:        config.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.10.31.15.40.00
//
// Beschreibung: Konfiguration des Agents: wo der Server steht, das persönliche
//               Token, der Check-in-Takt. Liegt als JSON an einem festen,
//               geschützten Ort (Windows: ProgramData\RIMPERIO, Linux: /etc/rimperio).
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

// Version des Agents — wandert in jeden Check-in und in die Inventur.
const Version = "2026.11.07.02.30.00"

// Config ist der dauerhafte Zustand des Agents auf der Platte.
type Config struct {
	ServerURL   string `json:"server_url"`
	AgentUID    string `json:"agent_uid"`
	AgentToken  string `json:"agent_token"`
	Interval    int    `json:"interval"`
	Site        string `json:"site"`
	Description string `json:"description"`
	Insecure    bool   `json:"insecure"` // TLS-Prüfung aus (nur für Selbstzertifikate im Labor)

	// Nur bis zur ersten erfolgreichen Anmeldung gesetzt: Mit diesem Geheimnis
	// meldet sich der Agent beim ersten Dienststart selbst an (so braucht der
	// MSI-Installer kein Netz zur Installationszeit). Danach wird es gelöscht.
	EnrollSecret string `json:"enroll_secret,omitempty"`
}

// ConfigDir liefert das Verzeichnis, in dem die Konfiguration liegt.
func ConfigDir() string {
	if runtime.GOOS == "windows" {
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "RIMPERIO")
	}
	return "/etc/rimperio"
}

// ConfigPath ist der volle Pfad zur Konfigurationsdatei.
func ConfigPath() string {
	return filepath.Join(ConfigDir(), "agent.json")
}

// Load liest die Konfiguration von der Platte.
func Load() (*Config, error) {
	data, err := os.ReadFile(ConfigPath())
	if err != nil {
		return nil, fmt.Errorf("Konfiguration nicht lesbar (%s): %w", ConfigPath(), err)
	}
	var c Config
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, fmt.Errorf("Konfiguration unlesbar: %w", err)
	}
	if c.Interval <= 0 {
		c.Interval = 60
	}
	return &c, nil
}

// Save schreibt die Konfiguration an ihren Ort — nur für den Besitzer lesbar.
func (c *Config) Save() error {
	if err := os.MkdirAll(ConfigDir(), 0o750); err != nil {
		return fmt.Errorf("Verzeichnis anlegen: %w", err)
	}
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	// 0600: Das Token darf nur der Dienstbenutzer (root/SYSTEM) lesen.
	if err := os.WriteFile(ConfigPath(), data, 0o600); err != nil {
		return fmt.Errorf("Konfiguration schreiben: %w", err)
	}
	return nil
}
