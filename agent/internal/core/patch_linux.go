//go:build linux

// ---------------------------------------------------------------------------
// Datei:        patch_linux.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Patchmanagement unter Linux über den vorhandenen Paketmanager
//               (apt / dnf / yum). Die Suche liefert je Paket installierte und
//               angebotene Version, das Archiv und ob es ein Sicherheitsupdate
//               ist (apt: *-security, dnf/yum: updateinfo). Eingespielt werden
//               nur die übergebenen Pakete — nie still „alles“.
//
//               Der Agent läuft als Dienstkonto: Lesen (Liste der Updates) geht
//               ohne Rechte, Auffrischen, Einspielen und Neustart laufen über
//               das Hilfsskript (asRoot, privileged_linux.go).
// ---------------------------------------------------------------------------

package core

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

const aptEnv = "DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a "
const aptOpts = "-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

// refreshLists frischt die Paketlisten auf (braucht root). Scheitert das, wird
// mit den vorhandenen Listen weitergesucht — apt-daily hält sie meist aktuell.
func refreshLists(direct string) {
	r := Execute(PendingCommand{Shell: "sh", Command: asRoot(direct, "pkg-refresh"), Timeout: 600})
	if hint := privilegeHint(r); hint != "" {
		logf("Paketlisten nicht aufgefrischt: %s", hint)
	}
}

// quoted setzt die (schon geprüften) Paketnamen einzeln in Anführungszeichen.
func quoted(ids []string) string {
	q := make([]string, 0, len(ids))
	for _, id := range ids {
		q = append(q, shellQuote(id))
	}
	return strings.Join(q, " ")
}

func patchScan() (PatchScanResult, error) {
	res := PatchScanResult{RebootPending: rebootPending()}
	switch {
	case hasCmd("apt-get"):
		res.Manager = "apt"
		refreshLists(aptEnv + "apt-get update -qq")
		r := Execute(PendingCommand{Shell: "sh", Command: "apt list --upgradable 2>/dev/null", Timeout: 180})
		res.Updates = parseApt(r.Stdout)
	case hasCmd("dnf"):
		res.Manager = "dnf"
		r := Execute(PendingCommand{Shell: "sh", Command: "dnf -q check-update", Timeout: 600})
		sec := Execute(PendingCommand{Shell: "sh", Command: "dnf -q updateinfo list --updates 2>/dev/null", Timeout: 300})
		res.Updates = parseDnf(r.Stdout, parseUpdateinfo(sec.Stdout))
	case hasCmd("yum"):
		res.Manager = "yum"
		r := Execute(PendingCommand{Shell: "sh", Command: "yum -q check-update", Timeout: 600})
		sec := Execute(PendingCommand{Shell: "sh", Command: "yum -q updateinfo list updates 2>/dev/null", Timeout: 300})
		res.Updates = parseDnf(r.Stdout, parseUpdateinfo(sec.Stdout))
	default:
		return res, fmt.Errorf("kein unterstützter Paketmanager (apt/dnf/yum) gefunden")
	}
	return res, nil
}

func patchApply(ids []string) (*PatchApplyResult, error) {
	res := &PatchApplyResult{}
	pkgs := quoted(ids)
	task := append([]string{"pkg-upgrade"}, ids...)
	var r CommandResult
	switch {
	case hasCmd("apt-get"):
		refreshLists(aptEnv + "apt-get update -qq")
		if len(ids) == 0 {
			r = Execute(PendingCommand{Shell: "sh", Command: asRoot(aptEnv+"apt-get -y "+aptOpts+" upgrade", task...), Timeout: 7200})
		} else {
			// install --only-upgrade zieht neue Abhängigkeiten nach (etwa ein
			// neues linux-image-*), fasst aber nichts an, was nicht genannt ist.
			r = Execute(PendingCommand{Shell: "sh", Command: asRoot(aptEnv+"apt-get -y "+aptOpts+" install --only-upgrade "+pkgs, task...), Timeout: 7200})
		}
		left := Execute(PendingCommand{Shell: "sh", Command: "apt list --upgradable 2>/dev/null", Timeout: 180})
		classify(res, ids, parseApt(left.Stdout))
	case hasCmd("dnf"), hasCmd("yum"):
		pm := "dnf"
		if !hasCmd("dnf") {
			pm = "yum"
		}
		r = Execute(PendingCommand{Shell: "sh", Command: asRoot(pm+" -y upgrade "+pkgs, task...), Timeout: 7200})
		left := Execute(PendingCommand{Shell: "sh", Command: pm + " -q check-update", Timeout: 600})
		classify(res, ids, parseDnf(left.Stdout, nil))
	default:
		return nil, fmt.Errorf("kein unterstützter Paketmanager")
	}
	res.Log = truncate(combine(r), 100_000)
	res.RebootRequired = rebootPending()
	for _, id := range res.Installed {
		if kernelPkg.MatchString(id) {
			res.RebootRequired = true
		}
	}
	res.Message = fmt.Sprintf("%d installiert, %d offen geblieben.", len(res.Installed), len(res.Failed))
	if err := exitErr(r); err != nil {
		if hint := privilegeHint(r); hint != "" {
			res.Message += " " + hint
			return res, fmt.Errorf("%s", hint)
		}
		if len(res.Failed) > 0 {
			res.Message += " " + describeFailures(res.Failed)
		}
		return res, err
	}
	return res, nil
}

// classify teilt die verlangten Pakete in installiert und offen geblieben —
// anhand dessen, was nach dem Lauf noch als aktualisierbar gemeldet wird.
func classify(res *PatchApplyResult, wanted []string, left []PatchUpdate) {
	still := map[string]bool{}
	for _, u := range left {
		still[u.ID] = true
		still[u.Package] = true
	}
	for _, id := range wanted {
		if still[id] {
			res.Failed = append(res.Failed, PatchFailure{ID: id, Error: "nach dem Lauf weiterhin offen"})
		} else {
			res.Installed = append(res.Installed, id)
		}
	}
	if len(wanted) == 0 {
		res.Installed = append(res.Installed, "alle")
	}
}

// rebootPending: Debian/Ubuntu legen eine Markerdatei ab, RHEL & Co. kennen
// needs-restarting -r (Exit 1 = Neustart nötig).
func rebootPending() bool {
	if _, err := os.Stat("/var/run/reboot-required"); err == nil {
		return true
	}
	if hasCmd("needs-restarting") {
		r := Execute(PendingCommand{Shell: "sh", Command: "needs-restarting -r >/dev/null 2>&1", Timeout: 60})
		return r.ExitCode != nil && *r.ExitCode == 1
	}
	return false
}

func scheduleReboot(delay int) error {
	minutes := (delay + 59) / 60
	if minutes < 1 {
		minutes = 1
	}
	r := Execute(PendingCommand{Shell: "sh",
		Command: asRoot(fmt.Sprintf("shutdown -r +%d 'RIMPERIO: Neustart nach Updates' 2>&1 || systemctl reboot", minutes),
			"reboot", strconv.Itoa(minutes)),
		Timeout: 60})
	if hint := privilegeHint(r); hint != "" {
		return fmt.Errorf("%s", hint)
	}
	return exitErr(r)
}

func combine(r CommandResult) string {
	return strings.TrimSpace(r.Stdout + "\n" + r.Stderr)
}

func exitErr(r CommandResult) error {
	if r.ExitCode != nil && *r.ExitCode != 0 {
		return fmt.Errorf("Befehl endete mit Exit %d", *r.ExitCode)
	}
	return nil
}
