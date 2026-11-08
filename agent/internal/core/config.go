// ---------------------------------------------------------------------------
// Datei:        config.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Konfiguration des Agents: wo der Server steht, das persönliche
//               Token, der Check-in-Takt. Liegt als JSON an einem festen,
//               geschützten Ort (Windows: ProgramData\RIMPERIO, Linux:
//               /opt/RMM/rimperio/etc — dort gehört sie dem Dienstkonto, unter
//               dem der Agent läuft). Ältere Linux-Installationen lagen unter
//               /etc/rimperio und liefen als root; die werden weiter gefunden.
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// Version des Agents — wandert in jeden Check-in und in die Inventur.
const Version = "2026.11.08.17.00.00"

// LinuxHome ist das Installationsverzeichnis unter Linux. Darunter liegt alles:
// bin/ (Programm), lib/ (Hilfsskript für Aufgaben, die root brauchen), etc/
// (Konfiguration) und var/ (Zustand). install-agent.sh legt es so an.
const LinuxHome = "/opt/RMM/rimperio"

// legacyConfigDir ist der Ort älterer Linux-Installationen (bis 2026.11.07).
const legacyConfigDir = "/etc/rimperio"

// HomeDir liefert das Installationsverzeichnis. RIMPERIO_HOME verschiebt es
// (für Tests); sonst gilt LinuxHome.
func HomeDir() string {
	if h := strings.TrimSpace(os.Getenv("RIMPERIO_HOME")); h != "" {
		return filepath.Clean(h)
	}
	return LinuxHome
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

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
	if runtime.GOOS != "linux" {
		return legacyConfigDir // macOS & Co.: unverändert
	}
	dir := filepath.Join(HomeDir(), "etc")
	// Eine ältere Installation (root, /etc/rimperio) bleibt lesbar, bis
	// install-agent.sh sie übernimmt. Das Dienstkonto selbst sieht dort nichts
	// (0700 root) und landet immer im neuen Verzeichnis.
	if !fileExists(filepath.Join(dir, "agent.json")) && fileExists(filepath.Join(legacyConfigDir, "agent.json")) {
		return legacyConfigDir
	}
	return dir
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
	if err := os.MkdirAll(ConfigDir(), 0o700); err != nil {
		return fmt.Errorf("Verzeichnis anlegen: %w", err)
	}
	// Erst die Rechte festziehen, dann das Geheimnis hineinschreiben. Unter
	// Windows wäre die Datei sonst für alle Benutzer lesbar (siehe
	// config_windows.go).
	if err := secureConfigDir(ConfigDir()); err != nil {
		return err
	}
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	// 0600: Das Token darf nur der Dienstbenutzer lesen (unter Linux das
	// Dienstkonto des Agents, unter Windows SYSTEM).
	if err := os.WriteFile(ConfigPath(), data, 0o600); err != nil {
		return fmt.Errorf("Konfiguration schreiben: %w", err)
	}
	return nil
}
