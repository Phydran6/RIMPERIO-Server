// ---------------------------------------------------------------------------
// Datei:        uninstall.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.02.30.00
//
// Beschreibung: Deinstallation — plattformunabhängiger Teil. Drei Wege, ein Ziel
//               (Agent vom Rechner, Eintrag vom Server):
//                 * Uninstall:      "rimperio-agent uninstall" (Weg ohne MSI)
//                 * Cleanup:        vom MSI beim Deinstallieren gerufen; Dienst
//                                   und Programmdatei entfernt das MSI selbst
//                 * selfUninstall:  vom Server angestoßen (Befehl "uninstall")
//               Mit keepRecord bleibt der Eintrag auf dem Server stehen.
//               Was nur unter Windows bzw. Linux geht: uninstall_*.go.
// ---------------------------------------------------------------------------

package core

import (
	"fmt"
	"os"
	"strings"
	"time"
)

// unregister meldet den Agent ab und versucht es bei Netzproblemen zweimal
// erneut. HTTP 401 heißt: Der Server kennt das Token nicht mehr — dann ist
// der Eintrag schon weg, und das ist auch ein Erfolg.
func unregister(client *Client) error {
	var err error
	for try := 0; try < 3; try++ {
		if try > 0 {
			time.Sleep(5 * time.Second)
		}
		if err = client.Unregister(); err == nil || strings.Contains(err.Error(), "HTTP 401") {
			return nil
		}
	}
	return err
}

// unregisterNotes meldet mit der gespeicherten Konfiguration ab und liefert,
// was der Benutzer dazu wissen sollte.
func unregisterNotes(cfg *Config) []string {
	if cfg == nil || cfg.AgentToken == "" {
		return []string{"Nicht angemeldet — auf dem Server ist nichts abzumelden."}
	}
	client := NewClient(cfg.ServerURL, cfg.AgentToken, cfg.Insecure)
	client.http.Timeout = 15 * time.Second
	if err := unregister(client); err != nil {
		return []string{fmt.Sprintf("Abmelden am Server fehlgeschlagen (%v) — "+
			"den Rechner bitte in der Oberfläche entfernen.", err)}
	}
	return []string{"Am Server abgemeldet."}
}

// RemoveConfig löscht die Konfiguration samt Token. Das Verzeichnis geht nur
// mit, wenn danach nichts anderes mehr darin liegt.
func RemoveConfig() error {
	if err := os.Remove(ConfigPath()); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("Konfiguration löschen: %w", err)
	}
	_ = os.Remove(ConfigDir()) // scheitert absichtlich, wenn nicht leer
	return nil
}

// Uninstall entfernt den Agent vom Rechner: abmelden (außer keepRecord),
// Dienst, Konfiguration und — wenn er am üblichen Ort liegt — das Programm.
func Uninstall(keepRecord bool) []string {
	var notes []string
	cfg, _ := Load()
	if !keepRecord {
		notes = append(notes, unregisterNotes(cfg)...)
	}
	return append(notes, platformUninstall(false)...)
}

// Cleanup ist der Teil, den das MSI beim Deinstallieren ruft: abmelden (außer
// keepRecord) und die Konfiguration löschen.
func Cleanup(keepRecord bool) []string {
	var notes []string
	cfg, _ := Load()
	if !keepRecord {
		notes = append(notes, unregisterNotes(cfg)...)
	}
	if err := RemoveConfig(); err != nil {
		notes = append(notes, err.Error())
	}
	return notes
}

// uninstallRequested führt den Befehl "uninstall" vom Server aus. Als Dienst
// räumt der Agent danach den Rechner auf und beendet sich; beim Testlauf
// ("once") meldet er nur ab und löscht seine Konfiguration.
func uninstallRequested(client *Client, asService bool) {
	logf("Deinstallation vom Server angefordert.")
	if err := unregister(client); err != nil {
		logf("Abmelden fehlgeschlagen: %v — der Eintrag bleibt auf dem Server.", err)
	}
	if !asService {
		if err := RemoveConfig(); err != nil {
			logf("%v", err)
		}
		logf("Abgemeldet, Konfiguration gelöscht. Dienst und Programm entfernt \"rimperio-agent uninstall -keep-record\".")
		return
	}
	for _, n := range platformUninstall(true) {
		logf("%s", n)
	}
}
