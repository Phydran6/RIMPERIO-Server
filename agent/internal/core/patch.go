// ---------------------------------------------------------------------------
// Datei:        patch.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Patchmanagement — plattformunabhängiger Teil. Führt die
//               besonderen Befehlsarten aus (patch_scan, patch_apply,
//               software_inventory) und ruft dafür die betriebssystem-eigene
//               Umsetzung (patch_windows.go / patch_linux.go) auf.
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"os/exec"
)

// PatchUpdate beschreibt ein verfügbares Update.
type PatchUpdate struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Severity string `json:"severity,omitempty"`
	KB       string `json:"kb,omitempty"`
	Size     string `json:"size,omitempty"`
}

func intPtr(i int) *int { return &i }

func hasCmd(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

// HandleCommand führt einen Befehl aus — entweder eine eingebaute Aktion oder,
// bei kind=="shell", den gewöhnlichen Shell-Befehl.
func HandleCommand(pc PendingCommand, client *Client) CommandResult {
	switch pc.Kind {
	case "patch_scan":
		ups, err := patchScan()
		if err != nil {
			return CommandResult{ID: pc.ID, Status: "error", ExitCode: intPtr(1), Stderr: err.Error()}
		}
		if ups == nil {
			ups = []PatchUpdate{}
		}
		b, _ := json.Marshal(ups)
		return CommandResult{ID: pc.ID, Status: "done", ExitCode: intPtr(0), Stdout: string(b)}

	case "patch_apply":
		out, err := patchApply()
		if err != nil {
			return CommandResult{ID: pc.ID, Status: "error", ExitCode: intPtr(1), Stdout: out, Stderr: err.Error()}
		}
		return CommandResult{ID: pc.ID, Status: "done", ExitCode: intPtr(0), Stdout: out}

	case "software_inventory":
		if client != nil {
			if err := client.SendInventory(CollectInventory()); err != nil {
				return CommandResult{ID: pc.ID, Status: "error", ExitCode: intPtr(1), Stderr: err.Error()}
			}
		}
		return CommandResult{ID: pc.ID, Status: "done", ExitCode: intPtr(0), Stdout: "Inventur gesendet."}

	default:
		return Execute(pc)
	}
}
