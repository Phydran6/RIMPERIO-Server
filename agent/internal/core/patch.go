// ---------------------------------------------------------------------------
// Datei:        patch.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Patchmanagement — plattformunabhängiger Teil. Führt die
//               eingebauten Befehlsarten aus und ruft dafür die
//               betriebssystem-eigene Umsetzung (patch_windows.go /
//               patch_linux.go) auf:
//
//                 patch_scan          offene Updates suchen (mit Kategorie,
//                                     Sicherheitskennzeichen, Versionen)
//                 patch_apply         nur die freigegebenen Updates
//                                     installieren, danach je nach Vorgabe
//                                     neu starten
//                 docker_scan         Container und Image-Updates (docker.go)
//                 docker_update       Container mit neuem Image neu erzeugen
//                 health_snapshot     Dienste/Container/Neustart-Bedarf für
//                                     den Vorher-Nachher-Vergleich
//                 reboot              geplanter Neustart
//                 software_inventory  Inventur sofort senden
//
//               Der Auftrag steht als JSON im Feld "command". Ein älterer
//               Server schickt dort nur den Namen der Aktion — dann gilt das
//               alte Verhalten (alles installieren, kein Neustart).
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
)

// PatchUpdate beschreibt ein verfügbares Update.
type PatchUpdate struct {
	ID         string   `json:"id"`
	Title      string   `json:"title"`
	Severity   string   `json:"severity,omitempty"`
	KB         string   `json:"kb,omitempty"`
	Size       string   `json:"size,omitempty"`
	Category   string   `json:"category,omitempty"`  // Windows: Klassifizierung; Linux: security | updates
	Products   []string `json:"products,omitempty"`  // Windows: betroffene Produkte
	Package    string   `json:"package,omitempty"`   // Linux: Paketname ohne Architektur
	Current    string   `json:"current,omitempty"`   // installierte Version
	Available  string   `json:"available,omitempty"` // angebotene Version
	Source     string   `json:"source,omitempty"`    // Repository / Archiv
	Security   bool     `json:"security,omitempty"`  // Sicherheitsupdate
	Reboot     bool     `json:"reboot,omitempty"`    // verlangt voraussichtlich einen Neustart
	Released   string   `json:"released,omitempty"`  // Veröffentlichung (ISO), sofern bekannt
	CVEs       []string `json:"cves,omitempty"`
	Kind       string   `json:"kind,omitempty"` // software | driver | kernel
	URL        string   `json:"url,omitempty"`
	Downloaded bool     `json:"downloaded,omitempty"`
}

// PatchScanResult ist die Antwort auf patch_scan.
type PatchScanResult struct {
	Manager       string        `json:"manager"`
	Updates       []PatchUpdate `json:"updates"`
	RebootPending bool          `json:"reboot_pending"`
}

// PatchApplyRequest ist der Auftrag für patch_apply.
type PatchApplyRequest struct {
	IDs         []string `json:"ids"`          // leer = alle offenen (Altverhalten)
	Reboot      string   `json:"reboot"`       // never | if_needed | always
	RebootDelay int      `json:"reboot_delay"` // Sekunden bis zum Neustart (Standard 60)
}

// PatchFailure beschreibt ein Update, das nicht installiert wurde.
type PatchFailure struct {
	ID    string `json:"id"`
	Title string `json:"title,omitempty"`
	Error string `json:"error"`
}

// PatchApplyResult ist die Antwort auf patch_apply.
type PatchApplyResult struct {
	Installed       []string       `json:"installed"`
	Failed          []PatchFailure `json:"failed"`
	RebootRequired  bool           `json:"reboot_required"`
	RebootScheduled bool           `json:"reboot_scheduled"`
	Message         string         `json:"message"`
	Log             string         `json:"log,omitempty"`
}

func intPtr(i int) *int { return &i }

func hasCmd(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

// longRunning sind Aufträge, die Minuten bis Stunden dauern können. Sie laufen
// neben dem Check-in her, damit der Rechner währenddessen nicht als offline gilt.
func longRunning(kind string) bool {
	switch kind {
	case "patch_scan", "patch_apply", "docker_scan", "docker_update":
		return true
	}
	return false
}

func okJSON(id int, v any) CommandResult {
	b, err := json.Marshal(v)
	if err != nil {
		return CommandResult{ID: id, Status: "error", ExitCode: intPtr(1), Stderr: err.Error()}
	}
	return CommandResult{ID: id, Status: "done", ExitCode: intPtr(0), Stdout: string(b)}
}

func errResult(id int, err error) CommandResult {
	return CommandResult{ID: id, Status: "error", ExitCode: intPtr(1), Stderr: err.Error()}
}

// decodePayload liest den JSON-Auftrag. Steht dort kein JSON (alter Server),
// bleibt out unverändert und es gilt die Voreinstellung.
func decodePayload(raw string, out any) {
	raw = strings.TrimSpace(raw)
	if strings.HasPrefix(raw, "{") {
		_ = json.Unmarshal([]byte(raw), out)
	}
}

// HandleCommand führt einen Befehl aus — entweder eine eingebaute Aktion oder,
// bei kind=="shell", den gewöhnlichen Shell-Befehl.
func HandleCommand(pc PendingCommand, client *Client) CommandResult {
	switch pc.Kind {
	case "patch_scan":
		res, err := patchScan()
		if err != nil {
			return errResult(pc.ID, err)
		}
		if res.Updates == nil {
			res.Updates = []PatchUpdate{}
		}
		return okJSON(pc.ID, res)

	case "patch_apply":
		req := PatchApplyRequest{Reboot: "never"}
		decodePayload(pc.Command, &req)
		res, err := patchApply(sanitizeIDs(req.IDs))
		if err != nil && res == nil {
			return errResult(pc.ID, err)
		}
		if res == nil {
			res = &PatchApplyResult{}
		}
		if res.Installed == nil {
			res.Installed = []string{}
		}
		if res.Failed == nil {
			res.Failed = []PatchFailure{}
		}
		if !res.RebootRequired {
			res.RebootRequired = rebootPending()
		}
		if wantReboot(req.Reboot, res.RebootRequired) && (len(res.Installed) > 0 || req.Reboot == "always") {
			if rerr := scheduleReboot(req.RebootDelay); rerr != nil {
				res.Message = strings.TrimSpace(res.Message + " Neustart nicht geplant: " + rerr.Error())
			} else {
				res.RebootScheduled = true
			}
		}
		out := okJSON(pc.ID, res)
		if err != nil {
			out.Status, out.ExitCode, out.Stderr = "error", intPtr(1), err.Error()
		}
		return out

	case "docker_scan":
		res, err := dockerScan()
		if err != nil {
			return errResult(pc.ID, err)
		}
		return okJSON(pc.ID, res)

	case "docker_update":
		req := DockerUpdateRequest{Cleanup: true, HealthTimeout: 120, Rollback: true, PreferCompose: true}
		decodePayload(pc.Command, &req)
		res, err := dockerUpdate(req)
		if err != nil && res == nil {
			return errResult(pc.ID, err)
		}
		out := okJSON(pc.ID, res)
		if err != nil {
			out.Status, out.ExitCode, out.Stderr = "error", intPtr(1), err.Error()
		}
		return out

	case "health_snapshot":
		return okJSON(pc.ID, collectHealth())

	case "reboot":
		var req struct {
			Delay int `json:"delay"`
		}
		decodePayload(pc.Command, &req)
		if err := scheduleReboot(req.Delay); err != nil {
			return errResult(pc.ID, err)
		}
		return okJSON(pc.ID, map[string]any{"reboot_scheduled": true})

	case "software_inventory":
		if client != nil {
			if err := client.SendInventory(CollectInventory()); err != nil {
				return errResult(pc.ID, err)
			}
		}
		return CommandResult{ID: pc.ID, Status: "done", ExitCode: intPtr(0), Stdout: "Inventur gesendet."}

	default:
		return Execute(pc)
	}
}

func wantReboot(mode string, required bool) bool {
	switch mode {
	case "always":
		return true
	case "if_needed":
		return required
	default:
		return false
	}
}

// IDs kommen vom Server und landen in einer Shell bzw. einem Skript. Nur
// Zeichen, die in Update-Kennungen und Paketnamen vorkommen, kommen durch.
var safeID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:+~@-]{0,200}$`)

func sanitizeIDs(ids []string) []string {
	out := make([]string, 0, len(ids))
	for _, id := range ids {
		id = strings.TrimSpace(id)
		if safeID.MatchString(id) {
			out = append(out, id)
		}
	}
	return out
}

func describeFailures(f []PatchFailure) string {
	parts := make([]string, 0, len(f))
	for _, x := range f {
		parts = append(parts, fmt.Sprintf("%s: %s", x.ID, x.Error))
	}
	return strings.Join(parts, "; ")
}
