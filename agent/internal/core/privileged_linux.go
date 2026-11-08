//go:build linux

// ---------------------------------------------------------------------------
// Datei:        privileged_linux.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Der Agent läuft unter Linux als eigenes Dienstkonto, nicht als
//               root. Was trotzdem root braucht — Paketlisten auffrischen,
//               Updates einspielen, Neustart, Deinstallation —, geht über das
//               Hilfsskript lib/rimperio-helper, aufgerufen mit "sudo -n". Die
//               sudo-Regel (install-agent.sh, --sudo limited) erlaubt nur dieses
//               Skript, und das Skript prüft jede Angabe selbst (nur Paketnamen,
//               keine Dateien, Minuten als Zahl).
//
//               Läuft der Agent doch als root (ältere Installation, Testlauf mit
//               sudo ohne eingerichtetes Dienstkonto), geht alles direkt wie
//               bisher.
//
//               DropRoot sorgt dafür, dass "sudo rimperio-agent once" oder
//               "… enroll" nicht als root läuft: Ist ein Dienstkonto
//               eingerichtet, startet sich der Agent unter diesem Konto neu.
// ---------------------------------------------------------------------------

package core

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// HelperPath ist das Hilfsskript, das install-agent.sh root-eigen ablegt.
func HelperPath() string {
	return filepath.Join(HomeDir(), "lib", "rimperio-helper")
}

func isRoot() bool { return os.Geteuid() == 0 }

// shellQuote setzt einen Wert in einfache Anführungszeichen für "sh -c".
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// asRoot liefert den Shell-Befehl für eine Aufgabe, die root braucht: als root
// den direkten Befehl, sonst das Hilfsskript über sudo mit der Aufgabe task.
func asRoot(direct string, task ...string) string {
	if isRoot() {
		return direct
	}
	parts := []string{"sudo", "-n", shellQuote(HelperPath())}
	for _, t := range task {
		parts = append(parts, shellQuote(t))
	}
	return strings.Join(parts, " ")
}

// currentUser nennt das Konto, unter dem der Agent läuft.
func currentUser() string {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username
	}
	return strconv.Itoa(os.Geteuid())
}

// privilegeHint erklärt einen gescheiterten sudo-Aufruf in Klartext — oder
// liefert "", wenn der Fehler nichts mit fehlenden Rechten zu tun hat.
func privilegeHint(r CommandResult) string {
	if isRoot() || exitErr(r) == nil {
		return ""
	}
	for _, line := range strings.Split(r.Stderr+"\n"+r.Stdout, "\n") {
		l := strings.ToLower(strings.TrimSpace(line))
		if strings.HasPrefix(l, "sudo:") || strings.Contains(l, "sudo: not found") ||
			strings.Contains(l, "sudo: command not found") || strings.Contains(l, "is not allowed to execute") {
			return fmt.Sprintf("Der Agent läuft als Konto %q und darf das nicht: sudo-Regel fehlt oder sudo ist "+
				"nicht installiert (%s). Abhilfe: install-agent.sh mit --sudo limited erneut ausführen.",
				currentUser(), strings.TrimSpace(line))
		}
	}
	return ""
}

// serviceAccount liefert das Dienstkonto, dem etc/ gehört — oder nil, wenn
// keins eingerichtet ist (ältere Installation, Testlauf).
func serviceAccount() (*user.User, uint32, uint32, error) {
	st, err := os.Stat(filepath.Join(HomeDir(), "etc"))
	if err != nil {
		return nil, 0, 0, err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok || sys.Uid == 0 {
		return nil, 0, 0, errors.New("kein Dienstkonto eingerichtet")
	}
	u, err := user.LookupId(strconv.FormatUint(uint64(sys.Uid), 10))
	if err != nil {
		return nil, 0, 0, err
	}
	gid, err := strconv.ParseUint(u.Gid, 10, 32)
	if err != nil {
		return nil, 0, 0, err
	}
	return u, sys.Uid, uint32(gid), nil
}

// DropRoot startet den Agent unter dem Dienstkonto neu, wenn er als root
// gerufen wurde und ein Dienstkonto eingerichtet ist. Ohne Dienstkonto oder
// ohne root kehrt DropRoot einfach zurück. Sonst endet der Prozess mit dem
// Rückgabecode des neu gestarteten.
func DropRoot() {
	if !isRoot() {
		return
	}
	u, uid, gid, err := serviceAccount()
	if err != nil {
		return
	}
	groups := []uint32{}
	if ids, err := u.GroupIds(); err == nil {
		for _, id := range ids {
			if g, err := strconv.ParseUint(id, 10, 32); err == nil {
				groups = append(groups, uint32(g))
			}
		}
	}
	exe, err := os.Executable()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Programmpfad unbekannt: %v\n", err)
		os.Exit(1)
	}
	home := filepath.Join(HomeDir(), "var")
	cmd := exec.Command(exe, os.Args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Dir = home
	cmd.Env = append(os.Environ(), "HOME="+home, "USER="+u.Username, "LOGNAME="+u.Username)
	cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uid, Gid: gid, Groups: groups}}
	fmt.Fprintf(os.Stderr, "Läuft als Dienstkonto %q, nicht als root.\n", u.Username)
	if err := cmd.Run(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			os.Exit(ee.ExitCode())
		}
		fmt.Fprintf(os.Stderr, "Start als %q fehlgeschlagen: %v\n", u.Username, err)
		os.Exit(1)
	}
	os.Exit(0)
}

// ManualSetupAllowed: Unter Linux richtet install-agent.sh den Dienst ein —
// mit Dienstkonto, Verzeichnissen, sudo-Regel. "rimperio-agent setup" bzw.
// "install" würden einen Dienst anlegen, der als root läuft.
func ManualSetupAllowed() error {
	return fmt.Errorf("unter Linux richtet install-agent.sh den Dienst ein — mit eigenem Dienstkonto statt root "+
		"und allem unter %s:\n  curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/"+
		"agent/installer/linux/install-agent.sh | sudo bash -s -- --server URL --secret SCHLÜSSEL [--user rimperio]", LinuxHome)
}

// agentUser nennt das Konto für die Inventur.
func agentUser() string { return currentUser() }
