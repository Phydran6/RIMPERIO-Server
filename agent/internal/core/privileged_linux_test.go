//go:build linux

// ---------------------------------------------------------------------------
// Datei:        privileged_linux_test.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Tests für den Weg ohne root: Aufruf des Hilfsskripts über sudo,
//               Erkennen fehlender Rechte, Ablage der Konfiguration unter dem
//               Installationsverzeichnis.
// ---------------------------------------------------------------------------

package core

import (
	"os"
	"path/filepath"
	"testing"
)

func TestAsRootUsesHelperViaSudo(t *testing.T) {
	if isRoot() {
		t.Skip("läuft als root — dort geht alles direkt")
	}
	t.Setenv("RIMPERIO_HOME", "/opt/RMM/rimperio")
	got := asRoot("apt-get update -qq", "pkg-upgrade", "openssl", "it's")
	want := `sudo -n '/opt/RMM/rimperio/lib/rimperio-helper' 'pkg-upgrade' 'openssl' 'it'\''s'`
	if got != want {
		t.Errorf("asRoot:\n bekommen %s\n erwartet %s", got, want)
	}
}

func TestPrivilegeHint(t *testing.T) {
	if isRoot() {
		t.Skip("als root gibt es keinen Rechtehinweis")
	}
	cases := []struct {
		name string
		r    CommandResult
		hint bool
	}{
		{"Passwort verlangt", CommandResult{ExitCode: intPtr(1), Stderr: "sudo: a password is required\n"}, true},
		{"sudo fehlt", CommandResult{ExitCode: intPtr(127), Stderr: "sh: 1: sudo: not found\n"}, true},
		{"nicht erlaubt", CommandResult{ExitCode: intPtr(1), Stderr: "Sorry, user rimperio is not allowed to execute '/usr/bin/id' as root on host.\n"}, true},
		{"apt-Fehler beim Paket sudo", CommandResult{ExitCode: intPtr(100),
			Stdout: "Unpacking sudo (1.9.13p3-1+deb12u1) ...\nE: Sub-process /usr/bin/dpkg returned an error code (1)\n"}, false},
		{"Erfolg", CommandResult{ExitCode: intPtr(0), Stderr: "sudo: kein Problem\n"}, false},
	}
	for _, c := range cases {
		if got := privilegeHint(c.r) != ""; got != c.hint {
			t.Errorf("%s: Hinweis %v, erwartet %v", c.name, got, c.hint)
		}
	}
}

func TestConfigLivesUnderHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("RIMPERIO_HOME", home)
	if fileExists(filepath.Join(legacyConfigDir, "agent.json")) {
		t.Skip("auf diesem Rechner liegt eine ältere Installation unter /etc/rimperio")
	}
	if got, want := ConfigDir(), filepath.Join(home, "etc"); got != want {
		t.Fatalf("ConfigDir: %s, erwartet %s", got, want)
	}
	cfg := &Config{ServerURL: "https://rmm.example.de", AgentToken: "geheim", Interval: 60}
	if err := cfg.Save(); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(ConfigPath())
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Errorf("agent.json hat %o, erwartet 600", st.Mode().Perm())
	}
	dir, _ := os.Stat(ConfigDir())
	if dir.Mode().Perm() != 0o700 {
		t.Errorf("etc/ hat %o, erwartet 700", dir.Mode().Perm())
	}
	back, err := Load()
	if err != nil || back.AgentToken != "geheim" {
		t.Fatalf("Load: %v %+v", err, back)
	}
}
